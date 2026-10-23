import asyncio
from types import SimpleNamespace

from typesafe_sdk import ChoiceAnswer, TypeSafeError

import gate
from gate import Gate, Verdict, route


def test_confident_verdicts_are_followed_and_unsure_ones_go_to_claude():
    assert route(Verdict("skip", 0.95, 0.2)) == "skip"
    assert route(Verdict("wait", 0.80, 0.2)) == "wait"
    assert route(Verdict("answer", 0.89, 0.2)) == "answer"
    assert route(Verdict("skip", 0.79, 0.2)) == "claude"
    assert route(None) == "claude"


def _gate_returning(result):
    g = Gate("test-key")

    async def system_one(state, questions):
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(answers={"verdict": result})

    g.client = SimpleNamespace(system_one=system_one)  # type: ignore[assignment]
    return g


def test_judge_maps_jev_choices_to_statuses():
    answer = ChoiceAnswer(choice="no_answer", confidence=0.97, probabilities={"no_answer": 0.97})
    verdict = asyncio.run(_gate_returning(answer).judge("逐字稿"))
    assert verdict is not None and verdict.status == "skip" and verdict.confidence == 0.97


def test_judge_returns_none_when_jev_fails_or_answers_oddly():
    assert asyncio.run(_gate_returning(TypeSafeError("down")).judge("x")) is None
    odd = ChoiceAnswer(choice="maybe", confidence=0.99, probabilities={"maybe": 0.99})
    assert asyncio.run(_gate_returning(odd).judge("x")) is None


def test_every_jev_option_has_a_status():
    assert set(gate.VERDICT.criteria) == set(gate.STATUS)
