import shutil
from pathlib import Path

import pytest

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Tests never reach real providers: `auto` resolves to mocks."""
    from findbyme.config import OFFLINE_ENV, PROVIDER_KEYS

    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(OFFLINE_ENV, "1")


@pytest.fixture
def portrait(tmp_path: Path) -> Path:
    """A small solid-colour reference image."""
    from findbyme import media

    out = tmp_path / "portrait.png"
    media.ffmpeg("-f", "lavfi", "-i", "color=c=0x3B6E8F:s=360x480", "-frames:v", "1", out)
    return out
