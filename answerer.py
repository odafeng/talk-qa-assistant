"""Answer audience questions with Claude, grounded in the talk's paper and the speaker's notes."""

import base64
import io
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import anthropic
from PIL import Image

from slides import deck_map
from talk import Talk, paper_pages

# Measured 2026-10-23 on the same question: Opus 5.5 13.6 s, Sonnet 5.5 with thinking and the PDF
# 10-15 s, Sonnet 5.5 without thinking and the paper as page text 5.9 s (first text 1.9 s). ADR 0002.
MODEL = "claude-sonnet-5-5"
EFFORT = "low"
THINKING = {"type": "between_tools"}  # Sonnet 5.5's way to turn thinking off
MAX_TOKENS = 16000
BETAS = ["server-side-fallback-2026-07-01"]
SKIP = "SKIP"
WAIT = "WAIT"

SYSTEM_TEMPLATE = """你是講者的即時 Q&A 助手。講者{speaker}正在{venue}線上報告{title}，現在是提問時間。

你會收到會議的自動逐字稿（可能有辨識錯字）。每一行標了說話的人：「聽眾」是 Meet 裡其他人的聲音（提問者、主持人），「我」是講者自己的麥克風。逐字稿沒有區分不同的聽眾，同一個聽眾的問題也可能被停頓切成好幾行。

你的工作：找出聽眾最近提出、而且還沒回答過的問題，給講者一份可以直接唸的口語稿。

只輸出下面三種之一：
1. SKIP：「新逐字稿」裡沒有需要新回答的問題。包括：寒暄、「聽得到嗎」、「謝謝」、主持人串場、講者自己在說話；聽眾只是附和（「對」「好，大概」「了解」）、重述自己的理解而且理解正確、或把已經回答過的問題再講一次；講者（「我」）已經在新逐字稿裡回答了聽眾的話。只輸出這四個字母。
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
- 資料裡也可能有圖片（論文的圖表、投影片截圖等），每張前面標了檔名。問題牽涉到圖表時可以參考；只有圖裡看得到的資訊，在【依據】寫出檔名。
- 講者筆記裡已經整理過的題目（例如 Q&A 預備、原文對不上的地方），優先沿用，但數字以原文為準。
- 講者自己（「我」）已經回答了一部分時，口語稿要接著補充，不要重複他已經說過的。
- 聽眾問題常常斷斷續續、被停頓切成好幾行，要把同一個人連續的幾行合起來理解。
- 只看「新逐字稿」決定要不要回答；「之前的逐字稿」已經判斷過，只當上下文。
- 回答之前先比對「已經回答過的問題」：新逐字稿只是把同一題講完、換句話說、或再確認一次，輸出 SKIP。但主題相近、問的是不同面向（例如前一題問片段長度，這一題問整台手術的負擔；前一題問為什麼這樣設計，這一題問改了會怎樣），要當成新問題回答。
- 聽眾只是附和或表示理解時（「好，大概」「對」「了解」），即使你覺得講者剛才的說法不夠精確，也輸出 SKIP；需要更正的地方，等聽眾下一次提問時再寫進【注意】。
- 「（更新）」要很節制：只有新逐字稿裡聽眾提出了新的子問題，或聽眾有會影響理解的誤解、而講者還沒更正時，才重新回答，並在【問題】開頭寫「（更新）」。同一個問題只是又被提到、聽眾在消化講者的回答、或講者已經回應過，都輸出 SKIP。
- 不要在回答最後另外列出原文句子，原文引句會由系統附在卡片下方。
- 講者不是這篇論文的作者。口語稿提到論文的做法時要說「作者」或「論文」，不要說「我們」。"""


def system_prompt(talk: Talk) -> str:
    return SYSTEM_TEMPLATE.format(speaker=talk.speaker, venue=talk.venue, title=talk.title)


MAX_IMAGES = 100  # Claude API per-request image limit
IMAGE_EDGE = 1568  # Claude resizes anything larger anyway; sending it smaller saves tokens and upload


def _image_block(path: Path) -> dict:
    """An image as a base64 block, scaled down so its long edge is at most IMAGE_EDGE."""
    with Image.open(path) as img:
        fmt = "JPEG" if img.format == "JPEG" else "PNG"
        if max(img.size) > IMAGE_EDGE:
            img.thumbnail((IMAGE_EDGE, IMAGE_EDGE))
        if fmt == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, format=fmt, **({"quality": 85} if fmt == "JPEG" else {}))
    data = base64.standard_b64encode(buffer.getvalue()).decode("ascii")
    media = "image/jpeg" if fmt == "JPEG" else "image/png"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


@dataclass(frozen=True)
class Context:
    """The cached prefix plus what is needed to turn citations back into pages.

    Images slow every answer down (first text 0.8-2 s without them, 6-10 s with 29 figures on the
    SurgiFlowVid talk), so they sit behind a second cache breakpoint and are sent only when the
    question is about a figure or table (needs_images). Both prefixes are pre-warmed.
    """

    text_blocks: list[dict]  # papers, then the speaker's notes (cache breakpoint 1)
    image_blocks: list[dict]  # label + image per file (cache breakpoint 2 on the last one)
    pages: list[list[int]]  # pages[document][block] -> page number
    papers: list[str]  # document titles, in order
    images: int

    def blocks(self, with_images: bool) -> list[dict]:
        return self.text_blocks + (self.image_blocks if with_images else [])


# A question about what a figure shows: only then are the images worth their latency (+5-6 s,
# measured). Tables are left out: pdftotext keeps their numbers, which were answered correctly.
FIGURE_WORDS = re.compile(r"圖|fig|figure|畫面|截圖|照片|顏色|長什麼樣|看起來", re.IGNORECASE)


