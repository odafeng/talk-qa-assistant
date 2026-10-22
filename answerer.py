"""Answer audience questions about SurgiFlowVid with Claude, grounded in the paper and talk notes."""

import re
import subprocess
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

# Measured 2026-10-23 on the same question: Opus 5.5 13.6 s, Sonnet 5.5 with thinking and the PDF
# 10-15 s, Sonnet 5.5 without thinking and the paper as page text 5.9 s (first text 1.9 s).
MODEL = "claude-sonnet-5-5"
EFFORT = "low"
THINKING = {"type": "between_tools"}  # Sonnet 5.5's way to turn thinking off
MAX_TOKENS = 16000
BETAS = ["server-side-fallback-2026-07-01"]
SKIP = "SKIP"
WAIT = "WAIT"

SYSTEM = """你是講者的即時 Q&A 助手。講者黃士峰（大腸直腸外科醫師、博士生）正在 IIR seminar 線上報告 SurgiFlowVid（Venkatesh et al., ICML 2026），現在是提問時間。

你會收到會議的自動逐字稿（可能有辨識錯字）。每一行標了說話的人：「聽眾」是 Meet 裡其他人的聲音（提問者、主持人），「我」是講者自己的麥克風。逐字稿沒有區分不同的聽眾，同一個聽眾的問題也可能被停頓切成好幾行。

你的工作：找出聽眾最近提出、而且還沒回答過的問題，給講者一份可以直接唸的口語稿。

只輸出下面三種之一：
1. SKIP：最近的聽眾發言裡沒有新的問題（寒暄、「聽得到嗎」、「謝謝」、主持人串場、講者自己在說話、或問題已經回答過而且沒有追問）。只輸出這四個字母。
2. WAIT：聽眾的問題看起來還沒說完（話講到一半、只有開場白「我想請問一下…」）。只輸出這四個字母。如果訊息裡註明「對方已經停下來了」，就不要再輸出 WAIT。
3. 回答，格式如下：

【問題】一句話寫出你理解的問題；逐字稿有錯字時寫出推測的原意。一次有好幾個問題就合成一句寫清楚。
【口語稿】講者可以直接唸的回答，三到六句，像當面說話一樣自然，不要條列、不要 markdown。可以用一句很短的話開頭（例如「謝謝這個問題」），接著直接回答，關鍵數字照原文說。問題是英文就用英文，否則用繁體中文。原文沒寫的部分，在口語稿裡就老實說「論文沒有報告這一點」。
【依據】一行就好：章節、表格或圖的編號和關鍵數字（照原文寫，附標準差）。不要整段引用原文，原文引句會另外附在卡片下方。
【翻到】可以翻到的投影片頁碼和標題。沒有適合的頁就省略這一行。
【注意】只在需要時寫：原文沒寫、原文前後矛盾、口語稿裡哪一句是推論。

規則：
- 只根據提供的論文原文和講者筆記回答。論文是唯一的權威來源；講者筆記裡標成 MY ANALYSIS、批判、推論的內容是講者自己的看法，用的時候要在【注意】說明。
- 原文沒寫的事，直接說原文沒寫，不要編造數字、作者意圖或實驗。
- 講者筆記裡已經整理過的題目（Q&A 預備、原文對不上的地方），優先沿用，但數字以原文為準。
- 講者自己（「我」）已經回答了一部分時，口語稿要接著補充，不要重複他已經說過的。
- 聽眾問題常常斷斷續續、被停頓切成好幾行，要把同一個人連續的幾行合起來理解。如果聽眾是在補充或修正一個已經回答過的問題，就重新給一份完整的回答，並在【問題】開頭寫「（更新）」。
- 不要在回答最後另外列出原文句子，原文引句會由系統附在卡片下方。
- 講者不是這篇論文的作者。口語稿提到論文的做法時要說「作者」或「論文」，不要說「我們」。"""


def _deck_map() -> str:
    titles = page_titles(DECK.read_text())
    lines = [f"第 {n} 頁：{t}" + ("（Backup）" if n > 44 else "") for n, t in enumerate(titles, 1)]
    return "\n".join(lines)


