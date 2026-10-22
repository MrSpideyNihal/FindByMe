"""Command line interface."""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import NoReturn

import typer
from dotenv import load_dotenv
from pydantic import ValidationError
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Table

from . import __version__, doctor, styles
from .config import ASPECTS, CONFIG_FILE, LANGUAGES, RUNS_DIR, Project, find_root
from .pipeline import BRIEF_FILE, Pipeline, PipelineError
from .pipeline import Progress as Step
from .pipeline.runner import BRIEF_META
from .plan import PLAN_FILE, Plan
from .providers import ProviderError, Stack
from .setup import (
    CastMember,
    SetupError,
    add_character,
    create_project,
    read_sheet_input,
    remove_character,
    reset_project,
    set_option,
    write_agent_files,
)

app = typer.Typer(
    help="FindByMe: set up your cast once, then turn any topic into a finished video.",
    no_args_is_help=True,
    add_completion=False,
)
cast_app = typer.Typer(help="Show or change the cast.", no_args_is_help=False)
app.add_typer(cast_app, name="cast")
console = Console()
BOM = chr(0xFEFF)


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
    terminal: bool = typer.Option(False, "--terminal", help="Answer the questions here instead."),
    port: int = typer.Option(7860, help="Port for the setup page."),
) -> None:
    """One-time setup: opens the setup page in your browser (or --terminal)."""
    if (directory / CONFIG_FILE).exists():
        _fail(
            f"{directory.resolve()} is already set up. Open it with `findbyme ui`, "
            "or start over with `findbyme reset`."
        )
    if not terminal:
        _serve(directory.resolve(), "127.0.0.1", port, open_browser=True)
        return

    answer = typer.prompt("How many characters will be in your videos?", default="1")
    # PowerShell pipes start with a byte-order mark; strip it so "1" reads as 1.
    answer = answer.lstrip(BOM).strip()
    if not answer.isdigit():
        _fail("enter a number of characters")
    count = int(answer)
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
        '\nNext: findbyme make "your topic" --style family\n'
        "No API keys? Ask Claude Code or Antigravity in this folder to make a video; "
        "they write the script and FindByMe renders it."
    )


