"""Video style presets.

Built-in presets ship in ``findbyme/styles``. A project can add its own or override a
built-in by dropping ``<name>.yaml`` into ``styles/`` next to ``findbyme.yaml``.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

BUILTIN_DIR = Path(__file__).parent / "styles"


class LineRange(BaseModel):
    min: int = Field(4, ge=1)
    max: int = Field(12, ge=1)


class Style(BaseModel):
    name: str
    description: str = ""
    seconds: int = Field(45, ge=5, le=600)
    lines: LineRange = Field(default_factory=LineRange)
    tone: str = ""
    structure: list[str] = Field(default_factory=list)
    look: str = ""


def _dirs(project_root: Path | None) -> list[Path]:
    dirs = [BUILTIN_DIR]
    if project_root is not None:
        dirs.append(project_root / "styles")
    return dirs


def available(project_root: Path | None = None) -> dict[str, Style]:
    """All styles by name; project styles win over built-ins with the same name."""
    found: dict[str, Style] = {}
    for directory in _dirs(project_root):
        for file in sorted(directory.glob("*.yaml")):
            data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
            data.setdefault("name", file.stem)
            found[data["name"]] = Style.model_validate(data)
    return found


def load(name: str, project_root: Path | None = None) -> Style:
    styles = available(project_root)
    if name not in styles:
        raise KeyError(f"unknown style {name!r}; available: {', '.join(sorted(styles))}")
    return styles[name]
