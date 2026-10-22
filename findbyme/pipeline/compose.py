"""Stage 5: fit every shot to its line, join them, burn captions, mix music."""

from __future__ import annotations

from pathlib import Path

from .. import media
from ..config import RenderConfig

AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def segment(shot: Path, audio: Path, seconds: float, settings: RenderConfig, out: Path) -> Path:
    """One line, exactly ``seconds`` long: the shot trimmed or held on its last frame,
    with the chosen voice take as its only audio."""
    width, height = settings.size
    graph = (
        f"[0:v]{media.cover_filter(width, height)},fps={settings.fps},"
        f"tpad=stop_mode=clone:stop_duration={seconds:.3f},"
        f"trim=duration={seconds:.3f},setpts=PTS-STARTPTS,format=yuv420p[v];"
        f"[1:a]aresample=48000,apad,atrim=duration={seconds:.3f},asetpts=PTS-STARTPTS[a]"
    )
    media.ffmpeg(
        "-i", shot, "-i", audio, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(settings.fps),
        *AUDIO_ARGS, out,
    )
    return out


def concat(segments: list[Path], out: Path) -> Path:
    listing = out.parent / "segments.txt"
    listing.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in segments), encoding="utf-8"
    )
    media.ffmpeg("-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", out)
    return out


def finish(
    joined: Path,
    captions: Path | None,
    settings: RenderConfig,
    music: Path | None,
    out: Path,
) -> Path:
    """Burn captions and duck background music under the voices."""
    # libass needs a plain relative path; Windows drive letters break filter parsing.
    run_dir = out.parent
    args: list[str | Path] = ["-i", joined.name]
    if music:
        args += ["-stream_loop", "-1", "-i", music.resolve()]

    filters = []
    if captions:
        filters.append(f"[0:v]ass={captions.name}[v]")
    if music:
        filters.append(
            f"[1:a]volume={settings.music_volume}[m];[0:a]asplit=2[voice][key];"
            "[m][key]sidechaincompress=threshold=0.04:ratio=8:attack=20:release=400[duck];"
            "[voice][duck]amix=inputs=2:duration=first:normalize=0[a]"
        )
    if filters:
        args += ["-filter_complex", ";".join(filters)]
    args += ["-map", "[v]" if captions else "0:v", "-map", "[a]" if music else "0:a"]
    args += (
        ["-c:v", "libx264", "-preset", "medium", "-crf", "18"] if captions else ["-c:v", "copy"]
    )
    args += [*AUDIO_ARGS, "-movflags", "+faststart", "-shortest", out.name]
    media.ffmpeg(*args, cwd=run_dir)
    return out


def cover(video: Path, at: float, out: Path) -> Path:
    media.ffmpeg("-ss", f"{at:.2f}", "-i", video, "-frames:v", "1", "-q:v", "2", out)
    return out


def frame_sheet(video: Path, total: float, out: Path, tiles: tuple[int, int] = (4, 3)) -> Path:
    """Contact sheet of evenly spaced frames, for a quick visual check of the whole video."""
    cols, rows = tiles
    rate = (cols * rows) / max(total, 0.1)
    media.ffmpeg(
        "-i", video, "-vf", f"fps={rate:.4f},scale=360:-2,tile={cols}x{rows}",
        "-frames:v", "1", "-q:v", "3", out,
    )
    return out
