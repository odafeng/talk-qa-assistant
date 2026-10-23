"""Fast first pass with Jev: does the new transcript need an answer? Claude is asked only when it might.

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


@dataclass(frozen=True)
class Verdict:
    status: str  # "answer", "wait" or "skip"
    confidence: float
    seconds: float

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
            response = await self.client.system_one(state, {"verdict": VERDICT})
        except TypeSafeError:
            return None
        answer = response.answers.get("verdict")
        if not isinstance(answer, ChoiceAnswer) or answer.choice not in STATUS:
            return None
        return Verdict(STATUS[answer.choice], answer.confidence, time.monotonic() - started)
