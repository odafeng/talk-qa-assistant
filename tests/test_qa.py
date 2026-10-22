from types import SimpleNamespace

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


def test_question_block_keeps_only_three_recent_answers():
    history = [(f"q{i}", f"a{i}") for i in range(5)]
    text = answerer._question_block("新問題", history)["text"]
    assert "q1" not in text and "q2" in text and "q4" in text
    assert text.endswith("逐字稿：新問題")


def test_citations_are_deduplicated_and_keep_page_numbers():
    cite = SimpleNamespace(start_page_number=7, cited_text="flow is used\n only in training")
    message = SimpleNamespace(
        content=[
            SimpleNamespace(citations=[cite, cite]),
            SimpleNamespace(citations=None),
        ]
    )
    assert answerer._citations(message) == [answerer.Citation(page=7, text="flow is used only in training")]


def test_server_starts_and_reports_missing_claude_key(monkeypatch):
    monkeypatch.setattr(config, "anthropic_key", lambda: "")
    with TestClient(app_module.app) as client:
        assert client.get("/health").json()["anthropic_key"] is False
        assert "Q&amp;A 助手" in client.get("/").text
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["anthropic_key"] is False
            ws.send_json({"type": "ask", "text": "flow 只在訓練時用嗎"})
            assert ws.receive_json()["type"] == "error"
