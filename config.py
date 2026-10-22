"""API keys: this folder's .env wins over the shell, so a stale exported key cannot override it."""

import os
from pathlib import Path

from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
LECTURE_ENV = Path.home() / "Desktop/Projects/lecture_live_notes/.env"


def _key(name: str, *env_files: Path) -> str:
    for path in env_files:
        if path.exists() and (value := (dotenv_values(path).get(name) or "").strip()):
            return value
    return os.getenv(name, "").strip()


def anthropic_key() -> str:
    return _key("ANTHROPIC_API_KEY", HERE / ".env")


def gemini_key() -> str:
    return _key("GEMINI_API_KEY", HERE / ".env", LECTURE_ENV)
