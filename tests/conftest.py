"""A tiny fake talk, so tests never need a real paper, script or deck."""

from pathlib import Path

import pytest

DECK = """
const Cover: Page = () => <Header title="Cover page" />;
const Method: Page = () => <Header title="Dual-prediction loss" />;
const Extra: Page = () => <div>no title here</div>;
export default [
  Cover,
  Method,
  Extra,
] satisfies Page[];
"""


@pytest.fixture(autouse=True)
def no_jev(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never call the real Jev API, even when the local .env has a key."""
    import config

    monkeypatch.setattr(config, "typesafe_key", lambda: "")


@pytest.fixture
def talk_dir(tmp_path: Path) -> Path:
    """A project with a qa_context/ folder holding one of each kind of file."""
    from PIL import Image

    ctx = tmp_path / "project" / "qa_context"
    (ctx / "figures").mkdir(parents=True)
    (ctx / "paper.paper.txt").write_text(
        "Abstract. We add optical flow.\f\f3.2 Flow is used only during training.\f", encoding="utf-8"
    )
    (ctx / "script.md").write_text("# 講稿\n第 1 頁：大家好", encoding="utf-8")
    (ctx / "outline.txt").write_text("## Q&A 預備\n- flow 只在訓練用", encoding="utf-8")
    (ctx / "index.tsx").write_text(DECK, encoding="utf-8")
    Image.new("RGB", (3000, 1000), "white").save(ctx / "figures" / "table2.png")
    (ctx / "sessions").mkdir()
    (ctx / "sessions" / "old_transcript.txt").write_text("不該被讀進去", encoding="utf-8")
    (ctx / "talk.toml").write_text(
        """
title = "TestPaper（Doe et al., 2026）"
speaker = "測試講者"
venue = "測試研討會"
body_pages = 2
vocabulary = ["TestPaper"]
""",
        encoding="utf-8",
    )
    return ctx


@pytest.fixture
def talk_config(talk_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QA_CONTEXT", str(talk_dir))
    return talk_dir
