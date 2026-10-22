"""Thin wrappers around ffmpeg and ffprobe."""

from __future__ import annotations

import base64
import json
import mimetypes
import shutil
import subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise MediaError(
            f"{' and '.join(missing)} not found on PATH. Install ffmpeg: https://ffmpeg.org/download.html"
        )


def ffmpeg(*args: str | Path, cwd: Path | None = None) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise MediaError(f"ffmpeg failed ({result.returncode}):\n{tail}")


def duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MediaError(f"ffprobe could not read {path}: {result.stderr.strip()}")
    return float(json.loads(result.stdout)["format"]["duration"])


def has_audio(path: Path) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def to_wav(src: Path, out: Path, sample_rate: int = 48000) -> Path:
    """Normalise any audio file to mono PCM WAV so every later stage sees one format."""
    ffmpeg("-i", src, "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", out)
    return out


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def cover_filter(width: int, height: int) -> str:
    """Scale to fill the frame, then centre-crop: no letterboxing."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )
