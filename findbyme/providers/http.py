"""Shared HTTP plumbing: retries with backoff, readable errors, downloads."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx

from .base import ProviderError

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


def api_key(env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise ProviderError(f"{env_name} is not set. Add it to your environment or .env file.")
    return value


def request(
    method: str,
    url: str,
    *,
    attempts: int = 4,
    timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> httpx.Response:
    delay = 2.0
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = httpx.request(method, url, timeout=timeout, **kwargs)
        except httpx.TransportError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code < 400:
                return response
            last_error = f"HTTP {response.status_code}: {response.text[:500]}"
            if response.status_code not in RETRY_STATUS:
                break
            retry_after = response.headers.get("retry-after", "")
            if retry_after.isdigit():
                delay = max(delay, float(retry_after))
        if attempt < attempts:
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    raise ProviderError(f"{method} {url} failed: {last_error}")


def download(url: str, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=DEFAULT_TIMEOUT, follow_redirects=True) as response:
        if response.status_code >= 400:
            raise ProviderError(f"download failed: HTTP {response.status_code} for {url}")
        with out.open("wb") as fh:
            for chunk in response.iter_bytes():
                fh.write(chunk)
    return out
