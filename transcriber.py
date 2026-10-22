"""Stream 16 kHz PCM audio to Gemini Live and report interim and final transcripts.

Ported from ~/Desktop/Projects/lecture_live_notes/app.py (gemini_asr_worker), minus file saving.
"""

import asyncio
import base64
import json
import re
import time
from collections.abc import Awaitable, Callable

import websockets
from opencc import OpenCC

MODEL = "gemini-3.5-transcribe-live"
WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
# Live sessions are capped at 10 minutes, so rotate a little before that.
ROTATE_SECONDS = 570
# Pings queue behind audio frames; a short deadline closed healthy sessions in lecture_live_notes.
PING_INTERVAL, PING_TIMEOUT = 30, 60
MAX_FAILURES = 5
VOCABULARY = [
    "SurgiFlowVid",
    "SurV-Gen",
    "SparseCtrl",
    "Endora",
    "SurgSora",
    "Bora",
    "SAR-RARP50",
    "GraSP",
    "AutoLaparo",
    "GynSurg",
    "optical flow",
    "RAFT",
    "UniMatch",
    "diffusion",
    "U-Net",
    "latent",
    "Stable Diffusion",
    "dual-prediction",
    "sparse visual encoder",
    "SVE",
    "Jaccard",
    "mAP",
    "Dice",
    "F1",
    "MViT",
    "TAPIS",
    "X3D",
    "rejection sampling",
    "under-represented",
    "data imbalance",
    "lambda p",
    "segmentation mask",
    "temporal attention",
    "ICML",
]

# s2twp also maps mainland wording (視頻 -> 影片), which Gemini sometimes emits.
TRADITIONAL = OpenCC("s2twp")
Send = Callable[[dict], Awaitable[None]]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", TRADITIONAL.convert(text)).strip()


class Transcriber:
    def __init__(
        self,
        api_key: str,
        send: Send,
        on_interim: Callable[[], None],
        on_final: Callable[[str], Awaitable[None]],
    ) -> None:
        self.api_key, self.send = api_key, send
        self.on_interim, self.on_final = on_interim, on_final

    async def _receive(self, ws) -> None:
        async for raw in ws:
            try:
                content = json.loads(raw).get("serverContent") or {}
            except (json.JSONDecodeError, AttributeError):
                continue
            if isinstance(interim := content.get("interimInputTranscription"), dict):
                if text := clean(str(interim.get("text", ""))):
                    self.on_interim()
                    await self.send({"type": "interim", "text": text})
            if isinstance(final := content.get("inputTranscription"), dict):
                if text := clean(str(final.get("text", ""))):
                    await self.on_final(text)

    async def _connect(self):
        ws = await websockets.connect(
            f"{WS_URL}?key={self.api_key}",
            max_size=8 * 1024 * 1024,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_TIMEOUT,
            close_timeout=5,
        )
        await ws.send(
            json.dumps(
                {
                    "setup": {
                        "model": f"models/{MODEL}",
                        "generationConfig": {"responseModalities": ["TEXT"]},
                        "inputAudioTranscription": {
                            "languageCodes": [],
                            "customVocabulary": VOCABULARY,
                            "mode": "SMART",
                        },
                    }
                }
            )
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if "setupComplete" in message:
                return ws
        raise TimeoutError("Gemini Live setup timed out")

    async def run(self, audio: asyncio.Queue) -> None:
        """Consume audio chunks until a None arrives; reconnects and rotates sessions as needed."""
        carry: bytes | None = None
        failures = 0
        while True:
            receiver = ws = None
            try:
                ws = await self._connect()
                failures = 0
                await self.send({"type": "status", "text": "Gemini 轉錄已連線，正在聆聽"})
                receiver = asyncio.create_task(self._receive(ws))
                started = time.monotonic()
                while time.monotonic() - started < ROTATE_SECONDS:
                    if receiver.done():
                        raise receiver.exception() or ConnectionError("Gemini Live connection ended")
                    if carry is not None:
                        chunk, carry = carry, None
                    else:
                        try:
                            chunk = await asyncio.wait_for(audio.get(), timeout=0.5)
                        except TimeoutError:
                            continue
                    if chunk is None:
                        await ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                        await asyncio.sleep(1.5)  # let the last final transcript arrive
                        return
                    try:
                        await ws.send(
                            json.dumps(
                                {
                                    "realtimeInput": {
                                        "audio": {
                                            "data": base64.b64encode(chunk).decode("ascii"),
                                            "mimeType": "audio/pcm;rate=16000",
                                        }
                                    }
                                }
                            )
                        )
                    except Exception:
                        carry = chunk
                        raise
                await ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                await asyncio.sleep(1.5)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network drops: retry with backoff, give up after MAX_FAILURES
                failures += 1
                await self.send({"type": "error", "text": f"Gemini 轉錄連線錯誤（第 {failures} 次）：{e}"})
                if failures >= MAX_FAILURES:
                    await self.send({"type": "error", "text": "Gemini 連續失敗 5 次，請改用手動輸入問題。"})
                    return
                await asyncio.sleep(min(1.5 * failures, 6))
            finally:
                if receiver:
                    receiver.cancel()
                if ws:
                    await ws.close()