@app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Start over: remove the cast and settings (finished videos in runs/ are kept)."""
    project = _project()
    if not yes and not typer.confirm(
        f"Delete the cast and settings in {project.root}? Videos in runs/ stay.", default=False
    ):
        raise typer.Exit(0)
    reset_project(project.root)
    console.print("Project cleared. Run `findbyme init` to set it up again.")


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
        if not check.ok:
            mark = "[red]!![/red]"
        elif check.warn:
            mark = "[yellow]--[/yellow]"
        else:
            mark = "[green]ok[/green]"
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


def _warn_placeholders(stack: Stack) -> None:
    missing = stack.placeholders()
    if not missing:
        return
    lines = "\n".join(f"  {kind}: {what}" for kind, what in missing.items())
    console.print(
        f"[yellow]Some stages have no API key, so they use placeholders:[/yellow]\n{lines}\n"
        "Add keys to .env in the project folder (see .env.example), then run "
        "`findbyme doctor`.\n"
    )


def _is_run(path: Path) -> bool:
    return (path / PLAN_FILE).exists() or (path / BRIEF_FILE).exists()


def _resolve_run(project: Project, run: str) -> Path:
    runs = project.path(RUNS_DIR)
    if run == "latest":
        candidates = sorted(p for p in runs.glob("*") if _is_run(p))
        if not candidates:
            _fail("no runs yet")
        return candidates[-1]
    for candidate in (Path(run), runs / run):
        if _is_run(candidate):
            return candidate.resolve()
    _fail(f"no run named {run}")


def _print_brief_help(run_dir: Path) -> None:
    console.print(
        f"\n[yellow]No script API key, so the script is written outside FindByMe.[/yellow]\n"
        f"Brief: {run_dir / BRIEF_FILE}\n\n"
        "Either:\n"
        "  - ask your coding agent (Claude Code, Antigravity, ...) in this folder:\n"
        f'      "write the script for {run_dir.name} and render it"\n'
        "  - or paste the brief into Claude, ChatGPT or Gemini, save the JSON reply, and run:\n"
        f"      findbyme plan {run_dir.name} --from reply.json\n"
        f"Then: findbyme render {run_dir.name}"
    )


@app.command()
def brief(
    topic: str = typer.Argument(..., help="What the video is about."),
    style: str = typer.Option("explainer", "--style", "-s"),
    notes: str = typer.Option("", "--notes", "-n", help="Extra direction for the writer."),
    seconds: int = typer.Option(0, "--seconds", help="Target length; 0 uses the style default."),
) -> None:
    """Start a video whose script you or your AI agent write (no API key needed)."""
    project = _project()
    try:
        run_dir = Pipeline(project).brief(topic, style, notes, seconds or None)
    except KeyError as exc:
        _fail(str(exc))
    console.print(f"Run: {run_dir.name}")
    _print_brief_help(run_dir)


@app.command("plan")
def plan_cmd(
    run: str = typer.Argument("latest", help="Run folder, its name, or `latest`."),
    source: Path = typer.Option(
        None, "--from", help="File with the script JSON; default is the run's plan.json."
    ),
) -> None:
    """Check a script against the cast and schema, and save it as the run's plan."""
    project = _project()
    run_dir = _resolve_run(project, run)
    path = source or run_dir / PLAN_FILE
    if not path.is_file():
        _fail(f"{path} not found; write the script JSON there first")
    pipeline = Pipeline(project)
    raw = path.read_text(encoding="utf-8").lstrip(BOM)
    try:
        if (run_dir / BRIEF_META).exists():
            plan = pipeline.accept(run_dir, raw)
        else:
            plan = Plan.model_validate_json(raw)
            plan.check_cast(project)
            plan.save(run_dir)
    except (ValueError, ValidationError) as exc:
        _fail(f"the script is not valid yet:\n{exc}")
    _print_plan(plan, project)
    console.print(f"\nScript OK. Render it: findbyme render {run_dir.name}")


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
    if plan is None:
        _print_brief_help(run_dir)
        return
    _print_plan(plan, project)
    console.print(f"\nScript saved: {run_dir / PLAN_FILE}\nRender it: findbyme render latest")


@app.command()
def render(
    run: str = typer.Argument("latest", help="Run folder, its name, or `latest`."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Regenerate everything."),
) -> None:
    """Render a run folder's plan.json into final.mp4."""
    project = _project()
    run_dir = _resolve_run(project, run)
    stack = Stack(project.providers)
    _warn_placeholders(stack)
    with _progress() as progress:
        pipeline = Pipeline(
            project, stack=stack, on_progress=_Bar(progress), use_cache=not no_cache
        )
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
    if plan is None:
        _print_brief_help(run_dir)
        return
    _print_plan(plan, project)
    if not yes and not typer.confirm("Render this script?", default=True):
        console.print(f"Kept the script for editing: {run_dir / 'plan.json'}")
        raise typer.Exit(0)
    render(str(run_dir), no_cache=False)


def _serve(root: Path, host: str, port: int, open_browser: bool) -> None:
    try:
        import uvicorn

        from .web.app import create_app
    except ImportError:
        _fail('the UI needs extra packages: pip install "findbyme[ui]"')
    url = f"http://{host}:{port}"
    console.print(f"FindByMe for {root}\nOpen {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(root), host=host, port=port, log_level="warning")


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(7860, help="Port."),
    directory: Path = typer.Option(Path("."), "--dir", help="Project folder."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser tab."),
) -> None:
    """Open the web UI. Runs first-time setup in the browser if there is no project yet."""
    try:
        root = find_root(directory)
    except FileNotFoundError:
        root = directory.resolve()
    _serve(root, host, port, open_browser=not no_browser)


@app.command("agent-files")
def agent_files() -> None:
    """Add AGENTS.md and a Claude Code skill so AI agents can make videos here."""
    written = write_agent_files(_project().root)
    for path in written:
        console.print(f"wrote {path}")
    if not written:
        console.print("Agent files already exist.")


@app.command()
def version() -> None:
    """Show the FindByMe version."""
    console.print(__version__)
