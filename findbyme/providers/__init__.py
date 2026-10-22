"""Build providers from ``findbyme.yaml`` entries.

Built-in names are listed below. Any other ``provider`` value of the form
``package.module:ClassName`` is imported and constructed with ``(model, options)``,
so custom or private models plug in without forking FindByMe.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from ..config import ProviderConfig, Providers, resolve
from .base import LLM, STT, TTS, AnimateRequest, ProviderError, VideoGen, Word
from .llm import (
    OPENAI_COMPATIBLE,
    AnthropicLLM,
    ManualLLM,
    MockLLM,
    OpenAICompatibleLLM,
    ScriptNeeded,
)
from .stt import MockSTT, OpenAIWhisper
from .tts import EdgeTTS, ElevenLabsTTS, MockTTS, OpenAITTS
from .video import FalVideo, MockVideo, ReplicateVideo

__all__ = [
    "LLM", "STT", "TTS", "VideoGen", "AnimateRequest", "ProviderError", "Word",
    "Stack", "build", "BUILTIN", "ScriptNeeded",
]

Factory = Callable[[str, dict[str, Any]], Any]

BUILTIN: dict[str, dict[str, Factory]] = {
    "llm": {
        **{name: (lambda m, o, n=name: OpenAICompatibleLLM(n, m, o)) for name in OPENAI_COMPATIBLE},
        "openai-compatible": lambda m, o: OpenAICompatibleLLM("openai-compatible", m, o),
        "anthropic": AnthropicLLM,
        "manual": ManualLLM,
        "mock": MockLLM,
    },
    "tts": {
        "openai": OpenAITTS,
        "elevenlabs": ElevenLabsTTS,
        "edge": EdgeTTS,
        "mock": MockTTS,
    },
    "stt": {
        "openai": OpenAIWhisper,
        "mock": MockSTT,
    },
    "video": {
        "fal": FalVideo,
        "replicate": ReplicateVideo,
        "mock": MockVideo,
    },
}


def build(kind: str, cfg: ProviderConfig) -> Any:
    cfg = resolve(kind, cfg)
    if ":" in cfg.provider:
        module_name, _, attr = cfg.provider.partition(":")
        try:
            factory = getattr(importlib.import_module(module_name), attr)
        except (ImportError, AttributeError) as exc:
            raise ProviderError(
                f"cannot load custom {kind} provider {cfg.provider!r}: {exc}"
            ) from exc
    else:
        try:
            factory = BUILTIN[kind][cfg.provider]
        except KeyError:
            known = ", ".join(sorted(BUILTIN[kind]))
            raise ProviderError(
                f"unknown {kind} provider {cfg.provider!r}; use one of {known} or module:Class"
            ) from None
    return factory(cfg.model, dict(cfg.options))


# What each stage looks like when it falls back to the offline mock.
PLACEHOLDER = {
    "llm": "the script is a fixed template, not written about your topic",
    "tts": "the voice is a beep",
    "stt": "caption timing is estimated",
    "video": "characters do not move; the reference image slowly pans",
}


class Stack:
    """The four providers a render uses, built once per run."""

    def __init__(self, providers: Providers):
        self.resolved = {
            kind: resolve(kind, getattr(providers, kind)) for kind in ("llm", "tts", "stt", "video")
        }
        self.llm: LLM = build("llm", providers.llm)
        self.tts: TTS = build("tts", providers.tts)
        self.stt: STT = build("stt", providers.stt)
        self.video: VideoGen = build("video", providers.video)

    def placeholders(self) -> dict[str, str]:
        """Stages running on mocks, with what that means for the video."""
        found = {k: PLACEHOLDER[k] for k, cfg in self.resolved.items() if cfg.provider == "mock"}
        # Edge voices report their own word timings, so a mock stt costs nothing then.
        if self.resolved["tts"].provider == "edge":
            found.pop("stt", None)
        return found

    def describe(self) -> dict[str, str]:
        return {
            "llm": self.llm.identity,
            "tts": self.tts.identity,
            "stt": self.stt.identity,
            "video": self.video.identity,
        }
