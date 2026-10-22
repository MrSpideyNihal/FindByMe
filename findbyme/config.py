"""Project configuration: the cast, the language, the providers and render settings.

Everything a render depends on lives in ``findbyme.yaml`` at the project root, so the
same project renders the same way no matter which agent or person drives it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

CONFIG_FILE = "findbyme.yaml"
CAST_DIR = "cast"
RUNS_DIR = "runs"
CACHE_DIR = ".findbyme-cache"

LANGUAGES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi (Devanagari script)",
    "hinglish": "Hinglish: casual Hindi and English mixed, Hindi words in Devanagari, "
    "English words in Latin script",
    "mr": "Marathi",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "ur": "Urdu",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Mandarin Chinese",
    "ar": "Arabic",
    "ru": "Russian",
    "id": "Indonesian",
    "tr": "Turkish",
}

# Speech-to-text needs an ISO-639-1 code; mixed languages are transcribed as their base.
_STT_CODES = {"hinglish": "hi"}

# Distinct default voices so a multi-character cast never sounds like one person.
DEFAULT_TTS_VOICES = ["coral", "ash", "sage", "verse", "ballad", "echo", "shimmer", "alloy"]

DEFAULT_VOICE_SHEET = """\
Speaks naturally and warmly, like a real person talking to someone they know.
Medium pace, short pauses between sentences, no announcer voice.
Reads the line exactly as written: no added, skipped or changed words.
"""

ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "4:5": (4, 5)}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "character"


def language_name(code: str) -> str:
    return LANGUAGES.get(code, code)


def stt_language(code: str) -> str | None:
    code = _STT_CODES.get(code, code)
    return code if len(code) == 2 else None


class Character(BaseModel):
    id: str
    name: str
    role: str = ""
    image: str
    voice_sheet: str | None = None
    tts_voice: str = "coral"

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _SLUG_RE.match(value):
            raise ValueError("id must be lowercase letters, digits and dashes (max 40)")
        return value


class ProviderConfig(BaseModel):
    provider: str
    model: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class Providers(BaseModel):
    llm: ProviderConfig
    tts: ProviderConfig
    stt: ProviderConfig
    video: ProviderConfig


class CaptionStyle(BaseModel):
    enabled: bool = True
    words_per_group: int = Field(3, ge=1, le=8)
    font: str = "Arial"
    size: int = Field(0, ge=0, description="0 picks a size from the output resolution")
    color: str = "#FFFFFF"
    highlight: str = "#FFD400"
    outline: str = "#000000"
    position: Literal["bottom", "middle", "top"] = "bottom"

    @field_validator("color", "highlight", "outline")
    @classmethod
    def _check_hex(cls, value: str) -> str:
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ValueError("colors must be #RRGGBB")
        return value.upper()


class RenderConfig(BaseModel):
    aspect: str = "9:16"
    short_side: int = Field(1080, ge=360, le=2160)
    fps: int = Field(30, ge=12, le=60)
    concurrency: int = Field(4, ge=1, le=32)
    gap_seconds: float = Field(0.15, ge=0, le=2)
    voice_takes: int = Field(2, ge=1, le=5, description="extra takes only if a take misreads")
    max_word_error: float = Field(0.15, ge=0, le=1)
    music: str | None = None
    music_volume: float = Field(0.12, ge=0, le=1)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)

    @field_validator("aspect")
    @classmethod
    def _check_aspect(cls, value: str) -> str:
        if value not in ASPECTS:
            raise ValueError(f"aspect must be one of {', '.join(ASPECTS)}")
        return value

    @property
    def size(self) -> tuple[int, int]:
        w, h = ASPECTS[self.aspect]
        scale = self.short_side / min(w, h)
        # libx264 needs even dimensions
        return (int(w * scale) // 2 * 2, int(h * scale) // 2 * 2)


class Project(BaseModel):
    version: int = 2
    language: str = "en"
    characters: list[Character]
    providers: Providers
    render: RenderConfig = Field(default_factory=RenderConfig)

    _root: Path = PrivateAttr(default_factory=Path.cwd)

    @model_validator(mode="after")
    def _unique_ids(self) -> Project:
        ids = [c.id for c in self.characters]
        if len(ids) != len(set(ids)):
            raise ValueError("character ids must be unique")
        if not ids:
            raise ValueError("a project needs at least one character")
        return self

    @property
    def root(self) -> Path:
        return self._root

    def path(self, relative: str | Path) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self._root / p

    def character(self, char_id: str) -> Character:
        for c in self.characters:
            if c.id == char_id:
                return c
        raise KeyError(f"no character with id {char_id!r}")

    def voice_sheet(self, char: Character) -> str:
        if char.voice_sheet:
            sheet = self.path(char.voice_sheet)
            if sheet.exists():
                return sheet.read_text(encoding="utf-8").strip()
        return DEFAULT_VOICE_SHEET.strip()

    @classmethod
    def load(cls, root: str | Path | None = None) -> Project:
        root = find_root(Path(root) if root else Path.cwd())
        data = yaml.safe_load((root / CONFIG_FILE).read_text(encoding="utf-8")) or {}
        migrated = _migrate(data)
        project = cls.model_validate(data)
        project._root = root
        if migrated:
            project.save()
        return project

    def save(self) -> Path:
        target = self._root / CONFIG_FILE
        data = self.model_dump(mode="json", exclude_none=True)
        target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), "utf-8")
        return target


def _migrate(data: dict[str, Any]) -> bool:
    """Version 1 wrote ``mock`` for every stage when setup ran without keys, which then
    never changed after keys were added. Those projects move to ``auto``."""
    if data.get("version", 1) >= 2:
        return False
    providers = data.get("providers") or {}
    if all((providers.get(k) or {}).get("provider") == "mock" for k in providers) and providers:
        for entry in providers.values():
            entry["provider"] = AUTO
    data["version"] = 2
    return True


def find_root(start: Path) -> Path:
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / CONFIG_FILE).exists():
            return candidate
    raise FileNotFoundError(
        f"no {CONFIG_FILE} found in {start} or its parents; run `findbyme init` first"
    )


AUTO = "auto"
OFFLINE_ENV = "FINDBYME_OFFLINE"
PROVIDER_KEYS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
    "ELEVENLABS_API_KEY", "FAL_KEY", "REPLICATE_API_TOKEN",
)


def default_providers(env: dict[str, str] | None = None) -> Providers:
    """New projects use ``auto`` everywhere: the provider is picked from the keys present
    when a video is made, so adding a key to ``.env`` later just works."""
    del env
    return Providers(
        llm=ProviderConfig(provider=AUTO),
        tts=ProviderConfig(provider=AUTO),
        stt=ProviderConfig(provider=AUTO),
        video=ProviderConfig(provider=AUTO),
    )


def _edge_available() -> bool:
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False
    return True


def detect(kind: str, env: dict[str, str] | None = None) -> ProviderConfig:
    """The best provider for one stage given the API keys in ``env``.

    Falls back to offline mocks, except for voice, which uses free Edge voices when no
    voice key is set. ``FINDBYME_OFFLINE=1`` forces mocks everywhere (tests, demos).
    """
    env = dict(os.environ) if env is None else env
    if env.get(OFFLINE_ENV):
        return ProviderConfig(provider="mock")
    has = {key: bool(env.get(key, "").strip()) for key in PROVIDER_KEYS}

    if kind == "llm":
        if has["ANTHROPIC_API_KEY"]:
            return ProviderConfig(provider="anthropic", model="claude-sonnet-5-5")
        if has["OPENAI_API_KEY"]:
            return ProviderConfig(provider="openai", model="gpt-4.1")
        if has["GEMINI_API_KEY"]:
            return ProviderConfig(provider="gemini", model="gemini-2.5-flash")
        if has["OPENROUTER_API_KEY"]:
            return ProviderConfig(provider="openrouter", model="openai/gpt-4.1")
        # No key: you or your AI agent write the script from a brief.
        return ProviderConfig(provider="manual")
    elif kind == "tts":
        if has["ELEVENLABS_API_KEY"]:
            return ProviderConfig(provider="elevenlabs", model="eleven_multilingual_v2")
        if has["OPENAI_API_KEY"]:
            return ProviderConfig(provider="openai", model="gpt-4o-mini-tts")
        if _edge_available():
            return ProviderConfig(provider="edge")
    elif kind == "stt":
        if has["OPENAI_API_KEY"]:
            return ProviderConfig(provider="openai", model="whisper-1")
    elif kind == "video":
        if has["FAL_KEY"]:
            return ProviderConfig(
                provider="fal",
                model="fal-ai/bytedance/omnihuman",
                options={
                    "inputs": {"image_url": "{image}", "audio_url": "{audio}"},
                    "output": "video.url",
                },
            )
        if has["REPLICATE_API_TOKEN"]:
            return ProviderConfig(
                provider="replicate",
                model="bytedance/omni-human",
                options={"inputs": {"image": "{image}", "audio": "{audio}"}, "output": ""},
            )
    return ProviderConfig(provider="mock")


def resolve(kind: str, cfg: ProviderConfig, env: dict[str, str] | None = None) -> ProviderConfig:
    """Turn an ``auto`` entry into a concrete one; a model or options set alongside
    ``auto`` still apply on top of the detected provider."""
    if cfg.provider != AUTO:
        return cfg
    found = detect(kind, env)
    return ProviderConfig(
        provider=found.provider,
        model=cfg.model or found.model,
        options={**found.options, **cfg.options},
    )
