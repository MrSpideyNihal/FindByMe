from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from findbyme.config import (
    CONFIG_FILE,
    Character,
    Project,
    ProviderConfig,
    RenderConfig,
    default_providers,
    detect,
    find_root,
    resolve,
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


def test_new_projects_resolve_providers_at_run_time():
    providers = default_providers()
    assert {p["provider"] for p in providers.model_dump().values()} == {"auto"}

    keys = {"OPENAI_API_KEY": "x", "FAL_KEY": "y"}
    assert resolve("llm", providers.llm, keys).provider == "openai"
    assert resolve("tts", providers.tts, keys).provider == "openai"
    assert resolve("video", providers.video, keys).provider == "fal"
    assert resolve("video", providers.video, {}).provider == "puppet"
    assert resolve("llm", providers.llm, {**keys, "FINDBYME_OFFLINE": "1"}).provider == "mock"


def test_auto_keeps_user_model_and_options():
    cfg = ProviderConfig(provider="auto", model="gpt-5", options={"temperature": 0.1})
    out = resolve("llm", cfg, {"OPENAI_API_KEY": "x"})
    assert (out.provider, out.model, out.options) == ("openai", "gpt-5", {"temperature": 0.1})


def test_tts_falls_back_to_free_edge_voices():
    assert detect("tts", {}).provider == "edge"


def test_v1_all_mock_project_migrates_to_auto(tmp_path):
    project = make_project(tmp_path)
    project.save()
    data = yaml.safe_load((tmp_path / CONFIG_FILE).read_text())
    data["version"] = 1
    for entry in data["providers"].values():
        entry["provider"] = "mock"
    (tmp_path / CONFIG_FILE).write_text(yaml.safe_dump(data))

    loaded = Project.load(tmp_path)
    assert loaded.providers.llm.provider == "auto"
    assert yaml.safe_load((tmp_path / CONFIG_FILE).read_text())["version"] == 2


def test_v2_explicit_mock_is_kept(tmp_path):
    project = make_project(tmp_path)
    project.providers.llm.provider = "mock"
    project.save()
    assert Project.load(tmp_path).providers.llm.provider == "mock"


def test_helpers():
    assert slugify("Grand Ma!") == "grand-ma"
    assert stt_language("hinglish") == "hi"
    assert stt_language("en") == "en"
    assert stt_language("custom-dialect") is None
