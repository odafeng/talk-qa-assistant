"""Local web app: Meet tab + mic audio -> Gemini Live transcript -> Claude spoken answers, in the browser.

Run from this folder:  uv run uvicorn app:app --port 8777
"""

import asyncio
import contextlib
import json
import logging
import re
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
from answerer import Answer, Answerer
from segmenter import Segmenter
from transcriber import Transcriber

HERE = Path(__file__).resolve().parent
SESSIONS = HERE / "sessions"
REWARM_SECONDS = 50 * 60  # under the 1-hour cache TTL
PORT = 8777
# Only this page may open the socket; any other site could otherwise read the transcript or spend API credit.
ALLOWED_ORIGINS = {f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"}
AUDIENCE, ME = "聽眾", "我"
CHANNELS = {0: AUDIENCE, 1: ME}  # first byte of each binary audio frame
WINDOW_LINES = 40  # transcript lines sent with each request
WAIT_FORCE_SECONDS = 5.0  # after a WAIT, answer anyway once the audience has been quiet this long
FORCE_NOTE = "對方已經停下來了，請直接處理，不要再輸出 WAIT。"
NOW_NOTE = "講者按了「現在回答」：請回答最近一個聽眾問題，不要輸出 SKIP 或 WAIT。"

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


@app.get("/download/{name}")
async def download(name: str) -> FileResponse:
    if not re.fullmatch(r"\d{8}_\d{6}_(transcript\.txt|qa\.jsonl)", name) or not (SESSIONS / name).exists():
        raise HTTPException(404)
    return FileResponse(SESSIONS / name, filename=name)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    if ws.headers.get("origin") not in ALLOWED_ORIGINS:
        await ws.close(code=1008)
        return
    await ws.accept()
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    transcript_path, qa_path = SESSIONS / f"{stamp}_transcript.txt", SESSIONS / f"{stamp}_qa.jsonl"
    started = time.monotonic()
    send_lock, evaluating = asyncio.Lock(), asyncio.Lock()
    lines: list[str] = []
    answered: list[str] = []
    queues: dict[int, asyncio.Queue] = {}
    tasks: set[asyncio.Task] = set()
    flags = {"auto": True, "rerun": "", "waiting": False, "next_id": 1}
    # The evaluation in flight; it can be restarted until its answer starts showing on screen.
    current: dict = {"task": None, "note": "", "visible": False}
    segmenter = Segmenter()

    async def send(event: dict) -> None:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            async with send_lock:
                await ws.send_text(json.dumps(event, ensure_ascii=False))

    def spawn(coro) -> None:
        task = asyncio.create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def run_once(note: str) -> None:
        answerer: Answerer | None = state["answerer"]
        if answerer is None:
            await send(
                {"type": "error", "text": "沒有 Claude API key：請填 tools/qa_assistant/.env 後重開。"}
            )
            return
        if not lines and not note:
            return
        aid = flags["next_id"]
        flags["next_id"] += 1
        await send({"type": "answer_start", "id": aid})
        state["last_call"] = time.monotonic()
        shown = ""
        try:
            async for piece in answerer.stream(lines[-WINDOW_LINES:], answered, note):
                if not isinstance(piece, Answer):
                    shown += piece
                    current["visible"] = shown.lstrip().startswith("【")
                    await send({"type": "answer_delta", "id": aid, "text": piece})
                    continue
                if piece.status == "answer":
                    answered.append(piece.question or "（未擷取到問題）")
                flags["waiting"] = piece.status == "wait"
                with qa_path.open("a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {
                                "t": datetime.now().isoformat(timespec="seconds"),
                                "note": note,
                                **asdict(piece),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                await send({"type": "answer_done", "id": aid, **asdict(piece)})
        except anthropic.APIError as e:
            await send({"type": "answer_error", "id": aid, "text": f"{type(e).__name__}: {e}"})

    async def evaluate(note: str = "") -> None:
        """One request at a time, so a question is never answered twice in parallel."""
        if evaluating.locked():
            flags["rerun"] = note or flags["rerun"] or " "
            return
        async with evaluating:
            await send({"type": "thinking", "on": True})
            while True:
                current.update(note=note.strip(), visible=False)
                current["task"] = task = asyncio.create_task(run_once(note.strip()))
                try:
                    await task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                note, flags["rerun"] = flags["rerun"], ""
                if not note:
                    break
            current["task"] = None
            await send({"type": "thinking", "on": False})

    def audience_still_talking() -> None:
        """New audience speech makes an unseen verdict stale: drop it and judge again at the next pause.

        A question asked in fragments then never gets answered from its first half. A forced
        request (typed question, 現在回答) is re-run at once instead of waiting for a pause.
        """
        task = current["task"]
        if task is not None and not task.done() and not current["visible"]:
            flags["rerun"] = current["note"] or flags["rerun"]
            flags["waiting"] = False
            task.cancel()

    async def on_audience_pause() -> None:
        if flags["auto"]:
            await evaluate()

    async def add_line(speaker: str, text: str) -> None:
        mins, secs = divmod(int(time.monotonic() - started), 60)
        line = f"[{mins:02d}:{secs:02d}] {speaker}：{text}"
        lines.append(line)
        with transcript_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        await send({"type": "final", "speaker": speaker, "line": line})
        if speaker == AUDIENCE:
            # Gemini only finalizes a line at the end of an utterance, so judge it now rather than
            # after a further pause; speech that follows cancels the judgment (audience_still_talking).
            segmenter.add_final(text, time.monotonic())
            spawn(on_audience_pause())

    async def ticker() -> None:
        while True:
            await asyncio.sleep(0.5)
            now = time.monotonic()
            segmenter.tick(now)  # only clears the finished utterance; it was judged on its final line
            if flags["waiting"] and now - segmenter.last_activity >= WAIT_FORCE_SECONDS:
                flags["waiting"] = False
                spawn(evaluate(FORCE_NOTE))

    async def start_channel(channel: int) -> None:
        if channel in queues or channel not in CHANNELS:
            return
        if not (key := config.gemini_key()):
            await send({"type": "error", "text": "找不到 GEMINI_API_KEY。"})
            return
        speaker = CHANNELS[channel]
        queues[channel] = asyncio.Queue(maxsize=500)

        async def send_tagged(event: dict) -> None:
            await send({**event, "speaker": speaker})

        def on_interim() -> None:
            if speaker == AUDIENCE:
                segmenter.add_interim(time.monotonic())
                audience_still_talking()

        async def on_final(text: str) -> None:
            await add_line(speaker, text)

        spawn(Transcriber(key, send_tagged, on_interim, on_final).run(queues[channel]))

    async def stop_all() -> None:
        for channel in list(queues):
            await queues.pop(channel).put(None)

    spawn(ticker())
    await send(
        {
            "type": "hello",
            "anthropic_key": bool(state["answerer"]),
            "gemini_key": bool(config.gemini_key()),
            "cache_tokens": state["warm_tokens"],
            "warm_error": state["warm_error"],
            "transcript": transcript_path.name,
            "qa": qa_path.name,
        }
    )
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (frame := message.get("bytes")) is not None:
                if frame and (queue := queues.get(frame[0])) is not None and not queue.full():
                    queue.put_nowait(frame[1:])
                continue
            event = json.loads(message.get("text") or "{}")
            kind = event.get("type")
            if kind == "start":
                await start_channel(int(event.get("channel", 0)))
            elif kind == "stop":
                await stop_all()
                await send({"type": "status", "text": "已停止聆聽"})
            elif kind == "auto":
                flags["auto"] = bool(event.get("on"))
            elif kind == "answer_now":
                spawn(evaluate(NOW_NOTE))
            elif kind == "ask" and (text := str(event.get("text", "")).strip()):
                await add_line("我（手動輸入的問題）", text)
                spawn(evaluate(f"講者手動輸入了這個問題，請直接回答，不要輸出 SKIP 或 WAIT：{text}"))
    finally:
        await stop_all()
        for task in list(tasks):
            task.cancel()
