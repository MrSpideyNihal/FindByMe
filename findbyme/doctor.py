"""Pre-flight checks: everything a render needs, reported before money is spent."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from .config import AUTO, Project, ProviderConfig, resolve
from .providers import PLACEHOLDER, ProviderError, build
from .providers.llm import OPENAI_COMPATIBLE

KEY_FOR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "fal": "FAL_KEY",
    "replicate": "REPLICATE_API_TOKEN",
    **{name: env for name, (_, env) in OPENAI_COMPATIBLE.items()},
}

# Keys that would replace a placeholder stage with a real one.
UNLOCKS = {
    "llm": "ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY",
    "tts": "OPENAI_API_KEY or ELEVENLABS_API_KEY (or pip install edge-tts for free voices)",
    "stt": "OPENAI_API_KEY",
    "video": "FAL_KEY or REPLICATE_API_TOKEN",
}


@dataclass(frozen=True)
class Check:
    ok: bool
    name: str
    detail: str
    warn: bool = False
    """Renders fine, but part of the output will be a placeholder."""


def required_key(cfg: ProviderConfig) -> str:
    return cfg.options.get("api_key_env") or KEY_FOR.get(cfg.provider, "")


def run(project: Project) -> list[Check]:
    checks = [
        Check(bool(shutil.which("ffmpeg") and shutil.which("ffprobe")), "ffmpeg",
              "found" if shutil.which("ffmpeg") else "install from https://ffmpeg.org"),
    ]
    for char in project.characters:
        image = project.path(char.image)
        checks.append(Check(image.is_file(), f"cast/{char.id}", str(char.image)))

    for kind in ("llm", "tts", "stt", "video"):
        configured: ProviderConfig = getattr(project.providers, kind)
        cfg = resolve(kind, configured)
        name = f"auto: {cfg.provider}" if configured.provider == AUTO else cfg.provider
        label = f"{kind}: {name}" + (f" ({cfg.model})" if cfg.model else "")
        env = required_key(cfg)
        if env and not os.environ.get(env):
            checks.append(Check(False, label, f"{env} is not set"))
            continue
        try:
            build(kind, cfg)
        except ProviderError as exc:
            checks.append(Check(False, label, str(exc)))
            continue
        if cfg.provider == "mock":
            if kind == "stt" and resolve("tts", project.providers.tts).provider == "edge":
                checks.append(Check(True, label, "not needed: edge voices give word timings"))
                continue
            checks.append(Check(
                True, label, f"PLACEHOLDER, {PLACEHOLDER[kind]}. Add {UNLOCKS[kind]} to .env",
                warn=True,
            ))
            continue
        checks.append(Check(True, label, "ready"))

    if project.render.music:
        music = project.path(project.render.music)
        checks.append(Check(music.is_file(), "music", str(project.render.music)))
    return checks
