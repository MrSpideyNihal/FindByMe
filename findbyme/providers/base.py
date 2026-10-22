"""Provider interfaces.

A provider is any object with the right method. The pipeline only talks to these four
interfaces, so swapping OpenAI for Gemini, or one video model for another, never touches
pipeline code: it is a change to ``findbyme.yaml``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class AnimateRequest:
    image: Path
    audio: Path
    prompt: str
    duration: float
    size: tuple[int, int]
    aspect: str


@runtime_checkable
class LLM(Protocol):
    identity: str

    def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> str: ...


@runtime_checkable
class TTS(Protocol):
    identity: str

    def synthesize(
        self, text: str, *, voice: str, instructions: str, language: str, out: Path
    ) -> Path:
        """Write speech for ``text`` to ``out`` (any format ffmpeg reads) and return it."""
        ...


@runtime_checkable
class STT(Protocol):
    identity: str

    def transcribe(self, audio: Path, *, language: str | None, hint: str) -> list[Word]: ...


@runtime_checkable
class VideoGen(Protocol):
    identity: str

    def animate(self, request: AnimateRequest, out: Path) -> Path:
        """Animate the character in ``request.image`` speaking ``request.audio``."""
        ...


def make_identity(provider: str, model: str, options: dict[str, Any]) -> str:
    """Stable id used in cache keys: same provider, model and options, same id."""
    digest = hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()[:10]
    return f"{provider}:{model}:{digest}"
