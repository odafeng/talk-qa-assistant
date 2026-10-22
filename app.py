"""Local web app: Meet tab audio -> Gemini Live transcript -> Claude answers, shown in the browser.

Run from this folder:  uv run uvicorn app:app --port 8777
"""

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import anthropic
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
from answerer import Answer, Answerer
from segmenter import Segmenter
from transcriber import Transcriber

HERE = Path(__file__).resolve().parent
SESSIONS = HERE / "sessions"
REWARM_SECONDS = 50 * 60  # under the 1-hour cache TTL

log = logging.getLogger("qa")
state: dict = {"answerer": None, "warm_tokens": 0, "last_call": 0.0, "warm_error": ""}


async def _warm() -> None:
    try:
        state["warm_tokens"] = await state["answerer"].prewarm()
        state["warm_error"] = ""
    except anthropic.APIError as e:
        state["warm_error"] = f"{type(e).__name__}: {e}"
    state["last_call"] = time.monotonic()


async def _keep_warm() -> None:
    while True:
        await asyncio.sleep(60)
        if state["answerer"] and time.monotonic() - state["last_call"] > REWARM_SECONDS:
            await _warm()


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    SESSIONS.mkdir(exist_ok=True)
    background = []
    if key := config.anthropic_key():
        state["answerer"] = Answerer(key)
        background = [asyncio.create_task(_warm()), asyncio.create_task(_keep_warm())]
    yield
    for task in background:
        task.cancel()


app = FastAPI(title="SurgiFlowVid Q&A assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(HERE / "static/index.html")


@app.get("/health")
async def health() -> dict:
    return {
        "anthropic_key": bool(config.anthropic_key()),
        "gemini_key": bool(config.gemini_key()),
        "cache_tokens": state["warm_tokens"],
        "warm_error": state["warm_error"],
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    log_path = SESSIONS / f"{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    send_lock = asyncio.Lock()
    history: list[tuple[str, str]] = []
    segments: dict[int, str] = {}
    tasks: set[asyncio.Task] = set()
    session = {"audio": None, "auto": True, "next_id": 1}
    segmenter = Segmenter()

    async def send(event: dict) -> None:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            async with send_lock:
                await ws.send_text(json.dumps(event, ensure_ascii=False))

    def record(event: dict) -> None:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"t": datetime.now().isoformat(timespec="seconds"), **event}, ensure_ascii=False)
                + "\n"
            )

    def spawn(coro) -> None:
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def answer(question: str, source: str) -> None:
        answerer: Answerer | None = state["answerer"]
        aid = session["next_id"]
        session["next_id"] += 1
        if answerer is None:
            await send(
                {"type": "error", "text": "沒有 Claude API key：請填 tools/qa_assistant/.env 後重開。"}
            )
            return
        await send({"type": "answer_start", "id": aid, "question": question, "source": source})
        state["last_call"] = time.monotonic()
        try:
            async for piece in answerer.stream(question, history):
                if isinstance(piece, Answer):
                    if not piece.skipped and not piece.refused:
                        history.append((question, piece.text))
                    record({"question": question, "source": source, **asdict(piece)})
                    await send({"type": "answer_done", "id": aid, **asdict(piece)})
                else:
                    await send({"type": "answer_delta", "id": aid, "text": piece})
        except anthropic.APIError as e:
            await send({"type": "answer_error", "id": aid, "text": f"{type(e).__name__}: {e}"})

    async def emit_segment(text: str) -> None:
        sid = len(segments) + 1
        segments[sid] = text
        record({"segment": text})
        await send({"type": "segment", "id": sid, "text": text})
        if session["auto"]:
            spawn(answer(text, "auto"))

    async def on_final(text: str) -> None:
        await send({"type": "final", "text": text})
        if done := segmenter.add_final(text, time.monotonic()):
            await emit_segment(done)

    async def ticker() -> None:
        while True:
            await asyncio.sleep(0.5)
            if done := segmenter.tick(time.monotonic()):
                await emit_segment(done)

    async def start_listening() -> None:
        if session["audio"] is not None:
            return
        if not (key := config.gemini_key()):
            await send({"type": "error", "text": "找不到 GEMINI_API_KEY。"})
            return
        session["audio"] = asyncio.Queue(maxsize=500)
        transcriber = Transcriber(key, send, lambda: segmenter.add_interim(time.monotonic()), on_final)
        spawn(transcriber.run(session["audio"]))

    async def stop_listening() -> None:
        if (queue := session["audio"]) is not None:
            session["audio"] = None
            await queue.put(None)
            await send({"type": "status", "text": "已停止聆聽"})

    spawn(ticker())
    await send(
        {
            "type": "hello",
            "anthropic_key": bool(state["answerer"]),
            "gemini_key": bool(config.gemini_key()),
            "cache_tokens": state["warm_tokens"],
            "warm_error": state["warm_error"],
        }
    )
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (chunk := message.get("bytes")) is not None:
                if (queue := session["audio"]) is not None and not queue.full():
                    queue.put_nowait(chunk)
                continue
            event = json.loads(message.get("text") or "{}")
            kind = event.get("type")
            if kind == "start":
                await start_listening()
            elif kind == "stop":
                await stop_listening()
            elif kind == "auto":
                session["auto"] = bool(event.get("on"))
            elif kind == "ask" and (text := str(event.get("text", "")).strip()):
                spawn(answer(text, "manual"))
            elif kind == "answer_segment" and (text := segments.get(int(event.get("id", 0)))):
                spawn(answer(text, "segment"))
    finally:
        await stop_listening()
        for task in list(tasks):
            task.cancel()
