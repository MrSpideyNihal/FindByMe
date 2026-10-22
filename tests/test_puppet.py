import pytest

from findbyme import media
from findbyme.config import CaptionStyle
from findbyme.pipeline.captions import TimedWord, build_ass
from findbyme.providers import AnimateRequest
from findbyme.providers.puppet import (
    GESTURES,
    PuppetVideo,
    Scene,
    draw_frame,
    plan_gestures,
    read_look,
)

from .conftest import needs_ffmpeg


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("shrugs with both palms up", ["shrug"]),
        ("holds up one finger, then a second finger", ["count", "count"]),
        ("points at the camera, then gives a quick two-finger salute", ["point", "salute"]),
        ("mimes lifting a barbell, then drops his arms and laughs", ["lift", "laugh"]),
        ("just talks", ["talk"]),
        ("", ["talk"]),
    ],
)
def test_actions_map_to_gestures(action, expected):
    assert plan_gestures(action) == expected
    assert all(name in GESTURES for name in expected)


def test_look_detects_mask_or_skin(tmp_path):
    from PIL import Image

    hero = tmp_path / "hero.png"
    Image.new("RGB", (64, 64), (180, 30, 40)).save(hero)
    assert read_look(hero).masked is True

    person = tmp_path / "person.png"
    Image.new("RGB", (64, 64), (224, 172, 140)).save(person)
    look = read_look(person)
    assert look.masked is False
    assert look.head == (224, 172, 140)


def test_frames_change_with_mouth_and_gesture(portrait):
    look = read_look(portrait)
    scene = Scene(180, 320, look, ["shrug", "wave"], 4.0, "happy", 1)
    closed = draw_frame(scene, 1.0, 0.0)
    talking = draw_frame(scene, 1.0, 1.0)
    later = draw_frame(scene, 3.0, 0.0)
    assert closed.size == (180, 320)
    assert closed.tobytes() != talking.tobytes()
    assert closed.tobytes() != later.tobytes()


@needs_ffmpeg
def test_puppet_renders_clip_matching_audio(tmp_path, portrait):
    audio = tmp_path / "a.wav"
    media.ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=1.5", "-ar", "48000", audio)
    video = PuppetVideo(options={"short_side": 180})
    req = AnimateRequest(portrait, audio, "", 1.5, (360, 640), "9:16", "waves", "happy")
    out = video.animate(req, tmp_path / "v.mp4")
    assert media.duration(out) == pytest.approx(1.5, abs=0.1)


def test_captions_hold_between_groups():
    words = [TimedWord(w, i * 0.5, i * 0.5 + 0.3) for i, w in enumerate("a b c d".split())]
    ass = build_ass([(0.0, words)], CaptionStyle(words_per_group=2), (1080, 1920))
    events = [line.split(",")[1:3] for line in ass.splitlines() if line.startswith("Dialogue")]
    # the last word of the first group stays up until the next group starts
    assert events[1] == ["0:00:00.50", "0:00:01.00"]
