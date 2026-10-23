"""Fast first pass with Jev: does the new transcript need an answer, and does it need the figures?

Replayed on the first real Q&A (2026-10-23, 53 audience lines), Jev took 0.2 s per verdict against
Claude's 2 s, and its verdicts at confidence >= 0.8 (31 of 53) matched Claude's in 30 cases; the one
difference was a question Claude missed. Below that confidence it often called self-introductions
"unfinished", so those verdicts go to Claude instead. ADR 0004.
"""

import time
from dataclasses import dataclass

from typesafe_sdk import AsyncTypeSafeClient, Choice, ChoiceAnswer, TypeSafeError

MODEL = "jev-1.13.0"  # pinned, as in jevdemo; never jev-latest
THRESHOLD = 0.8
TIMEOUT = 5.0

VERDICT = Choice(
    instructions=(
        "這是線上研討會 Q&A 時間的自動逐字稿。「聽眾」是提問者和主持人，「我」是講者。"
        "只根據「新逐字稿」那一段判斷：講者現在需不需要準備一份新的回答？"
    ),
    criteria={
        "new_question": "需要回答：新逐字稿裡聽眾提出了還沒回答過的問題；或對已回答的問題提出新的子問題；"
        "或聽眾有明顯誤解、講者還沒更正。",
        "unfinished": "還沒說完：聽眾的問題講到一半，只有開場白或半句話，應該等他說完。",
        "no_answer": "不需要回答：寒暄、道謝、附和（「對」「好」「了解」）、主持人串場或點名、講者自己在說話、"
        "或只是把已經回答過的問題再講一次、重述自己正確的理解。",
    },
)
STATUS = {"new_question": "answer", "unfinished": "wait", "no_answer": "skip"}

# Asked in the same call, so it costs no extra time. On 18 labelled questions it was right 17 times,
# against 13 for the keyword rule (answerer.needs_images), which misses "what did that failed
# example look like?"; the app sends images when either says so (ADR 0005).
FIGURE = Choice(
    instructions=(
        "只看「新逐字稿」裡的問題（聽眾問的，或講者手動輸入的）：要回答它，需不需要看論文裡的圖"
        "（例如架構圖、長條圖、生成影片的畫面、失敗案例的截圖），還是論文文字和表格的數字就夠了？"
    ),
    criteria={
        "needs_figure": "需要看圖：問題在問圖裡畫了什麼、某個例子或畫面長什麼樣子、看起來怎樣、只畫在圖上的數值或位置。",
        "text_enough": "不需要看圖：問的是方法、理由、設定、表格裡的數字、限制或一般看法，論文文字就能回答。",
    },
)


@dataclass(frozen=True)
class Verdict:
    status: str  # "answer", "wait" or "skip"
    confidence: float
    seconds: float
    needs_figure: bool = False

    @property
    def confident(self) -> bool:
        return self.confidence >= THRESHOLD


def route(verdict: Verdict | None) -> str:
    """What to do with Jev's verdict: "skip" or "wait" (no Claude call), "answer" (Claude writes
    without judging), or "claude" (low confidence or no verdict: Claude judges as before)."""
    if verdict is None or not verdict.confident:
        return "claude"
    return verdict.status


class Gate:
    def __init__(self, api_key: str) -> None:
        self.client = AsyncTypeSafeClient(api_key=api_key, model=MODEL, timeout=TIMEOUT)

    async def judge(self, state: str) -> Verdict | None:
        """Jev's verdict on the request text, or None if Jev is unavailable (Claude then decides)."""
        started = time.monotonic()
        try:
            response = await self.client.system_one(state, {"verdict": VERDICT, "figure": FIGURE})
        except TypeSafeError:
            return None
        answer = response.answers.get("verdict")
        if not isinstance(answer, ChoiceAnswer) or answer.choice not in STATUS:
            return None
        figure = response.answers.get("figure")
        needs_figure = isinstance(figure, ChoiceAnswer) and figure.choice == "needs_figure"
        return Verdict(STATUS[answer.choice], answer.confidence, time.monotonic() - started, needs_figure)