def paper_pages() -> list[tuple[int, str]]:
    """The paper as (page number, text) per non-empty page.

    Page text instead of the PDF halves the context (88k vs 173k tokens) and was much faster;
    tables survive pdftotext -layout well enough that table numbers were still answered correctly.
    """
    text = subprocess.run(
        ["pdftotext", "-layout", str(PAPER), "-"], check=True, capture_output=True, text=True
    )
    return [(n, page) for n, page in enumerate(text.stdout.split("\f"), 1) if page.strip()]


def build_context() -> tuple[list[dict], list[int]]:
    """The cached prefix (paper pages + talk materials) and the page number of each paper block."""
    pages = paper_pages()
    notes = (
        "以下是講者自己的材料，不是論文原文。\n\n"
        f"## 投影片頁碼對照\n{_deck_map()}\n\n"
        f"## 講稿\n{SCRIPT.read_text()}\n\n"
        f"## 講者大綱與筆記（含 Q&A 預備和講者的分析）\n{OUTLINE.read_text()}"
    )
    paper = {
        "type": "document",
        "source": {
            "type": "content",
            "content": [{"type": "text", "text": f"[論文第 {n} 頁]\n{text}"} for n, text in pages],
        },
        "title": "SurgiFlowVid (ICML 2026) 論文原文，逐頁文字",
        "citations": {"enabled": True},
    }
    notes_block = {"type": "text", "text": notes, "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    return [paper, notes_block], [n for n, _ in pages]


@dataclass
class Citation:
    page: int
    text: str


@dataclass
class Answer:
    text: str
    citations: list[Citation]
    status: str  # "answer", "skip", "wait" or "refused"

    @property
    def question(self) -> str:
        m = re.search(r"【問題】(.+)", self.text)
        return m.group(1).strip() if m else ""


def _request_block(transcript: list[str], answered: list[str], note: str = "") -> dict:
    parts = []
    if answered:
        parts.append("已經回答過的問題：\n" + "\n".join(f"- {q}" for q in answered[-8:]))
    parts.append("最近的逐字稿（由舊到新）：\n" + "\n".join(transcript))
    if note:
        parts.append(note)
    return {"type": "text", "text": "\n\n".join(parts)}


def _citations(message, page_numbers: list[int]) -> list[Citation]:
    seen, out = set(), []
    for block in message.content:
        for c in getattr(block, "citations", None) or []:
            index = getattr(c, "start_block_index", None)
            page = page_numbers[index] if index is not None and index < len(page_numbers) else None
            text = re.sub(r"\s+", " ", getattr(c, "cited_text", "")).strip()
            if page is None or (page, text) in seen:
                continue
            seen.add((page, text))
            out.append(Citation(page=page, text=text[:200]))
    return out


class Answerer:
    def __init__(self, api_key: str) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.context, self.page_numbers = build_context()

    def _params(self, content: list[dict], max_tokens: int) -> dict:
        return {
            "model": MODEL,
            "max_tokens": max_tokens,
            "system": SYSTEM,
            "output_config": {"effort": EFFORT},
            "thinking": THINKING,
            "betas": BETAS,
            "fallbacks": "default",
            "messages": [{"role": "user", "content": content}],
        }

    async def prewarm(self) -> int:
        """Write the paper and notes into the prompt cache so the first real answer is fast."""
        response = await self.client.beta.messages.create(**self._params(self.context, 0))
        return response.usage.cache_creation_input_tokens + response.usage.cache_read_input_tokens

    async def stream(
        self, transcript: list[str], answered: list[str], note: str = ""
    ) -> AsyncIterator[str | Answer]:
        """Yield text deltas, then one final Answer."""
        content = [*self.context, _request_block(transcript, answered, note)]
        async with self.client.beta.messages.stream(**self._params(content, MAX_TOKENS)) as stream:
            async for text in stream.text_stream:
                yield text
            message = await stream.get_final_message()
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        if message.stop_reason == "refusal":
            status = "refused"
        else:
            status = {SKIP: "skip", WAIT: "wait"}.get(text, "answer")
        yield Answer(text=text, citations=_citations(message, self.page_numbers), status=status)
