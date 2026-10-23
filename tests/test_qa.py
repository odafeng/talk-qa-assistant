from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import answerer
import app as app_module
import config
import talk as talk_config
from segmenter import Segmenter
from slides import deck_map


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


def test_context_folder_sorts_files_by_type(talk_dir):
    talk = talk_config.load(talk_dir)
    assert talk.speaker == "測試講者" and talk.vocabulary == ("TestPaper",)
    assert talk.papers == (talk_dir / "paper.paper.txt",)
    assert talk.notes == (talk_dir / "outline.txt", talk_dir / "script.md")  # sessions/ is skipped
    assert talk.images == (talk_dir / "figures" / "table2.png",)
    assert talk.slides == talk_dir / "index.tsx" and talk.sessions == talk_dir / "sessions"


def test_context_folder_works_without_talk_toml(talk_dir):
    (talk_dir / "talk.toml").unlink()
    talk = talk_config.load(talk_dir)
    assert talk.title == "project" and talk.speaker == "講者" and talk.body_pages is None


def test_signature_changes_when_a_file_is_added(talk_dir):
    before = talk_config.signature(talk_dir)
    (talk_dir / "new_note.md").write_text("補充", encoding="utf-8")
    assert talk_config.signature(talk_dir) != before


def test_paper_pages_keep_page_numbers_and_skip_blank_pages(talk_dir):
    pages = talk_config.paper_pages(talk_dir / "paper.paper.txt")
    assert [n for n, _ in pages] == [1, 3]


def test_deck_map_lists_every_page_and_marks_backup(talk_dir):
    assert deck_map(talk_dir / "index.tsx", body_pages=2).splitlines() == [
        "第 1 頁：Cover page",
        "第 2 頁：Dual-prediction loss",
        "第 3 頁：Extra（Backup）",
    ]


