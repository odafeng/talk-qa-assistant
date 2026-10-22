"""Answer audience questions about SurgiFlowVid with Claude, grounded in the paper and talk notes."""

import base64
import re
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from sync_notes import page_titles  # noqa: E402

PAPER = ROOT / "SurgiFlowVid_ICML2026.pdf"
SCRIPT = ROOT / "SurgiFlowVid_speaker_script.md"
OUTLINE = ROOT / "SurgiFlowVid_seminar_outline.md"
DECK = ROOT / "deck/slides/surgiflowvid-seminar/index.tsx"

MODEL = "claude-opus-5-5"
EFFORT = "low"
MAX_TOKENS = 16000
BETAS = ["server-side-fallback-2026-07-01"]
SKIP = "SKIP"

SYSTEM = """你是講者的即時 Q&A 助手。講者黃士峰（大腸直腸外科醫師、博士生）正在 IIR seminar 線上報告 SurgiFlowVid（Venkatesh et al., ICML 2026）。你收到的是會議中「其他人」說話的自動逐字稿，可能有辨識錯字，也可能根本不是問題。

先判斷：如果這段話不是在問跟這場報告、這篇論文或相關研究有關的問題（例如寒暄、「聽得到嗎」、「謝謝」、主持人串場、跟論文無關的閒聊），只輸出 SKIP 這四個字母，不要輸出其他任何東西。

如果是問題，用下面的格式回答，整體要短，講者要能在幾秒內看完：

【問題】用一句話寫出你理解的問題。逐字稿有錯字時，寫出你推測的原意。
【回答】一到三句，講者可以直接唸出來的話。問題是英文就用英文回答，否則用繁體中文。
【依據】原文的出處：章節、表格或圖的編號，數字照原文寫，附上標準差。
【翻到】可以翻到的投影片頁碼和標題，例如「第 38 頁 Sensitivity…」。沒有適合的頁就省略這一行。
【注意】只在需要時寫：原文沒寫、原文自己前後矛盾、或這是推論而不是原文內容。

規則：
- 只根據提供的論文原文和講者筆記回答。論文是唯一的權威來源；講者筆記裡標成 MY ANALYSIS、批判、推論的內容是講者自己的看法，引用時要說明是講者的分析。
- 原文沒寫的事，直接說「原文沒寫」，不要編造數字、作者意圖或實驗。
- 如果講者筆記裡已經整理過這題（例如 Q&A 預備、原文對不上的地方），優先沿用，但數字要以原文為準。
- 不要寒暄，不要重複問題以外的鋪陳。"""


def _deck_map() -> str:
    titles = page_titles(DECK.read_text())
    lines = [f"第 {n} 頁：{t}" + ("（Backup）" if n > 44 else "") for n, t in enumerate(titles, 1)]
    return "\n".join(lines)


def build_context() -> list[dict]:
    """The cached prefix: the paper PDF plus the talk materials. Must stay byte-identical."""
    pdf = base64.standard_b64encode(PAPER.read_bytes()).decode("ascii")
    notes = (
        "以下是講者自己的材料，不是論文原文。\n\n"
        f"## 投影片頁碼對照\n{_deck_map()}\n\n"
        f"## 講稿\n{SCRIPT.read_text()}\n\n"
        f"## 講者大綱與筆記（含 Q&A 預備和講者的分析）\n{OUTLINE.read_text()}"
    )
    return [
        {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": pdf},
            "title": "SurgiFlowVid (ICML 2026) 論文原文",
            "citations": {"enabled": True},
        },
        {"type": "text", "text": notes, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]


@dataclass
class Citation:
    page: int
    text: str


@dataclass
class Answer:
    text: str
    citations: list[Citation]
    skipped: bool
    refused: bool


def _question_block(question: str, history: list[tuple[str, str]]) -> dict:
    parts = []
    if history:
        recent = "\n\n".join(f"問：{q}\n答：{a}" for q, a in history[-3:])
        parts.append(f"（先前的問答，供追問時參考）\n{recent}\n")
    parts.append(f"逐字稿：{question}")
    return {"type": "text", "text": "\n".join(parts)}


def _citations(message) -> list[Citation]:
    seen, out = set(), []
    for block in message.content:
        for c in getattr(block, "citations", None) or []:
            page = getattr(c, "start_page_number", None)
            text = re.sub(r"\s+", " ", getattr(c, "cited_text", "")).strip()
            if page is None or (page, text) in seen:
                continue
            seen.add((page, text))
            out.append(Citation(page=page, text=text[:200]))
    return out


class Answerer:
    def __init__(self, api_key: str) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.context = build_context()

    def _params(self, content: list[dict], max_tokens: int) -> dict:
        return {
            "model": MODEL,
            "max_tokens": max_tokens,
            "system": SYSTEM,
            "output_config": {"effort": EFFORT},
            "betas": BETAS,
            "fallbacks": "default",
            "messages": [{"role": "user", "content": content}],
        }

    async def prewarm(self) -> int:
        """Write the paper and notes into the prompt cache so the first real answer is fast."""
        response = await self.client.beta.messages.create(**self._params(self.context, 0))
        return response.usage.cache_creation_input_tokens + response.usage.cache_read_input_tokens

    async def stream(self, question: str, history: list[tuple[str, str]]) -> AsyncIterator[str | Answer]:
        """Yield text deltas, then one final Answer."""
        content = [*self.context, _question_block(question, history)]
        async with self.client.beta.messages.stream(**self._params(content, MAX_TOKENS)) as stream:
            async for text in stream.text_stream:
                yield text
            message = await stream.get_final_message()
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        yield Answer(
            text=text,
            citations=_citations(message),
            skipped=text == SKIP,
            refused=message.stop_reason == "refusal",
        )
