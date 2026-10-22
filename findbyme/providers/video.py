"""Image-and-audio to video: the stage that makes a character talk and move.

Hosted video models change often and every one names its inputs differently, so the
fal and Replicate adapters take the request body as a template from ``findbyme.yaml``:

    video:
      provider: fal
      model: fal-ai/bytedance/omnihuman
      options:
        inputs: {image_url: "{image}", audio_url: "{audio}"}
        output: video.url

Placeholders: {image} and {audio} (sent as data URIs), {prompt}, {duration}, {aspect}.
A value that is exactly one placeholder keeps its type, so "{duration}" becomes a number.
Moving to a new model is a config edit, never a code change.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .. import media
from .base import AnimateRequest, ProviderError, make_identity
from .http import api_key, download, request


def render_inputs(template: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    def fill(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{") and stripped.endswith("}") and stripped[1:-1] in values:
                return values[stripped[1:-1]]
            for key, replacement in values.items():
                value = value.replace("{" + key + "}", str(replacement))
        return value

    return fill(template)


def pick(data: Any, path: str) -> Any:
    """Follow a dotted path (``video.url``, ``output.0``) into a JSON response."""
    for part in filter(None, path.split(".")):
        if isinstance(data, list):
            data = data[int(part)]
        elif isinstance(data, dict):
            data = data[part]
        else:
            raise KeyError(part)
    if isinstance(data, list) and data:
        data = data[0]
    return data


class _TemplatedVideo:
    provider = ""

    def __init__(self, model: str, options: dict[str, Any]):
        if not model:
            raise ProviderError(f"video provider {self.provider!r} needs a model")
        self.model = model
        self.inputs = options.get("inputs") or {"image_url": "{image}", "audio_url": "{audio}"}
        self.output = options.get("output", "video.url")
        self.timeout = float(options.get("timeout", 1200))
        self.poll = float(options.get("poll_seconds", 3))
        self.identity = make_identity(self.provider, model, options)

    def _payload(self, req: AnimateRequest) -> dict[str, Any]:
        return render_inputs(self.inputs, {
            "image": media.data_uri(req.image),
            "audio": media.data_uri(req.audio),
            "prompt": req.prompt,
            "duration": round(req.duration, 2),
            "aspect": req.aspect,
        })

    def _result_url(self, result: Any) -> str:
        try:
            url = pick(result, self.output)
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(
                f"{self.model}: no video at output path {self.output!r} in {str(result)[:300]}"
            ) from exc
        if isinstance(url, dict):
            url = url.get("url", "")
        if not isinstance(url, str) or not url.startswith("http"):
            raise ProviderError(f"{self.model}: expected a video URL, got {str(url)[:200]}")
        return url

    def _deadline_passed(self, started: float) -> bool:
        return time.monotonic() - started > self.timeout


class FalVideo(_TemplatedVideo):
    provider = "fal"

    def animate(self, req: AnimateRequest, out: Path) -> Path:
        headers = {"Authorization": f"Key {api_key('FAL_KEY')}"}
        job = request(
            "POST", f"https://queue.fal.run/{self.model}", headers=headers, json=self._payload(req)
        ).json()
        status_url, response_url = job.get("status_url"), job.get("response_url")
        if not status_url or not response_url:
            raise ProviderError(f"fal did not queue the job: {str(job)[:300]}")

        started = time.monotonic()
        while True:
            status = request("GET", status_url, headers=headers).json()
            state = status.get("status")
            if state == "COMPLETED":
                break
            if state not in ("IN_QUEUE", "IN_PROGRESS"):
                raise ProviderError(f"fal job failed: {str(status)[:300]}")
            if self._deadline_passed(started):
                raise ProviderError(f"fal job timed out after {self.timeout:.0f}s")
            time.sleep(self.poll)

        result = request("GET", response_url, headers=headers).json()
        return download(self._result_url(result), out)


class ReplicateVideo(_TemplatedVideo):
    provider = "replicate"

    def animate(self, req: AnimateRequest, out: Path) -> Path:
        headers = {"Authorization": f"Bearer {api_key('REPLICATE_API_TOKEN')}"}
        body: dict[str, Any] = {"input": self._payload(req)}
        if ":" in self.model:  # owner/name:version
            body["version"] = self.model.split(":", 1)[1]
            url = "https://api.replicate.com/v1/predictions"
        else:
            url = f"https://api.replicate.com/v1/models/{self.model}/predictions"
        prediction = request("POST", url, headers=headers, json=body).json()

        started = time.monotonic()
        while prediction.get("status") not in ("succeeded", "failed", "canceled"):
            if self._deadline_passed(started):
                raise ProviderError(f"replicate prediction timed out after {self.timeout:.0f}s")
            time.sleep(self.poll)
            prediction = request("GET", prediction["urls"]["get"], headers=headers).json()

        if prediction["status"] != "succeeded":
            raise ProviderError(
                f"replicate prediction {prediction['status']}: {prediction.get('error')}"
            )
        return download(self._result_url(prediction.get("output")), out)


class MockVideo:
    """Offline stand-in: the reference image with a slow push-in and drift."""

    identity = "mock:video"

    def __init__(self, model: str = "", options: dict[str, Any] | None = None):
        # Mocks take the same constructor arguments as real providers.
        del model, options

    def animate(self, req: AnimateRequest, out: Path) -> Path:
        width, height = req.size
        frames = max(1, round(req.duration * 30))
        motion = (
            f"scale={int(width * 1.12)}:{int(height * 1.12)}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:"
            f"x='(iw-ow)/2+(iw-ow)/3*sin(n/{frames}*3.1416)':"
            f"y='(ih-oh)/2-(ih-oh)/4*n/{frames}',setsar=1"
        )
        media.ffmpeg(
            "-loop", "1", "-framerate", "30", "-i", req.image,
            "-vf", motion, "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", out,
        )
        return out
