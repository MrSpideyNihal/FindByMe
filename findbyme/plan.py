"""The script contract between the writer model and the renderer.

Whatever model writes the script, it must produce this shape, and it is validated before
anything is rendered. ``plan.json`` is saved in the run folder; edit it by hand and
re-render, and the result no longer depends on any model at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from .config import Project

PLAN_FILE = "plan.json"


class Line(BaseModel):
    speaker: str = Field(description="character id")
    text: str = Field(min_length=1, max_length=400)
    emotion: str = "neutral"
    action: str = Field("", description="visible body movement and gestures while speaking")
    camera: str = "medium shot"

    @field_validator("text")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("line text is empty")
        return value


class Plan(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    topic: str = ""
    style: str = ""
    language: str = "en"
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    lines: list[Line] = Field(min_length=1, max_length=80)

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, tags: list[str]) -> list[str]:
        return [t.strip().lstrip("#").replace(" ", "") for t in tags if t.strip()][:10]

    def check_cast(self, project: Project) -> None:
        known = {c.id for c in project.characters}
        unknown = sorted({line.speaker for line in self.lines} - known)
        if unknown:
            raise ValueError(
                f"script uses unknown speakers {unknown}; cast is {sorted(known)}"
            )

    def save(self, run_dir: Path) -> Path:
        target = run_dir / PLAN_FILE
        target.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, run_dir: Path) -> Plan:
        return cls.model_validate(json.loads((run_dir / PLAN_FILE).read_text(encoding="utf-8")))


def schema_hint() -> str:
    """Compact description of the JSON the writer must return."""
    return json.dumps(
        {
            "title": "short title",
            "caption": "one-sentence post caption",
            "hashtags": ["five", "relevant", "hashtags", "no", "hash"],
            "lines": [
                {
                    "speaker": "<character id>",
                    "text": "exactly what they say",
                    "emotion": "one word: happy, curious, surprised, serious, ...",
                    "action": "visible movement while speaking, hands and body",
                    "camera": "medium shot | medium close-up | close-up | wide",
                }
            ],
        },
        indent=2,
    )
