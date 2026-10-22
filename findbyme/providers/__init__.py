"""Build providers from ``findbyme.yaml`` entries.

Built-in names are listed below. Any other ``provider`` value of the form
``package.module:ClassName`` is imported and constructed with ``(model, options)``,
so custom or private models plug in without forking FindByMe.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from ..config import ProviderConfig, Providers
from .base import LLM, STT, TTS, AnimateRequest, ProviderError, VideoGen, Word
from .llm import OPENAI_COMPATIBLE, AnthropicLLM, MockLLM, OpenAICompatibleLLM
from .stt import MockSTT, OpenAIWhisper
from .tts import ElevenLabsTTS, MockTTS, OpenAITTS
from .video import FalVideo, MockVideo, ReplicateVideo

__all__ = [
    "LLM", "STT", "TTS", "VideoGen", "AnimateRequest", "ProviderError", "Word",
    "Stack", "build", "BUILTIN",
]

Factory = Callable[[str, dict[str, Any]], Any]

BUILTIN: dict[str, dict[str, Factory]] = {
    "llm": {
        **{name: (lambda m, o, n=name: OpenAICompatibleLLM(n, m, o)) for name in OPENAI_COMPATIBLE},
        "openai-compatible": lambda m, o: OpenAICompatibleLLM("openai-compatible", m, o),
        "anthropic": AnthropicLLM,
        "mock": MockLLM,
    },
    "tts": {
        "openai": OpenAITTS,
        "elevenlabs": ElevenLabsTTS,
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


class Stack:
    """The four providers a render uses, built once per run."""

    def __init__(self, providers: Providers):
        self.llm: LLM = build("llm", providers.llm)
        self.tts: TTS = build("tts", providers.tts)
        self.stt: STT = build("stt", providers.stt)
        self.video: VideoGen = build("video", providers.video)

    def describe(self) -> dict[str, str]:
        return {
            "llm": self.llm.identity,
            "tts": self.tts.identity,
            "stt": self.stt.identity,
            "video": self.video.identity,
        }
