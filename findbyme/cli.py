"""Command line interface."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__, doctor, styles
from .config import ASPECTS, LANGUAGES, RUNS_DIR, Project, find_root
from .pipeline import Pipeline, PipelineError
from .pipeline import Progress as Step
from .plan import Plan
from .providers import ProviderError
from .setup import (
    CastMember,
    SetupError,
    add_character,
    create_project,
    read_sheet_input,
    remove_character,
    set_option,
)

app = typer.Typer(
    help="FindByMe: set up your cast once, then turn any topic into a finished video.",
    no_args_is_help=True,
    add_completion=False,
)
cast_app = typer.Typer(help="Show or change the cast.", no_args_is_help=False)
app.add_typer(cast_app, name="cast")
console = Console()


@app.callback()
def _load_env() -> None:
    try:
        load_dotenv(find_root(Path.cwd()) / ".env")
    except FileNotFoundError:
        load_dotenv()


def _project() -> Project:
    try:
        return Project.load()
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


def _fail(message: str) -> NoReturn:
    console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(1)


def _ask_member(number: int) -> CastMember:
    console.rule(f"Character {number}")
    name = typer.prompt("Name")
    role = typer.prompt("Role (mother, son, host, ...)", default="", show_default=False)
    while True:
        image = Path(typer.prompt("Reference image path").strip().strip('"'))
        if image.is_file():
            break
        console.print(f"[yellow]Not found: {image}[/yellow]")
    console.print(
        "Voice sheet: how this person talks (accent, pace, mood, catchphrases).\n"
        "Type it, give a .md/.txt path, or press Enter for the default."
    )
    while True:
        try:
            sheet = read_sheet_input(typer.prompt("Voice sheet", default="", show_default=False))
            break
        except SetupError as exc:
            console.print(f"[yellow]{exc}[/yellow]")
    return CastMember(name=name, role=role, image=image, voice_sheet=sheet)


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="Where to create the project."),
) -> None:
    """One-time setup: your characters, their voices and the language."""
    count = typer.prompt("How many characters will be in your videos?", type=int, default=1)
    if not 1 <= count <= 12:
        _fail("pick between 1 and 12 characters")
    cast = [_ask_member(i + 1) for i in range(count)]

    console.rule("Language")
    console.print(", ".join(f"{code}" for code in LANGUAGES) + " (or any language name)")
    language = typer.prompt("Language", default="en")
    aspect = typer.prompt(f"Aspect ratio ({', '.join(ASPECTS)})", default="9:16")

    try:
        project = create_project(directory, cast, language=language, aspect=aspect)
    except (SetupError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"\n[green]Project ready:[/green] {project.root}")
    _print_doctor(project)
    console.print(
        "\nNext: copy .env.example to .env and add your keys, then\n"
        '  findbyme make "your topic" --style family'
    )


@cast_app.callback(invoke_without_command=True)
def cast_list(ctx: typer.Context) -> None:
    """List the cast."""
    if ctx.invoked_subcommand:
        return
    project = _project()
    table = Table("id", "name", "role", "voice", "image", "voice sheet")
    for c in project.characters:
        table.add_row(c.id, c.name, c.role, c.tts_voice, c.image, c.voice_sheet or "default")
    console.print(table)


@cast_app.command("add")
def cast_add() -> None:
    """Add a character to the cast."""
    project = _project()
    try:
        char = add_character(project, _ask_member(len(project.characters) + 1))
    except SetupError as exc:
        _fail(str(exc))
    console.print(f"[green]Added[/green] {char.name} as `{char.id}`")


@cast_app.command("remove")
def cast_remove(char_id: str) -> None:
    """Remove a character and their files."""
    try:
        remove_character(_project(), char_id)
    except (SetupError, KeyError) as exc:
        _fail(str(exc))
    console.print(f"Removed `{char_id}`")


@app.command("styles")
def list_styles() -> None:
    """List video styles (built-in and from ./styles)."""
    root = _project().root if _has_project() else None
    table = Table("style", "seconds", "description")
    for style in styles.available(root).values():
        table.add_row(style.name, str(style.seconds), style.description)
    console.print(table)


def _has_project() -> bool:
    try:
        find_root(Path.cwd())
    except FileNotFoundError:
        return False
    return True


@app.command("set")
def set_cmd(
    key: str = typer.Argument(..., help="Dotted path, e.g. render.aspect or providers.llm.model"),
    value: str = typer.Argument(..., help="New value, parsed as YAML"),
) -> None:
    """Change one setting in findbyme.yaml, with validation."""
    try:
        set_option(_project(), key, value)
    except (SetupError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"[green]{key}[/green] = {value}")


def _print_doctor(project: Project) -> bool:
    table = Table("", "check", "detail", show_header=False, box=None)
    results = doctor.run(project)
    for check in results:
        mark = "[green]ok[/green]" if check.ok else "[red]!![/red]"
        table.add_row(mark, check.name, check.detail)
    console.print(table)
    return all(c.ok for c in results)


@app.command("doctor")
def doctor_cmd() -> None:
    """Check ffmpeg, the cast files and provider keys."""
    if not _print_doctor(_project()):
        raise typer.Exit(1)


def _print_plan(plan: Plan, project: Project) -> None:
    table = Table("#", "speaker", "line", "action", title=plan.title, show_lines=False)
    names = {c.id: c.name for c in project.characters}
    for i, line in enumerate(plan.lines, 1):
        table.add_row(str(i), names.get(line.speaker, line.speaker), line.text, line.action)
    console.print(table)


class _Bar:
    """Turns pipeline progress events into a rich progress display."""

    def __init__(self, progress: Progress):
        self.progress = progress
        self.tasks: dict[str, TaskID] = {}

    def __call__(self, step: Step) -> None:
        task = self.tasks.get(step.stage)
        if task is None:
            task = self.progress.add_task(step.stage, total=step.total)
            self.tasks[step.stage] = task
        self.progress.update(
            task, completed=step.done, total=step.total,
            description=f"{step.stage:<8} {step.message}",
        )


def _progress() -> Progress:
    return Progress(
        TextColumn("{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(), console=console,
    )


def _resolve_run(project: Project, run: str) -> Path:
    runs = project.path(RUNS_DIR)
    if run == "latest":
        candidates = sorted(p for p in runs.glob("*") if (p / "plan.json").exists())
        if not candidates:
            _fail("no runs yet")
        return candidates[-1]
    path = Path(run)
    for candidate in (path, runs / run):
        if (candidate / "plan.json").exists():
            return candidate.resolve()
    _fail(f"no plan.json in {run}")


@app.command()
def script(
    topic: str = typer.Argument(..., help="What the video is about."),
    style: str = typer.Option("explainer", "--style", "-s"),
    notes: str = typer.Option("", "--notes", "-n", help="Extra direction for the writer."),
    seconds: int = typer.Option(0, "--seconds", help="Target length; 0 uses the style default."),
) -> None:
    """Write the script only. Review or edit plan.json, then run `findbyme render`."""
    project = _project()
    try:
        run_dir, plan = Pipeline(project).write(topic, style, notes, seconds or None)
    except (ProviderError, KeyError) as exc:
        _fail(str(exc))
    _print_plan(plan, project)
    console.print(f"\nScript saved: {run_dir / 'plan.json'}\nRender it: findbyme render latest")


@app.command()
def render(
    run: str = typer.Argument("latest", help="Run folder, its name, or `latest`."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Regenerate everything."),
) -> None:
    """Render a run folder's plan.json into final.mp4."""
    project = _project()
    run_dir = _resolve_run(project, run)
    with _progress() as progress:
        pipeline = Pipeline(project, on_progress=_Bar(progress), use_cache=not no_cache)
        try:
            final = pipeline.render(run_dir)
        except (PipelineError, ProviderError, ValueError) as exc:
            progress.stop()
            _fail(str(exc))
    console.print(f"[green]Done:[/green] {final}")


@app.command()
def make(
    topic: str = typer.Argument(..., help="What the video is about."),
    style: str = typer.Option("explainer", "--style", "-s"),
    notes: str = typer.Option("", "--notes", "-n", help="Extra direction for the writer."),
    seconds: int = typer.Option(0, "--seconds", help="Target length; 0 uses the style default."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the script review."),
) -> None:
    """Script, review, render: topic in, finished video out."""
    project = _project()
    try:
        run_dir, plan = Pipeline(project).write(topic, style, notes, seconds or None)
    except (ProviderError, KeyError) as exc:
        _fail(str(exc))
    _print_plan(plan, project)
    if not yes and not typer.confirm("Render this script?", default=True):
        console.print(f"Kept the script for editing: {run_dir / 'plan.json'}")
        raise typer.Exit(0)
    render(str(run_dir), no_cache=False)


@app.command()
def version() -> None:
    """Show the FindByMe version."""
    console.print(__version__)