def test_context_has_papers_then_images_then_cached_notes(talk_dir):
    import base64
    import io

    from PIL import Image

    talk = talk_config.load(talk_dir)
    ctx = answerer.build_context(talk)
    paper, notes = ctx.text_blocks
    label, image = ctx.image_blocks
    assert ctx.blocks(False) == [paper, notes] and ctx.blocks(True) == [paper, notes, label, image]
    assert image["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert paper["citations"] == {"enabled": True} and ctx.pages == [[1, 3]] and ctx.papers == ["paper"]
    assert paper["source"]["content"][1]["text"].startswith("[paper 第 3 頁]")
    assert label["text"] == "圖片：figures/table2.png" and ctx.images == 1
    shrunk = Image.open(io.BytesIO(base64.b64decode(image["source"]["data"])))
    assert max(shrunk.size) == answerer.IMAGE_EDGE  # 3000 px wide scaled down
    assert notes["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "Q&A 預備" in notes["text"] and "第 3 頁：Extra（Backup）" in notes["text"]
    assert "測試講者" in answerer.system_prompt(talk) and "{speaker}" not in answerer.system_prompt(talk)


def test_request_block_separates_seen_and_new_lines():
    answered = [f"q{i}" for i in range(10)]
    text = answerer._request_block(["[00:01] 聽眾：舊的話"], ["[00:09] 聽眾：新的問題"], answered, "NOTE")[
        "text"
    ]
    assert "- q1\n" not in text and "- q2" in text and "- q9" in text
    assert text.index("已經回答過") < text.index("之前的逐字稿") < text.index("新逐字稿") < text.index("NOTE")
    assert text.index("舊的話") < text.index("之前的逐字稿") + 40 < text.index("新的問題")


def test_request_block_without_history_has_only_new_lines():
    text = answerer._request_block([], ["[00:01] 聽眾：請問"], [])["text"]
    assert "之前的逐字稿" not in text and "已經回答過" not in text and "請問" in text


def test_answer_status_and_question_extraction():
    a = answerer.Answer(text="【問題】flow 只在訓練用嗎？\n【口語稿】是的。", citations=[], status="answer")
    assert a.question == "flow 只在訓練用嗎？"
    assert answerer.Answer(text="SKIP", citations=[], status="skip").question == ""


def test_citations_are_deduplicated_and_keep_page_numbers():
    cite = SimpleNamespace(
        document_index=0, start_block_index=1, cited_text="flow is used\n only in training"
    )
    message = SimpleNamespace(
        content=[SimpleNamespace(citations=[cite, cite]), SimpleNamespace(citations=None)]
    )
    ctx = answerer.Context([], [], pages=[[1, 7, 8]], papers=["paper"], images=0)
    assert answerer._citations(message, ctx) == [
        answerer.Citation(page=7, text="flow is used only in training")
    ]


def test_citations_name_the_paper_when_there_are_several():
    cite = SimpleNamespace(document_index=1, start_block_index=0, cited_text="x")
    message = SimpleNamespace(content=[SimpleNamespace(citations=[cite])])
    ctx = answerer.Context([], [], pages=[[1, 2], [5, 6]], papers=["a", "b"], images=0)
    assert answerer._citations(message, ctx) == [answerer.Citation(page=5, text="x", paper="b")]


ORIGIN = {"origin": "http://localhost:8777"}


def test_server_starts_and_reports_missing_claude_key(monkeypatch, talk_config):
    monkeypatch.setattr(config, "anthropic_key", lambda: "")
    with TestClient(app_module.app) as client:
        assert client.get("/health").json()["anthropic_key"] is False
        assert "Q&amp;A 助手" in client.get("/").text
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["anthropic_key"] is False
            assert hello["title"] == "TestPaper（Doe et al., 2026）"
            ws.send_json({"type": "ask", "text": "flow 只在訓練時用嗎"})
            assert ws.receive_json()["type"] == "final"
            assert ws.receive_json()["type"] == "thinking"
            assert ws.receive_json()["type"] == "error"


def test_websocket_rejects_other_origins(monkeypatch, talk_config):
    monkeypatch.setattr(config, "anthropic_key", lambda: "")
    with TestClient(app_module.app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_json()


def test_download_only_serves_session_files(monkeypatch, talk_config):
    monkeypatch.setattr(config, "anthropic_key", lambda: "")
    with TestClient(app_module.app) as client:
        assert client.get("/download/..%2Fconfig.py").status_code == 404
        assert client.get("/download/20260101_000000_transcript.txt").status_code == 404


class FakeAnswerer:
    def __init__(self):
        self.calls = []
        self.context = answerer.Context([], [], pages=[], papers=[], images=0)

    async def stream(self, seen, new, answered, note="", with_images=False):
        self.calls.append((list(seen), list(new), list(answered), note))
        yield "【問題】flow 只在訓練用嗎？"
        yield "\n【口語稿】是的，只在訓練時用。"
        yield answerer.Answer(
            text="【問題】flow 只在訓練用嗎？\n【口語稿】是的，只在訓練時用。",
            citations=[answerer.Citation(page=4, text="flow")],
            status="answer",
        )


def test_manual_question_streams_a_spoken_answer(monkeypatch, talk_config):
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
    seen, new, answered, note = fake.calls[0]
    assert seen == [] and new[-1].endswith("我（手動輸入的問題）：flow 只在訓練時用嗎") and answered == []
    assert "不要輸出 SKIP 或 WAIT" in note


def test_lines_already_answered_are_sent_as_context_not_as_new(monkeypatch, talk_config):
    monkeypatch.setattr(config, "anthropic_key", lambda: "")
    fake = FakeAnswerer()
    with TestClient(app_module.app) as client:
        monkeypatch.setitem(app_module.state, "answerer", fake)
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.receive_json()  # hello
            for question in ("第一題", "第二題"):
                ws.send_json({"type": "ask", "text": question})
                while ws.receive_json()["type"] != "answer_done":
                    pass
    (_, first_new, _, _), (second_seen, second_new, second_answered, _) = fake.calls
    assert len(first_new) == 1 and first_new[0].endswith("第一題")
    assert len(second_seen) == 1 and second_seen[0].endswith("第一題")
    assert len(second_new) == 1 and second_new[0].endswith("第二題")
    assert second_answered == ["flow 只在訓練用嗎？"]


def test_init_creates_context_folder_and_launcher(tmp_path):
    import os

    import qa

    folder = qa.init(tmp_path / "talk")
    launcher = folder / "QA_Assistant.command"
    assert (folder / "sessions").is_dir() and os.access(launcher, os.X_OK)
    assert f'APP="{qa.HERE}"' in launcher.read_text(encoding="utf-8")
    assert 'title = "talk"' in (folder / "talk.toml").read_text(encoding="utf-8")
    (folder / "talk.toml").write_text('title = "kept"', encoding="utf-8")
    qa.init(tmp_path / "talk")  # running again keeps the user's talk.toml
    assert (folder / "talk.toml").read_text(encoding="utf-8") == 'title = "kept"'


def test_images_are_sent_only_for_figure_questions():
    assert answerer.needs_images(["[00:30] 聽眾：Fig. 5 那個失敗的例子錯在哪？"])
    assert answerer.needs_images(["生成的畫面看起來真實嗎"])
    assert not answerer.needs_images(["Table 2 的 Jaccard 是多少", "那張表的數字"])  # tables are in the text
    assert not answerer.needs_images(["optical flow 只在訓練的時候用嗎？", "表現有比較好嗎"])
