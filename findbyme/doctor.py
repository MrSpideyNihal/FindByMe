"""Pre-flight checks: everything a render needs, reported before money is spent."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from .config import Project, ProviderConfig
from .providers import ProviderError, build
from .providers.llm import OPENAI_COMPATIBLE

KEY_FOR = {
    "anthropic": "ANTHROPIC_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "fal": "FAL_KEY",
    "replicate": "REPLICATE_API_TOKEN",
    **{name: env for name, (_, env) in OPENAI_COMPATIBLE.items()},
}


@dataclass(frozen=True)
class Check:
    ok: bool
    name: str
    detail: str


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
        cfg: ProviderConfig = getattr(project.providers, kind)
        label = f"{kind}: {cfg.provider}" + (f" ({cfg.model})" if cfg.model else "")
        env = required_key(cfg)
        if env and not os.environ.get(env):
            checks.append(Check(False, label, f"{env} is not set"))
            continue
        try:
            build(kind, cfg)
        except ProviderError as exc:
            checks.append(Check(False, label, str(exc)))
            continue
        note = "offline mock, output is a placeholder" if cfg.provider == "mock" else "ready"
        checks.append(Check(True, label, note))

    if project.render.music:
        music = project.path(project.render.music)
        checks.append(Check(music.is_file(), "music", str(project.render.music)))
    return checks
