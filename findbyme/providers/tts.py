"""Text-to-speech providers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import media
from .base import make_identity
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
