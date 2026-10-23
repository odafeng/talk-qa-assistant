"""API keys: this folder's .env wins over the shell, so a stale exported key cannot override it."""

import os
from pathlib import Path

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parent / ".env"


def _key(name: str) -> str:
    if ENV_FILE.exists() and (value := (dotenv_values(ENV_FILE).get(name) or "").strip()):
        return value
    return os.getenv(name, "").strip()


def anthropic_key() -> str:
    return _key("ANTHROPIC_API_KEY")


def gemini_key() -> str:
    return _key("GEMINI_API_KEY")
