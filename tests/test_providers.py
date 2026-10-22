import json

import httpx
import pytest

from findbyme import media
from findbyme.config import ProviderConfig, default_providers
from findbyme.providers import AnimateRequest, ProviderError, Stack, build, http
from findbyme.providers import video as video_mod
from findbyme.providers.llm import MockLLM
from findbyme.providers.video import FalVideo, pick, render_inputs

from .conftest import needs_ffmpeg


def test_render_inputs_keeps_types_and_interpolates():
    template = {
        "image_url": "{image}",
        "duration": "{duration}",
        "prompt": "A {aspect} shot: {prompt}",
        "nested": [{"x": "{duration}"}],
    }
    out = render_inputs(template, {"image": "data:x", "duration": 4.5, "aspect": "9:16",
                                   "prompt": "waves"})
    assert out == {
        "image_url": "data:x",
        "duration": 4.5,
        "prompt": "A 9:16 shot: waves",
        "nested": [{"x": 4.5}],
    }


def test_pick_follows_paths():
    assert pick({"video": {"url": "u"}}, "video.url") == "u"
    assert pick({"output": ["a", "b"]}, "output.1") == "b"
    assert pick(["first", "second"], "") == "first"
    with pytest.raises(KeyError):
        pick({"video": {}}, "video.url")


def test_build_unknown_and_custom_providers():
    with pytest.raises(ProviderError, match="unknown llm provider"):
        build("llm", ProviderConfig(provider="nope"))
    custom = build("llm", ProviderConfig(provider="findbyme.providers.llm:MockLLM"))
    assert isinstance(custom, MockLLM)
    with pytest.raises(ProviderError, match="cannot load"):
        build("tts", ProviderConfig(provider="missing.module:Thing"))


def test_offline_stack_builds():
    stack = Stack(default_providers({}))
    assert set(stack.describe().values()) == {"mock:llm", "mock:tts", "mock:stt", "mock:video"}


def test_mock_llm_reads_brief():
    brief = {"topic": "saving money", "cast": [{"id": "mom"}, {"id": "kid"}], "max_lines": 4}
    raw = MockLLM().complete("sys", f"write it <brief>{json.dumps(brief)}</brief>")
    plan = json.loads(raw)
    assert [line["speaker"] for line in plan["lines"]] == ["mom", "kid", "mom", "kid"]


def test_request_retries_then_fails(monkeypatch):
    calls = []

    def fake(method, url, **kwargs):
        calls.append(url)
        return httpx.Response(503, text="busy")

    monkeypatch.setattr(http.httpx, "request", fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    with pytest.raises(ProviderError, match="HTTP 503"):
        http.request("GET", "https://example.test", attempts=3)
    assert len(calls) == 3


def test_request_does_not_retry_client_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(
        http.httpx, "request", lambda *a, **k: calls.append(1) or httpx.Response(401, text="no")
    )
    with pytest.raises(ProviderError, match="HTTP 401"):
        http.request("GET", "https://example.test")
    assert len(calls) == 1


@needs_ffmpeg
def test_fal_queue_flow(monkeypatch, tmp_path, portrait):
    monkeypatch.setenv("FAL_KEY", "test")
    audio = tmp_path / "a.wav"
    media.ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "1", audio)
    statuses = iter(["IN_QUEUE", "IN_PROGRESS", "COMPLETED"])
    sent = {}

    def fake_request(method, url, **kwargs):
        if method == "POST":
            sent.update(kwargs["json"])
            return httpx.Response(200, json={"status_url": "s", "response_url": "r"})
        if url == "s":
            return httpx.Response(200, json={"status": next(statuses)})
        return httpx.Response(200, json={"video": {"url": "https://cdn.test/v.mp4"}})

    monkeypatch.setattr(video_mod, "request", fake_request)
    monkeypatch.setattr(video_mod.time, "sleep", lambda s: None)
    def fake_download(url, out):
        out.write_text(url)
        return out

    monkeypatch.setattr(video_mod, "download", fake_download)

    gen = FalVideo("fal-ai/test", {"inputs": {"image_url": "{image}", "audio_url": "{audio}"}})
    req = AnimateRequest(portrait, audio, "waves", 1.0, (360, 640), "9:16")
    out = gen.animate(req, tmp_path / "v.mp4")

    assert out.read_text() == "https://cdn.test/v.mp4"
    assert sent["image_url"].startswith("data:image/png;base64,")
    assert sent["audio_url"].startswith("data:audio/")


@needs_ffmpeg
def test_mock_media_providers(tmp_path, portrait):
    stack = Stack(default_providers({}))
    text = "this line has exactly seven words total"
    audio = stack.tts.synthesize(
        text, voice="coral", instructions="", language="en", out=tmp_path / "l.wav"
    )
    seconds = media.duration(audio)
    assert seconds == pytest.approx(7 * 0.36, abs=0.1)

    words = stack.stt.transcribe(audio, language="en", hint=text)
    assert [w.text for w in words] == text.split()
    assert words[-1].end == pytest.approx(seconds, abs=0.05)

    clip = stack.video.animate(
        AnimateRequest(portrait, audio, "", seconds, (360, 640), "9:16"), tmp_path / "v.mp4"
    )
    assert media.duration(clip) == pytest.approx(seconds, abs=0.1)
