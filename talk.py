"""The talk the assistant answers for, discovered from one folder (qa_context/) in the user's project.

Everything dropped into the folder is used, sorted by type:
- *.pdf and *.paper.txt (pre-extracted text, form feed between pages): papers
- *.md and other *.txt: the speaker's notes (script, outline, Q&A prep)
- *.png / *.jpg / *.jpeg / *.webp / *.gif: images (figures, tables, screenshots), named by filename
- *.tsx: an open-slide deck, for "turn to slide N"
- talk.toml (optional): title, speaker, venue, vocabulary, body_pages, extra slides path

The folder lives in the user's project, never in this repo (ADR 0003, 0005).
"""

import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG = "talk.toml"
SESSIONS = "sessions"
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass(frozen=True)
class Talk:
    folder: Path
    title: str
    speaker: str
    venue: str
    papers: tuple[Path, ...]
    notes: tuple[Path, ...]
    images: tuple[Path, ...]
    slides: Path | None
    body_pages: int | None
    vocabulary: tuple[str, ...]

    @property
    def sessions(self) -> Path:
        return self.folder / SESSIONS


def _files(folder: Path) -> list[Path]:
    """Every file under the folder except the session logs and hidden files, in a stable order."""
    out = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if rel.parts[0] == SESSIONS or any(p.startswith(".") for p in rel.parts):
            continue
        if path.is_file():
            out.append(path)
    return out


def load(folder: Path | None = None) -> Talk:
    folder = (folder or Path(os.getenv("QA_CONTEXT", "qa_context"))).expanduser().resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"找不到參考資料夾 {folder}（用 QA_CONTEXT 指定，或執行 qa.py init 建立）")
    config_path = folder / CONFIG
    data = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}

    papers, notes, images, decks = [], [], [], []
    for path in _files(folder):
        name, suffix = path.name.lower(), path.suffix.lower()
        if suffix == ".pdf" or name.endswith(".paper.txt"):
            papers.append(path)
        elif suffix in IMAGE_TYPES:
            images.append(path)
        elif suffix == ".tsx":
            decks.append(path)
        elif suffix in (".md", ".txt"):
            notes.append(path)

    slides = data.get("slides")
    if slides:
        slides_path = Path(slides).expanduser()
        decks.insert(0, slides_path if slides_path.is_absolute() else (folder / slides_path).resolve())
    return Talk(
        folder=folder,
        title=data.get("title", folder.parent.name),
        speaker=data.get("speaker", "講者"),
        venue=data.get("venue", "研討會"),
        papers=tuple(papers),
        notes=tuple(notes),
        images=tuple(images),
        slides=decks[0] if decks else None,
        body_pages=data.get("body_pages"),
        vocabulary=tuple(data.get("vocabulary", [])),
    )


def signature(folder: Path) -> tuple:
    """Changes whenever a file in the folder is added, removed or edited (used to reload live)."""
    return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in _files(folder))


def paper_pages(paper: Path) -> list[tuple[int, str]]:
    """(page number, text) for each non-empty page of a PDF, or of a .paper.txt with form feeds."""
    if paper.suffix.lower() == ".pdf":
        result = subprocess.run(
            ["pdftotext", "-layout", str(paper), "-"], check=True, capture_output=True, text=True
        )
        text = result.stdout
    else:
        text = paper.read_text(encoding="utf-8")
    return [(n, page) for n, page in enumerate(text.split("\f"), 1) if page.strip()]
