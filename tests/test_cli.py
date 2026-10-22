import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from findbyme.cli import app
from findbyme.config import Project
from findbyme.setup import CastMember, SetupError, create_project, read_sheet_input, set_option

from .conftest import needs_ffmpeg

runner = CliRunner()



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
    result = runner.invoke(app, ["init", "--terminal"], input=answers)
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


def test_project_gets_agent_files(tmp_path, portrait):
    create_project(tmp_path / "p", [CastMember("A", portrait)])
    root = tmp_path / "p"
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".claude/skills/make-video/SKILL.md"):
        assert (root / name).is_file(), name
    assert "findbyme brief" in (root / "AGENTS.md").read_text()


def test_init_refuses_existing_project(workdir, portrait):
    create_project(workdir, [CastMember("A", portrait)])
    result = runner.invoke(app, ["init", "--terminal"])
    assert result.exit_code == 1
    assert "already set up" in result.output


def test_reset_clears_cast_but_keeps_runs(workdir, portrait):
    create_project(workdir, [CastMember("A", portrait)])
    (workdir / "runs" / "old").mkdir(parents=True)
    result = runner.invoke(app, ["reset", "--yes"])
    assert result.exit_code == 0, result.output
    assert not (workdir / "findbyme.yaml").exists()
    assert not (workdir / "cast").exists()
    assert (workdir / "runs" / "old").exists()


@needs_ffmpeg
def test_agent_writes_script_without_a_key(workdir, portrait):
    create_project(workdir, [CastMember("Host", portrait)])
    set_option(Project.load(workdir), "providers.llm.provider", "manual")
    set_option(Project.load(workdir), "render.short_side", "360")

    made = runner.invoke(app, ["make", "tides", "--style", "explainer"])
    assert made.exit_code == 0, made.output
    assert "No script API key" in made.output
    run_dir = next((workdir / "runs").iterdir())
    brief = (run_dir / "brief.md").read_text(encoding="utf-8")
    assert '"id": "host"' in brief and "JSON" in brief

    not_yet = runner.invoke(app, ["render", run_dir.name])
    assert not_yet.exit_code == 1
    assert "no script yet" in not_yet.output

    bad = run_dir / "plan.json"
    bad.write_text('{"title": "Tides", "lines": [{"speaker": "ghost", "text": "Hi"}]}')
    assert runner.invoke(app, ["plan", run_dir.name]).exit_code == 1

    bad.write_text(
        "```json\n"
        '{"title": "Tides", "lines": [{"speaker": "host", "text": "The moon pulls the sea.",'
        ' "action": "raises one hand slowly"}]}\n```'
    )
    checked = runner.invoke(app, ["plan", run_dir.name])
    assert checked.exit_code == 0, checked.output
    assert json.loads(bad.read_text())["topic"] == "tides"

    rendered = runner.invoke(app, ["render", run_dir.name])
    assert rendered.exit_code == 0, rendered.output
    assert (run_dir / "final.mp4").exists()
