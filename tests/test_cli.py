from pathlib import Path

import pytest
from typer.testing import CliRunner

from findbyme.cli import app
from findbyme.config import Project
from findbyme.setup import CastMember, SetupError, create_project, read_sheet_input, set_option

from .conftest import needs_ffmpeg

runner = CliRunner()

PROVIDER_KEYS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY",
    "FAL_KEY", "REPLICATE_API_TOKEN",
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def workdir(tmp_path, monkeypatch) -> Path:
    project = tmp_path / "videos"
    project.mkdir()
    monkeypatch.chdir(project)
    return project


def test_create_project_copies_cast(tmp_path, portrait):
    project = create_project(
        tmp_path / "p",
        [
            CastMember("Priya", portrait, role="mother", voice_sheet="Warm, slow, Mumbai accent."),
            CastMember("Priya", portrait, role="aunt"),
        ],
        language="hi",
    )
    ids = [c.id for c in project.characters]
    assert ids == ["priya", "priya-2"]
    assert project.path(project.characters[0].image).is_file()
    assert "Mumbai" in project.voice_sheet(project.characters[0])
    assert project.characters[0].tts_voice != project.characters[1].tts_voice
    assert (tmp_path / "p" / ".env.example").exists()

    with pytest.raises(SetupError, match="already exists"):
        create_project(tmp_path / "p", [CastMember("X", portrait)])


def test_create_project_rejects_bad_image(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("x")
    with pytest.raises(SetupError, match="use one of"):
        create_project(tmp_path / "p", [CastMember("X", bad)])


def test_read_sheet_input(tmp_path):
    sheet = tmp_path / "v.md"
    sheet.write_text("Fast and cheeky.", "utf-8")
    assert read_sheet_input(str(sheet)) == "Fast and cheeky."
    assert read_sheet_input("Talks slowly") == "Talks slowly"
    assert read_sheet_input("  ") == ""
    with pytest.raises(SetupError):
        read_sheet_input(str(tmp_path / "missing.md"))


def test_set_option_validates(tmp_path, portrait):
    project = create_project(tmp_path / "p", [CastMember("A", portrait)])
    updated = set_option(project, "render.aspect", "16:9")
    assert Project.load(tmp_path / "p").render.aspect == "16:9"
    assert updated.render.size == (1920, 1080)
    with pytest.raises(ValueError):
        set_option(project, "render.aspect", "7:3")
    with pytest.raises(SetupError, match="no setting"):
        set_option(project, "render.nope.deeper", "1")


def test_init_wizard(workdir, portrait):
    answers = "\n".join([
        "2",
        "Priya", "mother", str(portrait), "Warm and calm.",
        "Aarav", "son", str(portrait), "",
        "hinglish", "9:16",
    ]) + "\n"
    result = runner.invoke(app, ["init"], input=answers)
    assert result.exit_code == 0, result.output
    project = Project.load(workdir)
    assert [c.name for c in project.characters] == ["Priya", "Aarav"]
    assert project.language == "hinglish"
    assert project.characters[1].voice_sheet is None

    listed = runner.invoke(app, ["cast"])
    assert "Priya" in listed.output and "Aarav" in listed.output


def test_styles_listed_without_project(workdir):
    result = runner.invoke(app, ["styles"])
    assert result.exit_code == 0
    assert "family" in result.output


def test_commands_need_a_project(workdir):
    result = runner.invoke(app, ["render"])
    assert result.exit_code == 1
    assert "findbyme init" in result.output


@needs_ffmpeg
def test_make_end_to_end(workdir, portrait):
    create_project(workdir, [CastMember("Host", portrait)])
    set_option(Project.load(workdir), "render.short_side", "360")

    assert runner.invoke(app, ["doctor"]).exit_code == 0
    result = runner.invoke(app, ["make", "how rainbows form", "--style", "kids", "--yes"])
    assert result.exit_code == 0, result.output
    finals = list((workdir / "runs").glob("*/final.mp4"))
    assert len(finals) == 1

    again = runner.invoke(app, ["render", "latest"])
    assert again.exit_code == 0, again.output
