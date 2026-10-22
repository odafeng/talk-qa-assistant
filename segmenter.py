"""Group live transcript pieces into utterances that can be answered as one question."""

import re
from dataclasses import dataclass, field

# A pause this long after the last transcript activity ends the utterance.
SILENCE_SECONDS = 2.0
# Very short pieces ("好", "OK", "謝謝") are not worth an answer.
MIN_CHARS = 4
# A long monologue is cut so an answer can start before the speaker finishes.
MAX_CHARS = 400


def _content_length(text: str) -> int:
    return len(re.sub(r"[\s\W_]+", "", text))


@dataclass
class Segmenter:
    silence: float = SILENCE_SECONDS
    min_chars: int = MIN_CHARS
    max_chars: int = MAX_CHARS
    _parts: list[str] = field(default_factory=list)
    _last_activity: float = 0.0

    def add_final(self, text: str, now: float) -> str | None:
        """Record a final transcript piece; returns an utterance if it hit the length cap."""
        text = text.strip()
        if text:
            self._parts.append(text)
            self._last_activity = now
        if _content_length(self.pending) >= self.max_chars:
            return self._flush()
        return None

    def add_interim(self, now: float) -> None:
        """Interim text means someone is still talking, so the pause timer restarts."""
        self._last_activity = now

    def tick(self, now: float) -> str | None:
        """Return the finished utterance once the pause is long enough, else None."""
        if self._parts and now - self._last_activity >= self.silence:
            return self._flush()
        return None

    @property
    def last_activity(self) -> float:
        return self._last_activity

    @property
    def pending(self) -> str:
        return " ".join(self._parts)

    def _flush(self) -> str | None:
        text, self._parts = self.pending, []
        return text if _content_length(text) >= self.min_chars else None
