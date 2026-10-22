from pathlib import Path

import pytest
from pydantic import ValidationError

from findbyme.config import (
    CONFIG_FILE,
    Character,
    Project,
    RenderConfig,
    default_providers,
    find_root,
    slugify,
    stt_language,
)


def make_project(tmp_path: Path) -> Project:
    project = Project(
        language="hinglish",
        characters=[
            Character(id="mom", name="Priya", role="mother", image="cast/mom/ref.png"),
            Character(id="kid", name="Aarav", role="son", image="cast/kid/ref.png"),
        ],
        providers=default_providers({}),
    )
    project._root = tmp_path
    return project


def test_save_and_load_roundtrip(tmp_path):
    project = make_project(tmp_path)
    project.save()

    loaded = Project.load(tmp_path)
    assert loaded.model_dump() == project.model_dump()
    assert loaded.root == tmp_path.resolve()


def test_find_root_walks_up(tmp_path):
    make_project(tmp_path).save()
    nested = tmp_path / "runs" / "a"
    nested.mkdir(parents=True)
    assert find_root(nested) == tmp_path.resolve()


def test_find_root_without_project(tmp_path):
    with pytest.raises(FileNotFoundError, match=CONFIG_FILE):
        find_root(tmp_path)


def test_duplicate_character_ids_rejected():
    char = Character(id="mom", name="A", image="a.png")
    with pytest.raises(ValidationError, match="unique"):
        Project(characters=[char, char], providers=default_providers({}))


def test_bad_character_id_rejected():
    with pytest.raises(ValidationError):
        Character(id="Mom Here", name="A", image="a.png")


def test_voice_sheet_falls_back_to_default(tmp_path):
    project = make_project(tmp_path)
    sheet = project.voice_sheet(project.characters[0])
    assert "exactly as written" in sheet

    (tmp_path / "voice.md").write_text("Fast, cheeky, Delhi accent.", "utf-8")
    project.characters[0].voice_sheet = "voice.md"
    assert project.voice_sheet(project.characters[0]) == "Fast, cheeky, Delhi accent."


@pytest.mark.parametrize(
    ("aspect", "expected"),
    [("9:16", (1080, 1920)), ("16:9", (1920, 1080)), ("1:1", (1080, 1080)), ("4:5", (1080, 1350))],
)
def test_render_size(aspect, expected):
    assert RenderConfig(aspect=aspect).size == expected


def test_default_providers_follow_keys():
    offline = default_providers({})
    assert {p["provider"] for p in offline.model_dump().values()} == {"mock"}

    online = default_providers({"OPENAI_API_KEY": "x", "FAL_KEY": "y"})
    assert online.llm.provider == "openai"
    assert online.tts.provider == "openai"
    assert online.video.provider == "fal"


def test_helpers():
    assert slugify("Grand Ma!") == "grand-ma"
    assert stt_language("hinglish") == "hi"
    assert stt_language("en") == "en"
    assert stt_language("custom-dialect") is None
