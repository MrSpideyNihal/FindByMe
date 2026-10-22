"""Text-to-speech providers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .. import media
from ..config import DEFAULT_TTS_VOICES
from .base import ProviderError, make_identity
from .http import api_key, request


class OpenAITTS:
    def __init__(self, model: str, options: dict[str, Any]):
        self.model = model or "gpt-4o-mini-tts"
        self.base_url = options.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.speed = options.get("speed")
        self.identity = make_identity("openai", self.model, options)

    def synthesize(
        self, text: str, *, voice: str, instructions: str, language: str, out: Path
    ) -> Path:
        body: dict[str, Any] = {
            "model": self.model,
            "voice": voice,
            "input": text,
            "response_format": "wav",
        }
        # Only the gpt-* speech models accept free-form voice direction.
        if self.model.startswith("gpt-") and instructions:
            body["instructions"] = instructions
        if self.speed:
            body["speed"] = self.speed
        response = request(
            "POST",
            f"{self.base_url}/audio/speech",
            headers={"Authorization": f"Bearer {api_key('OPENAI_API_KEY')}"},
            json=body,
        )
        out.write_bytes(response.content)
        return out


class ElevenLabsTTS:
    """`tts_voice` on each character must be an ElevenLabs voice id."""

    def __init__(self, model: str, options: dict[str, Any]):
        self.model = model or "eleven_multilingual_v2"
        self.settings = options.get("voice_settings", {"stability": 0.45, "similarity_boost": 0.8})
        self.identity = make_identity("elevenlabs", self.model, options)

    def synthesize(
        self, text: str, *, voice: str, instructions: str, language: str, out: Path
    ) -> Path:
        response = request(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": api_key("ELEVENLABS_API_KEY")},
            json={"text": text, "model_id": self.model, "voice_settings": self.settings},
        )
        mp3 = out.with_suffix(".mp3")
        mp3.write_bytes(response.content)
        return mp3


class MockTTS:
    """Offline stand-in: a soft tone as long as the line would take to say."""

    identity = "mock:tts"
    seconds_per_word = 0.36

    def __init__(self, model: str = "", options: dict[str, Any] | None = None):
        # Mocks take the same constructor arguments as real providers.
        del model, options

    def synthesize(
        self, text: str, *, voice: str, instructions: str, language: str, out: Path
    ) -> Path:
        seconds = max(1.2, len(text.split()) * self.seconds_per_word)
        pitch = 180 + (sum(map(ord, voice)) % 8) * 25
        media.ffmpeg(
            "-f", "lavfi", "-i", f"sine=frequency={pitch}:duration={seconds:.2f}",
            "-af", f"volume=0.15,afade=t=in:d=0.05,afade=t=out:st={seconds - 0.1:.2f}:d=0.1",
            "-ar", "48000", "-ac", "1", out,
        )
        return out


# Free Microsoft Edge neural voices: (female, male) per language. No key needed.
EDGE_VOICES: dict[str, tuple[str, ...]] = {
    "en": (
        "en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural", "en-US-ChristopherNeural",
    ),
    "hi": ("hi-IN-SwaraNeural", "hi-IN-MadhurNeural"),
    "hinglish": ("hi-IN-SwaraNeural", "hi-IN-MadhurNeural"),
    "mr": ("mr-IN-AarohiNeural", "mr-IN-ManoharNeural"),
    "bn": ("bn-IN-TanishaaNeural", "bn-IN-BashkarNeural"),
    "ta": ("ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"),
    "te": ("te-IN-ShrutiNeural", "te-IN-MohanNeural"),
    "gu": ("gu-IN-DhwaniNeural", "gu-IN-NiranjanNeural"),
    "ur": ("ur-IN-GulNeural", "ur-IN-SalmanNeural"),
    "es": ("es-ES-ElviraNeural", "es-MX-JorgeNeural"),
    "pt": ("pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"),
    "fr": ("fr-FR-DeniseNeural", "fr-FR-HenriNeural"),
    "de": ("de-DE-KatjaNeural", "de-DE-ConradNeural"),
    "it": ("it-IT-ElsaNeural", "it-IT-DiegoNeural"),
    "ja": ("ja-JP-NanamiNeural", "ja-JP-KeitaNeural"),
    "ko": ("ko-KR-SunHiNeural", "ko-KR-InJoonNeural"),
    "zh": ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"),
    "ar": ("ar-SA-ZariyahNeural", "ar-SA-HamedNeural"),
    "ru": ("ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"),
    "id": ("id-ID-GadisNeural", "id-ID-ArdiNeural"),
    "tr": ("tr-TR-EmelNeural", "tr-TR-AhmetNeural"),
}


def edge_voice(voice: str, language: str) -> str:
    """Use ``voice`` if it is already an Edge voice; otherwise map the character's default
    voice slot to a voice for the language, so two characters never share a voice."""
    if voice.endswith("Neural"):
        return voice
    pool = EDGE_VOICES.get(language, EDGE_VOICES["en"])
    if voice in DEFAULT_TTS_VOICES:
        slot = DEFAULT_TTS_VOICES.index(voice)
    else:
        slot = sum(map(ord, voice))
    return pool[slot % len(pool)]


def words_sidecar(audio: Path) -> Path:
    """Where a TTS provider that knows its own word timings writes them."""
    return audio.with_name(audio.name + ".words.json")


class EdgeTTS:
    """Free neural voices from Microsoft Edge's read-aloud service, with word timings.

    Set a character's ``tts_voice`` to any Edge voice name (``edge-tts --list-voices``)
    to choose one; otherwise a voice is picked for the project language.
    """

    def __init__(self, model: str = "", options: dict[str, Any] | None = None):
        options = options or {}
        self.rate = options.get("rate", "+0%")
        self.identity = make_identity("edge", model, options)

    def synthesize(
        self, text: str, *, voice: str, instructions: str, language: str, out: Path
    ) -> Path:
        try:
            import edge_tts
        except ImportError as exc:
            raise ProviderError("edge voice needs edge-tts: pip install edge-tts") from exc

        name = edge_voice(voice, language)
        mp3 = out.with_suffix(".mp3")

        async def run() -> list[dict[str, float | str]]:
            words: list[dict[str, float | str]] = []
            stream = edge_tts.Communicate(text, name, rate=self.rate, boundary="WordBoundary")
            with mp3.open("wb") as fh:
                async for chunk in stream.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        start = chunk["offset"] / 1e7
                        words.append({
                            "text": chunk["text"],
                            "start": round(start, 3),
                            "end": round(start + chunk["duration"] / 1e7, 3),
                        })
            return words

        try:
            words = asyncio.run(run())
        except Exception as exc:  # noqa: BLE001 - network and service errors vary by version
            raise ProviderError(f"edge voice {name} failed: {exc}") from exc
        if not mp3.exists() or mp3.stat().st_size == 0:
            raise ProviderError(f"edge voice {name} returned no audio")
        words_sidecar(mp3).write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        return mp3
