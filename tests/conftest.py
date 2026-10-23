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
    (tmp_path / "paper.txt").write_text(
        "Abstract. We add optical flow.\f\f3.2 Flow is used only during training.\f", encoding="utf-8"
    )
    (tmp_path / "script.md").write_text("# 講稿\n第 1 頁：大家好", encoding="utf-8")
    (tmp_path / "outline.md").write_text("## Q&A 預備\n- flow 只在訓練用", encoding="utf-8")
    (tmp_path / "index.tsx").write_text(DECK, encoding="utf-8")
    (tmp_path / "talk.toml").write_text(
        """
title = "TestPaper（Doe et al., 2026）"
speaker = "測試講者"
venue = "測試研討會"
paper = "paper.txt"
notes = ["script.md", "outline.md"]
slides = "index.tsx"
body_pages = 2
vocabulary = ["TestPaper"]
sessions = "sessions"
""",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def talk_config(talk_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = talk_dir / "talk.toml"
    monkeypatch.setenv("TALK_CONFIG", str(path))
    return path
