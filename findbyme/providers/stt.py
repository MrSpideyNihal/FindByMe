"""Speech-to-text with word timestamps, used to time captions and check voice takes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import media
from .base import Word, make_identity
from .http import api_key, request


class OpenAIWhisper:
    def __init__(self, model: str, options: dict[str, Any]):
        self.model = model or "whisper-1"
        self.base_url = options.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.key_env = options.get("api_key_env", "OPENAI_API_KEY")
        self.identity = make_identity("openai", self.model, options)

    def transcribe(self, audio: Path, *, language: str | None, hint: str) -> list[Word]:
        data: dict[str, Any] = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
            # Passing the script as the prompt keeps spellings and names consistent.
            "prompt": hint[:800],
        }
        if language:
            data["language"] = language
        with audio.open("rb") as fh:
            payload = request(
                "POST",
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key(self.key_env)}"},
                data=data,
                files={"file": (audio.name, fh, "audio/wav")},
            ).json()
        return [
            Word(text=w["word"].strip(), start=float(w["start"]), end=float(w["end"]))
            for w in payload.get("words", [])
            if w.get("word", "").strip()
        ]


class MockSTT:
    """Offline stand-in: spreads the hint's words across the clip, weighted by length."""

    identity = "mock:stt"

    def __init__(self, model: str = "", options: dict[str, Any] | None = None):
        # Mocks take the same constructor arguments as real providers.
        del model, options

    def transcribe(self, audio: Path, *, language: str | None, hint: str) -> list[Word]:
        tokens = hint.split()
        if not tokens:
            return []
        total = media.duration(audio)
        weights = [len(t) + 2 for t in tokens]
        scale = total / sum(weights)
        words, cursor = [], 0.0
        for token, weight in zip(tokens, weights, strict=True):
            end = cursor + weight * scale
            words.append(Word(text=token, start=round(cursor, 3), end=round(end, 3)))
            cursor = end
        return words
