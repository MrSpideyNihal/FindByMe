"""Local web UI. Same setup and pipeline code as the CLI, driven over a small JSON API."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .. import __version__, doctor, styles
from ..config import ASPECTS, CAST_DIR, CONFIG_FILE, LANGUAGES, RUNS_DIR, Project
from ..pipeline import Pipeline, PipelineError
from ..pipeline import Progress as Step
from ..plan import Plan
from ..providers import ProviderError
from ..setup import (
    CastMember,
    SetupError,
    add_character,
    create_project,
    remove_character,
    set_option,
)

STATIC = Path(__file__).parent / "static"


class ScriptRequest(BaseModel):
    topic: str
    style: str = "explainer"
    notes: str = ""
    seconds: int | None = None


class SettingRequest(BaseModel):
    key: str
    value: str


class VoiceRequest(BaseModel):
    sheet: str = ""
    tts_voice: str = ""


@dataclass
class Job:
    id: str
    run: str
    status: str = "running"
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "run": self.run, "status": self.status,
                "steps": self.steps, "error": self.error}


def create_app(root: Path) -> FastAPI:
    root = root.resolve()
    load_dotenv(root / ".env")
    app = FastAPI(title="FindByMe", version=__version__, docs_url=None, redoc_url=None)
    jobs: dict[str, Job] = {}
    lock = threading.Lock()

    def project() -> Project:
        if not (root / CONFIG_FILE).exists():
            raise HTTPException(409, "no project yet; finish setup first")
        return Project.load(root)

    def run_dir(name: str) -> Path:
        path = (root / RUNS_DIR / name).resolve()
        if path.parent != (root / RUNS_DIR).resolve() or not (path / "plan.json").exists():
            raise HTTPException(404, f"no run named {name!r}")
        return path

    def summary(path: Path) -> dict[str, Any]:
        plan = Plan.load(path)
        base = f"/files/{RUNS_DIR}/{path.name}"
        final = path / "final.mp4"
        manifest = path / "run.json"
        return {
            "name": path.name,
            "title": plan.title,
            "style": plan.style,
            "lines": len(plan.lines),
            "video": f"{base}/final.mp4" if final.exists() else None,
            "cover": f"{base}/cover.jpg" if (path / "cover.jpg").exists() else None,
            "frames": f"{base}/frames.jpg" if (path / "frames.jpg").exists() else None,
            "post": (path / "post.txt").read_text("utf-8") if (path / "post.txt").exists() else "",
            "manifest": json.loads(manifest.read_text("utf-8")) if manifest.exists() else None,
        }

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        has_project = (root / CONFIG_FILE).exists()
        data: dict[str, Any] = {
            "version": __version__,
            "root": str(root),
            "languages": LANGUAGES,
            "aspects": list(ASPECTS),
            "styles": [s.model_dump() for s in styles.available(root).values()],
            "project": None,
        }
        if has_project:
            proj = project()
            data["project"] = proj.model_dump(mode="json")
            data["sheets"] = {c.id: proj.voice_sheet(c) for c in proj.characters}
            data["doctor"] = [c.__dict__ for c in doctor.run(proj)]
        return data

    async def _save_upload(upload: UploadFile, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / Path(upload.filename or "image.png").name
        with target.open("wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        return target

    @app.post("/api/setup")
    async def setup(
        cast: str = Form(..., description="JSON list of {name, role, voice_sheet}"),
        language: str = Form("en"),
        aspect: str = Form("9:16"),
        images: list[UploadFile] = File(...),
    ) -> dict[str, Any]:
        members_info = json.loads(cast)
        if len(members_info) != len(images):
            raise HTTPException(400, "every character needs exactly one image")
        with tempfile.TemporaryDirectory() as tmp:
            members = []
            for info, upload in zip(members_info, images, strict=True):
                image = await _save_upload(upload, Path(tmp) / str(len(members)))
                members.append(CastMember(
                    name=info.get("name", ""), role=info.get("role", ""),
                    voice_sheet=info.get("voice_sheet", ""), image=image,
                ))
            try:
                create_project(root, members, language=language, aspect=aspect)
            except (SetupError, ValidationError, ValueError) as exc:
                raise HTTPException(400, str(exc)) from exc
        return state()

    @app.post("/api/cast")
    async def cast_add(
        name: str = Form(...),
        role: str = Form(""),
        voice_sheet: str = Form(""),
        image: UploadFile = File(...),
    ) -> dict[str, Any]:
        proj = project()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = await _save_upload(image, folder)
            try:
                add_character(proj, CastMember(name, path, role=role, voice_sheet=voice_sheet))
            except SetupError as exc:
                raise HTTPException(400, str(exc)) from exc
        return state()

    @app.delete("/api/cast/{char_id}")
    def cast_remove(char_id: str) -> dict[str, Any]:
        try:
            remove_character(project(), char_id)
        except (SetupError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return state()

    @app.put("/api/cast/{char_id}/voice")
    def cast_voice(char_id: str, body: VoiceRequest) -> dict[str, Any]:
        proj = project()
        try:
            char = proj.character(char_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        sheet = root / CAST_DIR / char_id / "voice.md"
        if body.sheet.strip():
            sheet.parent.mkdir(parents=True, exist_ok=True)
            sheet.write_text(body.sheet.strip() + "\n", encoding="utf-8")
            char.voice_sheet = f"{CAST_DIR}/{char_id}/voice.md"
        else:
            sheet.unlink(missing_ok=True)
            char.voice_sheet = None
        if body.tts_voice.strip():
            char.tts_voice = body.tts_voice.strip()
        proj.save()
        return state()

    @app.post("/api/settings")
    def settings(body: SettingRequest) -> dict[str, Any]:
        try:
            set_option(project(), body.key, body.value)
        except (SetupError, ValidationError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return state()

    @app.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        folder = root / RUNS_DIR
        paths = sorted(folder.glob("*/plan.json"), reverse=True) if folder.exists() else []
        return [summary(p.parent) for p in paths]

    @app.post("/api/script")
    def script(body: ScriptRequest) -> dict[str, Any]:
        if not body.topic.strip():
            raise HTTPException(400, "write a topic first")
        try:
            path, plan = Pipeline(project()).write(
                body.topic.strip(), body.style, body.notes, body.seconds
            )
        except (ProviderError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"run": path.name, "plan": plan.model_dump()}

    @app.get("/api/runs/{name}")
    def run_detail(name: str) -> dict[str, Any]:
        path = run_dir(name)
        return {**summary(path), "plan": Plan.load(path).model_dump()}

    @app.put("/api/runs/{name}/plan")
    def save_plan(name: str, body: dict[str, Any]) -> dict[str, Any]:
        path = run_dir(name)
        try:
            plan = Plan.model_validate(body)
            plan.check_cast(project())
        except (ValidationError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        plan.save(path)
        return {"run": name, "plan": plan.model_dump()}

    @app.post("/api/runs/{name}/render")
    def render(name: str) -> dict[str, Any]:
        path = run_dir(name)
        proj = project()
        with lock:
            for job in jobs.values():
                if job.run == name and job.status == "running":
                    return job.as_dict()
            job = Job(id=uuid.uuid4().hex[:12], run=name)
            jobs[job.id] = job

        def on_progress(step: Step) -> None:
            job.steps[step.stage] = {"done": step.done, "total": step.total,
                                     "message": step.message}

        def work() -> None:
            try:
                Pipeline(proj, on_progress=on_progress).render(path)
                job.status = "done"
            except (PipelineError, ProviderError, ValueError, RuntimeError) as exc:
                job.error = str(exc)
                job.status = "failed"

        threading.Thread(target=work, name=f"render-{name}", daemon=True).start()
        return job.as_dict()

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        data = job.as_dict()
        if job.status == "done":
            data["result"] = summary(run_dir(job.run))
        return data

    @app.get("/files/{kind}/{rest:path}")
    def files(kind: str, rest: str) -> FileResponse:
        if kind not in (CAST_DIR, RUNS_DIR):
            raise HTTPException(404)
        base = (root / kind).resolve()
        target = (base / rest).resolve()
        if base not in target.parents or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.middleware("http")
    async def revalidate(request: Request, call_next: Any) -> Response:
        # Make browsers recheck the UI files so an upgrade never shows a stale page.
        response = await call_next(request)
        if not request.url.path.startswith("/files/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