def needs_images(texts: list[str]) -> bool:
    return any(FIGURE_WORDS.search(t) for t in texts)


def build_context(talk: Talk) -> Context:
    """Papers as per-page text documents and the speaker's notes, then the images (ADR 0005).

    Papers go in as page text rather than as PDFs: half the tokens (88k vs 173k on the SurgiFlowVid
    talk) and much faster, while table numbers were still answered correctly (ADR 0002).
    """
    text_blocks, pages, papers = [], [], []
    for path in talk.papers:
        page_texts = paper_pages(path)
        title = path.name.removesuffix(".paper.txt") if path.name.endswith(".paper.txt") else path.stem
        text_blocks.append(
            {
                "type": "document",
                "source": {
                    "type": "content",
                    "content": [{"type": "text", "text": f"[{title} 第 {n} 頁]\n{t}"} for n, t in page_texts],
                },
                "title": f"論文原文：{title}",
                "citations": {"enabled": True},
            }
        )
        pages.append([n for n, _ in page_texts])
        papers.append(title)
    sections = ["以下是講者自己的材料，不是論文原文。"]
    if talk.slides:
        sections.append(f"## 投影片頁碼對照\n{deck_map(talk.slides, talk.body_pages)}")
    sections += [
        f"## {note.relative_to(talk.folder)}\n{note.read_text(encoding='utf-8')}" for note in talk.notes
    ]
    text_blocks.append(
        {"type": "text", "text": "\n\n".join(sections), "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    )
    images = talk.images[:MAX_IMAGES]
    image_blocks: list[dict] = []
    for path in images:
        image_blocks.append({"type": "text", "text": f"圖片：{path.relative_to(talk.folder)}"})
        image_blocks.append(_image_block(path))
    if image_blocks:
        image_blocks[-1] = {**image_blocks[-1], "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    return Context(text_blocks, image_blocks, pages, papers, len(images))


@dataclass
class Citation:
    page: int
    text: str
    paper: str = ""


@dataclass
class Answer:
    text: str
    citations: list[Citation]
    status: str  # "answer", "skip", "wait" or "refused"

    @property
    def question(self) -> str:
        m = re.search(r"【問題】(.+)", self.text)
        return m.group(1).strip() if m else ""


def _request_block(seen: list[str], new: list[str], answered: list[str], note: str = "") -> dict:
    """seen: lines an earlier verdict already covered (context only); new: lines since then."""
    parts = []
    if answered:
        parts.append("已經回答過的問題：\n" + "\n".join(f"- {q}" for q in answered[-8:]))
    if seen:
        parts.append("之前的逐字稿（已經判斷過，只當上下文）：\n" + "\n".join(seen))
    parts.append("新逐字稿（上次判斷之後才說的，由舊到新）：\n" + ("\n".join(new) or "（沒有）"))
    if note:
        parts.append(note)
    return {"type": "text", "text": "\n\n".join(parts)}


def _citations(message, context: Context) -> list[Citation]:
    seen, out = set(), []
    for block in message.content:
        for c in getattr(block, "citations", None) or []:
            doc, index = getattr(c, "document_index", 0) or 0, getattr(c, "start_block_index", None)
            doc_pages = context.pages[doc] if doc < len(context.pages) else []
            page = doc_pages[index] if index is not None and index < len(doc_pages) else None
            text = re.sub(r"\s+", " ", getattr(c, "cited_text", "")).strip()
            if page is None or (doc, page, text) in seen:
                continue
            seen.add((doc, page, text))
            paper = context.papers[doc] if len(context.papers) > 1 else ""
            out.append(Citation(page=page, text=text[:200], paper=paper))
    return out


class Answerer:
    def __init__(self, api_key: str, talk: Talk) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.reload(talk)

    def reload(self, talk: Talk) -> None:
        """Rebuild the cached prefix from the context folder (new files are used from the next call)."""
        self.system = system_prompt(talk)
        self.context = build_context(talk)

    def _params(self, content: list[dict], max_tokens: int) -> dict:
        return {
            "model": MODEL,
            "max_tokens": max_tokens,
            "system": self.system,
            "output_config": {"effort": EFFORT},
            "thinking": THINKING,
            "betas": BETAS,
            "fallbacks": "default",
            "messages": [{"role": "user", "content": content}],
        }

    async def prewarm(self) -> int:
        """Write both prefixes (text only, then text + images) into the cache so neither starts cold."""
        total = 0
        for with_images in (False, True) if self.context.image_blocks else (False,):
            blocks = self.context.blocks(with_images)
            response = await self.client.beta.messages.create(**self._params(blocks, 0))
            total = response.usage.cache_creation_input_tokens + response.usage.cache_read_input_tokens
        return total

    async def stream(
        self, seen: list[str], new: list[str], answered: list[str], note: str = "", with_images: bool = False
    ) -> AsyncIterator[str | Answer]:
        """Yield text deltas, then one final Answer. Images are sent only when with_images."""
        content = [*self.context.blocks(with_images), _request_block(seen, new, answered, note)]
        async with self.client.beta.messages.stream(**self._params(content, MAX_TOKENS)) as stream:
            async for text in stream.text_stream:
                yield text
            message = await stream.get_final_message()
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        if message.stop_reason == "refusal":
            status = "refused"
        else:
            status = {SKIP: "skip", WAIT: "wait"}.get(text, "answer")
        yield Answer(text=text, citations=_citations(message, self.context), status=status)
