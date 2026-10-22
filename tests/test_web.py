import json
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from findbyme.web.app import create_app  # noqa: E402

from .conftest import needs_ffmpeg  # noqa: E402


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY",
                "FAL_KEY", "REPLICATE_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "proj"))


def setup_project(client, portrait, names=("Priya", "Aarav")):
    files = [("images", (f"{n}.png", portrait.read_bytes(), "image/png")) for n in names]
    cast = [{"name": n, "role": "", "voice_sheet": "Calm." if i == 0 else ""}
            for i, n in enumerate(names)]
    return client.post(
        "/api/setup",
        data={"cast": json.dumps(cast), "language": "en", "aspect": "9:16"},
        files=files,
    )


def test_state_before_setup(client):
    data = client.get("/api/state").json()
    assert data["project"] is None
    assert any(s["name"] == "family" for s in data["styles"])
    assert client.get("/").status_code == 200


def test_setup_and_cast_management(client, portrait):
    response = setup_project(client, portrait)
    assert response.status_code == 200, response.text
    data = response.json()
    assert [c["id"] for c in data["project"]["characters"]] == ["priya", "aarav"]
    assert data["sheets"]["priya"] == "Calm."

    image = data["project"]["characters"][0]["image"]
    assert client.get(f"/files/{image}").status_code == 200
    assert client.get("/files/cast/../findbyme.yaml").status_code == 404
    assert client.get("/files/other/x").status_code == 404

    added = client.post(
        "/api/cast", data={"name": "Dadi", "role": "grandmother"},
        files={"image": ("d.png", portrait.read_bytes(), "image/png")},
    )
    assert added.status_code == 200
    assert len(added.json()["project"]["characters"]) == 3

    voice = {"sheet": "Slow, loving.", "tts_voice": "sage"}
    voiced = client.put("/api/cast/dadi/voice", json=voice)
    assert voiced.json()["sheets"]["dadi"] == "Slow, loving."

    removed = client.delete("/api/cast/dadi")
    assert len(removed.json()["project"]["characters"]) == 2


def test_setup_rejects_mismatched_images(client, portrait):
    response = client.post(
        "/api/setup",
        data={"cast": json.dumps([{"name": "A"}, {"name": "B"}])},
        files=[("images", ("a.png", portrait.read_bytes(), "image/png"))],
    )
    assert response.status_code == 400


def test_settings_validate(client, portrait):
    setup_project(client, portrait)
    ok = client.post("/api/settings", json={"key": "render.aspect", "value": "16:9"})
    assert ok.json()["project"]["render"]["aspect"] == "16:9"
    bad = client.post("/api/settings", json={"key": "render.fps", "value": "1000"})
    assert bad.status_code == 400


def test_plan_edit_is_validated(client, portrait):
    setup_project(client, portrait)
    run = client.post("/api/script", json={"topic": "tea", "style": "couple"}).json()
    plan = run["plan"]
    plan["lines"][0]["speaker"] = "ghost"
    assert client.put(f"/api/runs/{run['run']}/plan", json=plan).status_code == 400
    assert client.put("/api/runs/..%2F..%2Fetc/plan", json=plan).status_code == 404


@needs_ffmpeg
def test_render_job(client, portrait):
    setup_project(client, portrait, names=("Host",))
    client.post("/api/settings", json={"key": "render.short_side", "value": "360"})
    run = client.post("/api/script", json={"topic": "clouds", "style": "explainer"}).json()
    job = client.post(f"/api/runs/{run['run']}/render").json()

    deadline = time.monotonic() + 120
    while job["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.5)
        job = client.get(f"/api/jobs/{job['id']}").json()

    assert job["status"] == "done", job["error"]
    assert client.get(job["result"]["video"]).status_code == 200
    assert client.get("/api/runs").json()[0]["video"]
