"""One-time project setup, shared by the CLI wizard and the web UI."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import (
    CAST_DIR,
    CONFIG_FILE,
    DEFAULT_TTS_VOICES,
    Character,
    Project,
    default_providers,
    slugify,
)

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}

ENV_EXAMPLE = """\
# Fill in the keys for the providers you use; leave the rest empty.
# Without any keys FindByMe runs on offline mock providers.
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
OPENROUTER_API_KEY=
ELEVENLABS_API_KEY=
FAL_KEY=
REPLICATE_API_TOKEN=
"""


class SetupError(ValueError):
    pass


@dataclass
class CastMember:
    name: str
    image: Path
    role: str = ""
    voice_sheet: str = ""
    """Voice sheet text. Empty means the built-in default."""
    tts_voice: str = ""


def _unique_id(name: str, taken: set[str]) -> str:
    base = slugify(name)
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _store(root: Path, member: CastMember, taken: set[str], index: int) -> Character:
    """Copy the reference image and voice sheet into cast/<id>/ and describe the character."""
    image = member.image.expanduser()
    if not image.is_file():
        raise SetupError(f"image not found: {image}")
    if image.suffix.lower() not in IMAGE_TYPES:
        raise SetupError(f"{image.name}: use one of {', '.join(sorted(IMAGE_TYPES))}")
    if not member.name.strip():
        raise SetupError("every character needs a name")

    char_id = _unique_id(member.name, taken)
    folder = root / CAST_DIR / char_id
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / f"reference{image.suffix.lower()}"
    shutil.copyfile(image, stored)

    sheet_rel = None
    if member.voice_sheet.strip():
        (folder / "voice.md").write_text(member.voice_sheet.strip() + "\n", encoding="utf-8")
        sheet_rel = f"{CAST_DIR}/{char_id}/voice.md"

    return Character(
        id=char_id,
        name=member.name.strip(),
        role=member.role.strip(),
        image=f"{CAST_DIR}/{char_id}/{stored.name}",
        voice_sheet=sheet_rel,
        tts_voice=member.tts_voice or DEFAULT_TTS_VOICES[index % len(DEFAULT_TTS_VOICES)],
    )


def add_character(project: Project, member: CastMember) -> Character:
    taken = {c.id for c in project.characters}
    char = _store(project.root, member, taken, len(project.characters))
    project.characters.append(char)
    project.save()
    return char


def remove_character(project: Project, char_id: str) -> None:
    char = project.character(char_id)
    if len(project.characters) == 1:
        raise SetupError("a project needs at least one character")
    project.characters.remove(char)
    project.save()
    shutil.rmtree(project.path(CAST_DIR) / char_id, ignore_errors=True)


def create_project(
    root: Path, cast: list[CastMember], language: str = "en", aspect: str = "9:16"
) -> Project:
    root = root.resolve()
    if (root / CONFIG_FILE).exists():
        raise SetupError(f"{root / CONFIG_FILE} already exists")
    if not cast:
        raise SetupError("add at least one character")
    root.mkdir(parents=True, exist_ok=True)

    characters: list[Character] = []
    for index, member in enumerate(cast):
        characters.append(_store(root, member, {c.id for c in characters}, index))

    project = Project(language=language, characters=characters, providers=default_providers())
    project.render.aspect = aspect
    project._root = root
    project.save()

    env_example = root / ".env.example"
    if not env_example.exists():
        env_example.write_text(ENV_EXAMPLE, encoding="utf-8")
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".env\nruns/\n.findbyme-cache/\n", encoding="utf-8")
    return project


def read_sheet_input(value: str) -> str:
    """A voice sheet answer may be a path to a text file or the description itself."""
    value = value.strip()
    if not value:
        return ""
    candidate = Path(value.strip('"')).expanduser()
    if len(value) < 260 and candidate.suffix.lower() in {".md", ".txt"}:
        if not candidate.is_file():
            raise SetupError(f"voice sheet file not found: {candidate}")
        return candidate.read_text(encoding="utf-8")
    return value


def set_option(project: Project, dotted: str, raw: str) -> Project:
    """Set ``render.aspect`` or ``providers.video.model`` style paths; the value is YAML."""
    data = project.model_dump(mode="json")
    node = data
    *parents, leaf = dotted.split(".")
    for part in parents:
        if not isinstance(node, dict) or part not in node:
            raise SetupError(f"no setting named {dotted!r}")
        node = node[part]
    if not isinstance(node, dict):
        raise SetupError(f"no setting named {dotted!r}")
    if isinstance(node.get(leaf), str):
        # YAML would read "16:9" as the base-60 number 969; text settings stay text.
        node[leaf] = raw
    else:
        node[leaf] = yaml.safe_load(raw) if raw.strip() else None
    updated = Project.model_validate(data)
    updated._root = project.root
    updated.save()
    return updated
