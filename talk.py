"""The talk the assistant answers for: speaker, paper, notes and slides, read from a TOML file.

The file lives with the talk materials, outside this repo, so the repo never holds a paper,
a script or the speaker's details. See talk.example.toml.
"""

import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Talk:
    title: str
    speaker: str
    venue: str
    paper: Path
    notes: tuple[Path, ...]
    slides: Path | None
    body_pages: int | None
    vocabulary: tuple[str, ...]
    sessions: Path


def load(path: Path | None = None) -> Talk:
    path = (path or Path(os.getenv("TALK_CONFIG", "talk.toml"))).expanduser().resolve()
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    def resolve(value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (path.parent / p).resolve()

    slides = data.get("slides")
    return Talk(
        title=data["title"],
        speaker=data["speaker"],
        venue=data.get("venue", "研討會"),
        paper=resolve(data["paper"]),
        notes=tuple(resolve(n) for n in data.get("notes", [])),
        slides=resolve(slides) if slides else None,
        body_pages=data.get("body_pages"),
        vocabulary=tuple(data.get("vocabulary", [])),
        sessions=resolve(data.get("sessions", "qa_sessions")),
    )


def paper_pages(paper: Path) -> list[tuple[int, str]]:
    """(page number, text) for each non-empty page of a PDF, or of a .txt with form-feed page breaks."""
    if paper.suffix.lower() == ".pdf":
        result = subprocess.run(
            ["pdftotext", "-layout", str(paper), "-"], check=True, capture_output=True, text=True
        )
        text = result.stdout
    else:
        text = paper.read_text(encoding="utf-8")
    return [(n, page) for n, page in enumerate(text.split("\f"), 1) if page.strip()]
