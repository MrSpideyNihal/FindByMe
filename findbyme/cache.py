"""Content-addressed cache for expensive stage outputs.

A voice take or an animated shot is keyed by everything that could change it: provider
identity, model, text, voice direction, reference image bytes. Re-rendering after an edit
only pays for the lines that actually changed.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def key(*parts: object) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:24]


class Cache:
    def __init__(self, root: Path, enabled: bool = True):
        self.root = root
        self.enabled = enabled

    def path(self, kind: str, digest: str, suffix: str) -> Path:
        folder = self.root / kind / digest[:2]
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{digest}{suffix}"

    def hit(self, kind: str, digest: str, suffix: str) -> Path | None:
        if not self.enabled:
            return None
        candidate = self.path(kind, digest, suffix)
        return candidate if candidate.exists() and candidate.stat().st_size > 0 else None


def place(src: Path, dest: Path) -> Path:
    """Put a cached file into a run folder without copying when the filesystem allows."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)
    return dest
