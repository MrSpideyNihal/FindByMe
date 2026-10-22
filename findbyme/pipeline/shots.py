"""Stage 4: animate the speaking character for every line."""

from __future__ import annotations

from pathlib import Path

from ..cache import Cache, file_digest, key, place
from ..config import Project
from ..plan import Line
from ..providers import AnimateRequest, VideoGen
from ..styles import Style
from .voice import LineAudio


def shot_prompt(project: Project, style: Style, line: Line) -> str:
    char = project.character(line.speaker)
    who = f"{char.name}, {char.role}" if char.role else char.name
    parts = [
        f"{who} speaks directly, lips in sync with the audio.",
        f"While talking: {line.action}." if line.action else "",
        f"Emotion: {line.emotion}.",
        f"Camera: {line.camera}, steady.",
        f"Setting: {style.look}" if style.look else "",
        "Natural hand gestures, head movement and posture shifts; never frozen. "
        "Keep the face, hair, clothes and body identical to the reference image.",
    ]
    return " ".join(p for p in parts if p)


def animate_line(
    project: Project,
    video: VideoGen,
    cache: Cache,
    style: Style,
    line: Line,
    audio: LineAudio,
    out: Path,
) -> Path:
    image = project.path(project.character(line.speaker).image)
    prompt = shot_prompt(project, style, line)
    settings = project.render
    digest = key(
        video.identity, file_digest(image), file_digest(audio.path), prompt,
        settings.size, settings.aspect,
    )
    cached = cache.hit("shots", digest, ".mp4")
    if cached is None:
        target = cache.path("shots", digest, ".mp4")
        partial = target.with_suffix(".part.mp4")
        video.animate(
            AnimateRequest(
                image=image,
                audio=audio.path,
                prompt=prompt,
                duration=audio.duration,
                size=settings.size,
                aspect=settings.aspect,
                action=line.action,
                emotion=line.emotion,
            ),
            partial,
        )
        # rename only once the file is complete, so a crash never leaves a bad cache entry
        partial.replace(target)
        cached = target
    return place(cached, out)
