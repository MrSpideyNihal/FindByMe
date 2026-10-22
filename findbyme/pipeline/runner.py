"""Runs the stages and keeps every artifact of a video in one run folder.

    runs/20261022-153000-why-the-sky-is-blue/
      brief.md       the writer's prompt (when you or your agent write the script)
      plan.json      the script (edit it and re-render)
      audio/         chosen voice take per line
      shots/         animated shot per line
      segments/      shots fitted to their line
      captions.ass
      final.mp4  cover.jpg  frames.jpg  post.txt
      run.json       providers, timings and per-line checks

Lines are independent until the final join, so each one runs voice, shot and segment
in its own worker. A 10-line video with concurrency 4 takes about three shot-lengths of
wall time, not ten.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import __version__, media, styles
from ..cache import Cache
from ..config import CACHE_DIR, RUNS_DIR, Project, slugify
from ..plan import PLAN_FILE, Plan
from ..providers import ScriptNeeded, Stack
from . import captions, compose
from .script import brief_document, parse_reply, write_script
from .shots import animate_line
from .voice import LineAudio, voice_line

BRIEF_FILE = "brief.md"
BRIEF_META = "brief.json"


class PipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Progress:
    stage: str
    done: int
    total: int
    message: str = ""


ProgressCallback = Callable[[Progress], None]


@dataclass
class _LineResult:
    index: int
    audio: LineAudio
    segment: Path
    seconds: float


class Pipeline:
    def __init__(
        self,
        project: Project,
        stack: Stack | None = None,
        on_progress: ProgressCallback | None = None,
        use_cache: bool = True,
    ):
        self.project = project
        self.stack = stack or Stack(project.providers)
        self.cache = Cache(project.path(CACHE_DIR), enabled=use_cache)
        self._notify = on_progress or (lambda p: None)

    def new_run_dir(self, topic: str) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = self.project.path(RUNS_DIR) / f"{stamp}-{slugify(topic)}"
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    def brief(
        self, topic: str, style: str, notes: str = "", seconds: int | None = None
    ) -> Path:
        """Start a run whose script is written outside FindByMe (by you or your agent)."""
        chosen = styles.load(style, self.project.root)
        run_dir = self.new_run_dir(topic)
        (run_dir / BRIEF_FILE).write_text(
            brief_document(self.project, chosen, topic, notes, seconds), encoding="utf-8"
        )
        (run_dir / BRIEF_META).write_text(
            json.dumps({"topic": topic, "style": chosen.name}, ensure_ascii=False), "utf-8"
        )
        return run_dir

    def accept(self, run_dir: Path, reply: str) -> Plan:
        """Validate a script written from the brief and save it as the run's plan."""
        meta = json.loads((run_dir / BRIEF_META).read_text(encoding="utf-8"))
        plan = parse_reply(self.project, meta["style"], meta["topic"], reply)
        plan.save(run_dir)
        return plan

    def write(
        self, topic: str, style: str, notes: str = "", seconds: int | None = None
    ) -> tuple[Path, Plan | None]:
        """Write the script with the configured model. Without one, start a brief run
        and return ``None`` for the plan."""
        chosen = styles.load(style, self.project.root)
        self._notify(Progress("script", 0, 1, f"writing a {chosen.name} script"))
        try:
            plan = write_script(self.project, self.stack.llm, chosen, topic, notes, seconds)
        except ScriptNeeded:
            return self.brief(topic, style, notes, seconds), None
        run_dir = self.new_run_dir(topic)
        plan.save(run_dir)
        self._notify(Progress("script", 1, 1, f"{len(plan.lines)} lines"))
        return run_dir, plan

    def render(self, run_dir: Path) -> Path:
        media.require_ffmpeg()
        started = time.monotonic()
        if not (run_dir / PLAN_FILE).exists():
            raise PipelineError(
                f"{run_dir.name} has no script yet. Write it from {BRIEF_FILE} into {PLAN_FILE}"
            )
        plan = Plan.load(run_dir)
        plan.check_cast(self.project)
        style = self._style(plan.style)
        settings = self.project.render
        for sub in ("audio", "shots", "segments"):
            (run_dir / sub).mkdir(exist_ok=True)

        results = self._render_lines(run_dir, plan, style)

        self._notify(Progress("compose", 0, 1, "joining shots"))
        offsets, cursor = [], 0.0
        for r in results:
            offsets.append(cursor)
            cursor += r.seconds
        total = cursor

        caption_file = None
        if settings.captions.enabled:
            segments = [
                (offset, captions.align(plan.lines[r.index].text, r.audio.words, r.audio.duration))
                for offset, r in zip(offsets, results, strict=True)
            ]
            caption_file = run_dir / "captions.ass"
            caption_file.write_text(
                captions.build_ass(segments, settings.captions, settings.size), encoding="utf-8"
            )

        joined = compose.concat([r.segment for r in results], run_dir / "joined.mp4")
        music = self.project.path(settings.music) if settings.music else None
        final = compose.finish(joined, caption_file, settings, music, run_dir / "final.mp4")
        joined.unlink(missing_ok=True)
        compose.cover(final, min(0.6, total / 2), run_dir / "cover.jpg")
        compose.frame_sheet(final, total, run_dir / "frames.jpg")
        self._write_post(run_dir, plan)
        self._write_manifest(run_dir, plan, results, total, time.monotonic() - started)
        self._notify(Progress("compose", 1, 1, f"{total:.1f}s video ready"))
        return final

    def make(self, topic: str, style: str, notes: str = "", seconds: int | None = None) -> Path:
        run_dir, plan = self.write(topic, style, notes, seconds)
        if plan is None:
            raise PipelineError(f"no script model; write the script from {run_dir / BRIEF_FILE}")
        return self.render(run_dir)

    def _style(self, name: str) -> styles.Style:
        try:
            return styles.load(name, self.project.root)
        except KeyError:
            return styles.Style(name=name or "custom")

    def _render_lines(self, run_dir: Path, plan: Plan, style: styles.Style) -> list[_LineResult]:
        settings = self.project.render
        total = len(plan.lines)

        def produce(index: int) -> _LineResult:
            line = plan.lines[index]
            n = f"{index + 1:03d}"
            audio = voice_line(
                self.project, self.stack.tts, self.stack.stt, self.cache, line,
                run_dir / "audio" / f"line_{n}.wav",
            )
            shot = animate_line(
                self.project, self.stack.video, self.cache, style, line, audio,
                run_dir / "shots" / f"line_{n}.mp4",
            )
            seconds = audio.duration + settings.gap_seconds
            seg = compose.segment(
                shot, audio.path, seconds, settings, run_dir / "segments" / f"seg_{n}.mp4"
            )
            return _LineResult(index, audio, seg, seconds)

        results: dict[int, _LineResult] = {}
        errors: dict[int, str] = {}
        self._notify(Progress("lines", 0, total, "voicing and animating"))
        with ThreadPoolExecutor(max_workers=settings.concurrency) as pool:
            futures = {pool.submit(produce, i): i for i in range(total)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:  # noqa: BLE001 - reported per line below
                    errors[index] = f"{type(exc).__name__}: {exc}"
                self._notify(Progress(
                    "lines", len(results) + len(errors), total,
                    f"line {index + 1} {'failed' if index in errors else 'done'}",
                ))

        if errors:
            detail = "\n".join(f"  line {i + 1}: {msg}" for i, msg in sorted(errors.items()))
            raise PipelineError(
                f"{len(errors)} of {total} lines failed. Finished lines are cached; "
                f"re-run render to retry only the failed ones.\n{detail}"
            )
        return [results[i] for i in range(total)]

    def _write_post(self, run_dir: Path, plan: Plan) -> None:
        tags = " ".join(f"#{t}" for t in plan.hashtags)
        (run_dir / "post.txt").write_text(f"{plan.caption}\n\n{tags}\n".strip() + "\n", "utf-8")

    def _write_manifest(
        self, run_dir: Path, plan: Plan, results: list[_LineResult], total: float, took: float
    ) -> None:
        manifest = {
            "findbyme": __version__,
            "title": plan.title,
            "duration": round(total, 2),
            "render_seconds": round(took, 1),
            "providers": self.stack.describe(),
            "size": list(self.project.render.size),
            "lines": [
                {
                    "speaker": plan.lines[r.index].speaker,
                    "seconds": round(r.seconds, 2),
                    "word_error": round(r.audio.word_error, 3),
                    "voice_takes": r.audio.takes,
                }
                for r in results
            ],
        }
        (run_dir / "run.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
