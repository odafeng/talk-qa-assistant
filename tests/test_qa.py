from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from fastapi.testclient import TestClient

import answerer
import app as app_module
import config
from segmenter import Segmenter


def test_utterance_ends_after_pause():
    s = Segmenter(silence=2.0)
    assert s.add_final("請問 optical flow", now=0.0) is None
    assert s.add_final("只在訓練時用嗎", now=1.0) is None
    assert s.tick(now=2.5) is None  # 1.5 s since the last piece
    assert s.tick(now=3.1) == "請問 optical flow 只在訓練時用嗎"
    assert s.tick(now=10.0) is None  # nothing pending any more


def test_interim_text_keeps_the_utterance_open():
    s = Segmenter(silence=2.0)
    s.add_final("請問這個方法", now=0.0)
    s.add_interim(now=1.8)
    assert s.tick(now=2.5) is None
    assert s.tick(now=3.9) == "請問這個方法"


def test_short_pieces_are_dropped():
    s = Segmenter(silence=2.0)
    s.add_final("好。", now=0.0)
    assert s.tick(now=3.0) is None


def test_long_monologue_is_cut_at_the_cap():
    s = Segmenter(silence=2.0, max_chars=10)
    assert s.add_final("一二三四五", now=0.0) is None
    assert s.add_final("六七八九十", now=0.5) == "一二三四五 六七八九十"


def test_deck_map_lists_every_page_and_marks_backup():
    lines = answerer._deck_map().splitlines()
    assert len(lines) == 69
    assert lines[0].startswith("第 1 頁：")
    assert lines[44].endswith("（Backup）") and not lines[43].endswith("（Backup）")


def test_context_caches_the_notes_after_the_pdf():
    pdf, notes = answerer.build_context()
    assert pdf["type"] == "document" and pdf["citations"] == {"enabled": True}
    assert notes["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "Q&A 預備" in notes["text"] and "第 44 頁" in notes["text"]


def test_request_block_lists_answered_questions_then_the_transcript():
    answered = [f"q{i}" for i in range(10)]
    text = answerer._request_block(["[00:01] 聽眾：請問 flow 呢"], answered, "NOTE")["text"]
    assert "- q1\n" not in text and "- q2" in text and "- q9" in text
    assert text.index("已經回答過") < text.index("最近的逐字稿") < text.index("NOTE")


def test_answer_status_and_question_extraction():
    a = answerer.Answer(text="【問題】flow 只在訓練用嗎？\n【口語稿】是的。", citations=[], status="answer")
    assert a.question == "flow 只在訓練用嗎？"
    assert answerer.Answer(text="SKIP", citations=[], status="skip").question == ""


def test_citations_are_deduplicated_and_keep_page_numbers():
    cite = SimpleNamespace(start_page_number=7, cited_text="flow is used\n only in training")
    message = SimpleNamespace(
        content=[
            SimpleNamespace(citations=[cite, cite]),
            SimpleNamespace(citations=None),
        ]
    )
    assert answerer._citations(message) == [answerer.Citation(page=7, text="flow is used only in training")]


ORIGIN = {"origin": "http://localhost:8777"}


@pytest.fixture(autouse=True)
def sessions_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "SESSIONS", tmp_path)


def test_server_starts_and_reports_missing_claude_key(monkeypatch):
    monkeypatch.setattr(config, "anthropic_key", lambda: "")
    with TestClient(app_module.app) as client:
        assert client.get("/health").json()["anthropic_key"] is False
        assert "Q&amp;A 助手" in client.get("/").text
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["anthropic_key"] is False
            ws.send_json({"type": "ask", "text": "flow 只在訓練時用嗎"})
            assert ws.receive_json()["type"] == "final"
            assert ws.receive_json()["type"] == "thinking"
            assert ws.receive_json()["type"] == "error"


def test_websocket_rejects_other_origins(monkeypatch):
    monkeypatch.setattr(config, "anthropic_key", lambda: "")
    with TestClient(app_module.app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_json()


def test_download_only_serves_session_files(monkeypatch):
    monkeypatch.setattr(config, "anthropic_key", lambda: "")
    with TestClient(app_module.app) as client:
        assert client.get("/download/..%2Fconfig.py").status_code == 404
        assert client.get("/download/20260101_000000_transcript.txt").status_code == 404


class FakeAnswerer:
    def __init__(self):
        self.calls = []

    async def stream(self, transcript, answered, note=""):
        self.calls.append((list(transcript), list(answered), note))
        yield "【問題】flow 只在訓練用嗎？"
        yield "\n【口語稿】是的，只在訓練時用。"
        yield answerer.Answer(
            text="【問題】flow 只在訓練用嗎？\n【口語稿】是的，只在訓練時用。",
            citations=[answerer.Citation(page=4, text="flow")],
            status="answer",
        )


def test_manual_question_streams_a_spoken_answer(monkeypatch):
    monkeypatch.setattr(config, "anthropic_key", lambda: "")
    fake = FakeAnswerer()
    with TestClient(app_module.app) as client:
        monkeypatch.setitem(app_module.state, "answerer", fake)
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.receive_json()  # hello
            ws.send_json({"type": "ask", "text": "flow 只在訓練時用嗎"})
            events = [ws.receive_json() for _ in range(7)]
    kinds = [e["type"] for e in events]
    assert kinds == [
        "final",
        "thinking",
        "answer_start",
        "answer_delta",
        "answer_delta",
        "answer_done",
        "thinking",
    ]
    assert events[5]["status"] == "answer" and events[5]["citations"][0]["page"] == 4
    transcript, answered, note = fake.calls[0]
    assert transcript[-1].endswith("我（手動輸入的問題）：flow 只在訓練時用嗎") and answered == []
    assert "不要輸出 SKIP 或 WAIT" in note
