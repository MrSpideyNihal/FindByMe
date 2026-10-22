"""Keyless animated avatar, drawn in code.

The character is a flat 2D figure styled from the reference image's colours. Its mouth
follows the voice's loudness frame by frame, the eyes blink, the head bobs and tilts, and
the arms and hands act out the line's ``action`` (shrug, point, count, wave, lift, ...).
No API key and no network: frames are drawn with Pillow and piped straight into ffmpeg.

This is the "avatar drawn in code, mouth = loudness" approach. It does not reproduce the
exact person or artwork in the reference image; a video model provider does that.

Options (``providers.video.options``): ``pattern: web`` draws web lines on a masked head
and a chest emblem; ``short_side`` sets the drawing resolution (default 720).
"""

from __future__ import annotations

import colorsys
import math
import random
import re
import subprocess
import threading
import wave
import zlib
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .. import media
from .base import AnimateRequest, ProviderError, make_identity

FPS = 30
SUPERSAMPLE = 2  # draw at 2x and box-downscale: smooth edges without a vector library

RGB = tuple[int, int, int]
Point = tuple[float, float]

INK = (26, 24, 30)


# --------------------------------------------------------------------------- palette


@dataclass(frozen=True)
class Look:
    primary: RGB
    secondary: RGB
    head: RGB
    hands: RGB
    background: RGB
    spotlight: RGB
    masked: bool = False
    hair: RGB | None = None
    pattern: str = ""


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t),
            round(a[2] + (b[2] - a[2]) * t))


def _average(group: list[RGB]) -> RGB:
    n = len(group)
    return (sum(c[0] for c in group) // n, sum(c[1] for c in group) // n,
            sum(c[2] for c in group) // n)


def _is_skin(r: int, g: int, b: int) -> bool:
    return r > 95 and g > 40 and b > 20 and r > g > b and r - b > 15 and abs(r - g) > 15


def read_look(image: Path, pattern: str = "") -> Look:
    """Pick the figure's colours from the reference: the two most common saturated hues,
    and skin if enough of it shows (otherwise the head is drawn as a mask)."""
    with Image.open(image) as img:
        small = img.convert("RGB").resize((96, 96))
    raw = small.tobytes()
    pixels: list[RGB] = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]

    skin = [p for p in pixels if _is_skin(*p)]
    buckets: dict[int, list[RGB]] = {}
    for r, g, b in pixels:
        hue, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if sat < 0.35 or light < 0.12 or light > 0.88:
            continue
        buckets.setdefault(round(hue * 12) % 12, []).append((r, g, b))
    ranked = sorted(buckets.values(), key=len, reverse=True)

    primary = _average(ranked[0]) if ranked else (52, 96, 140)
    secondary = _average(ranked[1]) if len(ranked) > 1 else _mix(primary, INK, 0.45)
    masked = len(skin) / max(1, len(pixels)) < 0.06
    head = primary if masked else _average(skin)
    darks = [p for p in pixels if sum(p) < 180]
    hair = None if masked or len(darks) < 40 else _average(darks)

    background = _mix(secondary, (244, 242, 238), 0.84)
    return Look(
        primary=primary,
        secondary=secondary,
        head=head,
        hands=primary if masked else head,
        background=background,
        spotlight=_mix(background, (255, 255, 255), 0.55),
        masked=masked,
        hair=hair,
        pattern=pattern,
    )


# --------------------------------------------------------------------------- gestures


@dataclass(frozen=True)
class Arm:
    """Upper arm and forearm angles in degrees from straight down: positive swings away
    from the body, 180 points up, negative crosses in front of the body."""

    upper: float
    fore: float
    hand: str = "open"
    near: float = 1.0  # hand scale; above 1 reads as reaching toward the camera


@dataclass(frozen=True)
class Gesture:
    left: Arm
    right: Arm
    shoulders: float = 0.0
    tilt: float = 0.0
    wobble: float = 0.0
    wobble_hz: float = 1.6
    nod: float = 0.0
    prop: str = ""


GESTURES: dict[str, Gesture] = {
    "rest": Gesture(Arm(8, 4, "fist"), Arm(8, 4, "fist")),
    "talk": Gesture(Arm(20, 112, "open"), Arm(10, 30, "open"), wobble=9),
    "point": Gesture(Arm(10, 24, "fist"), Arm(18, -128, "point", near=1.35), wobble=3),
    "shrug": Gesture(Arm(30, 105, "palm"), Arm(30, 105, "palm"), shoulders=26, tilt=7),
    "count": Gesture(Arm(10, 24, "fist"), Arm(26, 166, "count"), wobble=3),
    "wave": Gesture(Arm(10, 24, "fist"), Arm(78, 170, "open"), wobble=20, wobble_hz=2.4),
    "lift": Gesture(Arm(148, 178, "fist"), Arm(148, 178, "fist"), wobble=7, wobble_hz=1.1,
                    prop="bar"),
    "salute": Gesture(Arm(10, 24, "fist"), Arm(130, 246, "flat"), tilt=-4),
    "spread": Gesture(Arm(40, 138, "open"), Arm(40, 138, "open"), shoulders=8),
    "chest": Gesture(Arm(10, 24, "fist"), Arm(14, -106, "flat"), tilt=5),
    "wrist": Gesture(Arm(16, -94, "fist"), Arm(14, -60, "point")),
    "nod": Gesture(Arm(14, 56, "open"), Arm(14, 56, "open"), nod=10),
    "laugh": Gesture(Arm(20, 64, "open"), Arm(20, 64, "open"), shoulders=12, tilt=-7,
                     nod=6),
    "think": Gesture(Arm(10, 24, "fist"), Arm(18, -152, "fist"), tilt=7),
    "thumbs": Gesture(Arm(10, 24, "fist"), Arm(28, 160, "thumb"), wobble=3),
}

# First match wins, so specific gestures come before generic words like "finger".
_KEYWORDS: list[tuple[str, str]] = [
    (r"salute", "salute"),
    (r"shrug|palms? up", "shrug"),
    (r"thumbs? up", "thumbs"),
    (r"point", "point"),
    (r"count|finger", "count"),
    (r"wave|waving", "wave"),
    (r"lift|barbell|weight|flex|dumbbell", "lift"),
    (r"spread|wide|open arms|arms out", "spread"),
    (r"chest|heart", "chest"),
    (r"wrist|watch", "wrist"),
    (r"laugh|giggle", "laugh"),
    (r"nod", "nod"),
    (r"chin|think|ponder|scratch", "think"),
]


def plan_gestures(action: str) -> list[str]:
    """'mimes lifting a barbell, then drops his arms and laughs' -> ['lift', 'laugh']."""
    parts = [p for p in re.split(r"\bthen\b|;|, and |\. ", action.lower()) if p.strip()]
    names = []
    for part in parts or [""]:
        found = next((name for pattern, name in _KEYWORDS if re.search(pattern, part)), "")
        names.append(found or "talk")
    return names


# --------------------------------------------------------------------------- audio


def loudness(audio: Path, frames: int) -> list[float]:
    """Per-frame mouth opening in 0..1 from the voice track."""
    tmp = audio.with_name(audio.stem + ".env.wav")
    media.ffmpeg("-i", audio, "-ac", "1", "-ar", "8000", "-c:a", "pcm_s16le", tmp)
    try:
        with wave.open(str(tmp), "rb") as wav:
            samples = array("h", wav.readframes(wav.getnframes()))
    finally:
        tmp.unlink(missing_ok=True)

    per_frame = max(1, 8000 // FPS)
    rms = []
    for i in range(frames):
        chunk = samples[i * per_frame:(i + 1) * per_frame]
        rms.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)) if chunk else 0.0)
    peak = sorted(rms)[int(len(rms) * 0.95)] if rms else 0.0
    level = [min(1.0, r / peak) if peak else 0.0 for r in rms]

    # fast attack, slower release: reads as speech instead of flicker
    out, current = [], 0.0
    for value in level:
        current += (value - current) * (0.7 if value > current else 0.35)
        out.append(0.0 if current < 0.08 else current)
    return out


# --------------------------------------------------------------------------- timing


def _smooth(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _blend(a: Gesture, b: Gesture, t: float) -> Gesture:
    def arm(p: Arm, q: Arm) -> Arm:
        return Arm(_lerp(p.upper, q.upper, t), _lerp(p.fore, q.fore, t),
                   q.hand if t > 0.5 else p.hand, _lerp(p.near, q.near, t))

    return Gesture(
        left=arm(a.left, b.left),
        right=arm(a.right, b.right),
        shoulders=_lerp(a.shoulders, b.shoulders, t),
        tilt=_lerp(a.tilt, b.tilt, t),
        wobble=_lerp(a.wobble, b.wobble, t),
        wobble_hz=b.wobble_hz,
        nod=_lerp(a.nod, b.nod, t),
        prop=b.prop if t > 0.5 else a.prop,
    )


@dataclass
class Scene:
    width: int
    height: int
    look: Look
    gestures: list[str]
    duration: float
    emotion: str
    seed: int
    blinks: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        rng = random.Random(self.seed)  # nosec B311 - blink timing, not security
        t = rng.uniform(0.6, 1.8)
        while t < self.duration:
            self.blinks.append(t)
            t += rng.uniform(2.2, 4.2)

    @property
    def span(self) -> float:
        return self.duration / len(self.gestures)

    def gesture_at(self, t: float) -> Gesture:
        index = min(len(self.gestures) - 1, int(t / self.span))
        current = GESTURES[self.gestures[index]]
        if index == 0:
            return _blend(GESTURES["rest"], current, _smooth(t / 0.35))
        previous = GESTURES[self.gestures[index - 1]]
        return _blend(previous, current, _smooth((t - index * self.span) / 0.35))

    def blink(self, t: float) -> float:
        for start in self.blinks:
            if start <= t < start + 0.16:
                return 1 - abs((t - start) / 0.08 - 1)
        return 0.0


# --------------------------------------------------------------------------- drawing


class _Canvas:
    """Design units around the head centre -> supersampled pixels. The frame is about
    900 units wide in portrait."""

    def __init__(self, width: int, height: int):
        w, h = width * SUPERSAMPLE, height * SUPERSAMPLE
        self.u = min(w, h * 9 / 16) / 900
        self.cx = w / 2
        self.cy = h * (0.34 if h >= w else 0.33)
        self.size = (w, h)

    def p(self, x: float, y: float) -> Point:
        return (self.cx + x * self.u, self.cy + y * self.u)

    def r(self, value: float) -> float:
        return value * self.u

    def box(self, x: float, y: float, rx: float, ry: float) -> list[float]:
        return [*self.p(x - rx, y - ry), *self.p(x + rx, y + ry)]


def _dot(d: ImageDraw.ImageDraw, c: _Canvas, at: Point, radius: float, fill: RGB) -> None:
    d.ellipse(c.box(at[0], at[1], radius, radius), fill=fill)


def _limb(d: ImageDraw.ImageDraw, c: _Canvas, a: Point, b: Point, width: float, fill: RGB,
          stroke: float = 6) -> None:
    """A capsule from a to b with an ink outline."""
    for w, colour in ((width + 2 * stroke, INK), (width, fill)):
        d.line([c.p(*a), c.p(*b)], fill=colour, width=round(c.r(w)))
        _dot(d, c, a, w / 2, colour)
        _dot(d, c, b, w / 2, colour)


def _toward(origin: Point, side: int, angle: float, length: float) -> Point:
    rad = math.radians(angle)
    return (origin[0] + side * math.sin(rad) * length, origin[1] + math.cos(rad) * length)


def _hand(d: ImageDraw.ImageDraw, c: _Canvas, wrist: Point, side: int, angle: float,
          arm: Arm, look: Look, count: int) -> None:
    k = arm.near
    palm = _toward(wrist, side, angle, 38 * k)

    def finger(offset: float, length: float, width: float = 30) -> None:
        tip = _toward(palm, side, angle + offset, length * k)
        _limb(d, c, palm, tip, width * k, look.hands)

    shape = arm.hand
    if shape == "point":
        finger(0, 92)
    elif shape == "count":
        for i in range(count):
            finger(-16 + i * 16, 82)
    elif shape == "thumb":
        finger(-90, 70, 34)
    elif shape in ("open", "palm"):
        for offset in (-30, -10, 10, 30):
            finger(offset, 64)
    elif shape == "flat":
        finger(0, 74, 62)

    d.ellipse(c.box(palm[0], palm[1], 54 * k, 54 * k), fill=look.hands, outline=INK,
              width=round(c.r(6)))
    if shape in ("open", "palm", "fist", "point", "count", "flat"):
        thumb = _toward(palm, side, angle + 75, 40 * k)
        _limb(d, c, palm, thumb, 30 * k, look.hands)
        d.ellipse(c.box(palm[0], palm[1], 48 * k, 48 * k), fill=look.hands)


def _web(d: ImageDraw.ImageDraw, c: _Canvas, centre: Point, rx: float, ry: float,
         colour: RGB, mid: float, unit: float) -> None:
    spokes = 12
    rings = (0.35, 0.62, 0.86)
    width = max(1, round(unit * 3.5))
    ends = []
    for i in range(spokes):
        a = 2 * math.pi * i / spokes + 0.13
        ends.append((math.cos(a), math.sin(a)))
        d.line([(mid + centre[0] * unit, mid + centre[1] * unit),
                (mid + (centre[0] + math.cos(a) * rx) * unit,
                 mid + (centre[1] + math.sin(a) * ry) * unit)], fill=colour, width=width)
    for ring in rings:
        for i in range(spokes):
            (ax, ay), (bx, by) = ends[i], ends[(i + 1) % spokes]
            pts = []
            for s in range(5):
                f = s / 4
                sag = 1 - 0.08 * math.sin(math.pi * f)  # threads sag toward the centre
                x = (ax + (bx - ax) * f) * rx * ring * sag
                y = (ay + (by - ay) * f) * ry * ring * sag
                pts.append((mid + (centre[0] + x) * unit, mid + (centre[1] + y) * unit))
            d.line(pts, fill=colour, width=width)


def _head(scene: Scene, c: _Canvas, mouth: float, blink: float) -> Image.Image:
    """The head on its own layer so it can tilt as one piece."""
    look = scene.look
    size = round(c.r(560))
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    u, mid = c.u, size / 2
    stroke = round(u * 7)

    def box(x: float, y: float, rx: float, ry: float) -> list[float]:
        return [mid + (x - rx) * u, mid + (y - ry) * u, mid + (x + rx) * u, mid + (y + ry) * u]

    rx, ry = 172, 198
    if look.hair and not look.masked:
        d.ellipse(box(0, -46, rx + 14, ry - 20), fill=look.hair, outline=INK, width=stroke)
    d.ellipse(box(0, 0, rx, ry), fill=look.head, outline=INK, width=stroke)
    if look.hair and not look.masked:
        d.chord(box(0, -70, rx - 6, 120), 180, 360, fill=look.hair)

    if look.masked and look.pattern == "web":
        _web(d, c, (0, 18), rx, ry, _mix(look.head, INK, 0.55), mid, u)
        d.ellipse(box(0, 0, rx, ry), outline=INK, width=stroke)

    surprised = scene.emotion in ("surprised", "excited", "curious", "shocked")
    open_eye = max(0.1, 1 - blink)
    if look.masked:
        for side in (-1, 1):
            half_h = (58 if surprised else 50) * open_eye
            cx, cy = side * 74, -30
            rot = math.radians(-22 * side)
            points = []
            for i in range(32):
                a = 2 * math.pi * i / 32
                px, py = math.cos(a) * 60, math.sin(a) * half_h
                px *= 1 + 0.32 * max(0.0, math.cos(a) * side)  # pointed outer corner
                py *= 1 - 0.25 * max(0.0, -math.cos(a) * side)  # flatter inner edge
                x = px * math.cos(rot) - py * math.sin(rot)
                y = px * math.sin(rot) + py * math.cos(rot)
                points.append((mid + (cx + x) * u, mid + (cy + y) * u))
            d.polygon(points, fill=INK)
            inner = [(mid + (p[0] - mid) * 0.82 + (cx * u) * 0.18,
                      mid + (p[1] - mid) * 0.82 + (cy * u) * 0.18) for p in points]
            d.polygon(inner, fill=(250, 250, 248))
    else:
        for side in (-1, 1):
            eye_h = (32 if surprised else 26) * open_eye
            d.ellipse(box(side * 62, -24, 30, eye_h), fill=(252, 252, 250), outline=INK,
                      width=stroke)
            if open_eye > 0.35:
                d.ellipse(box(side * 60, -20, 14, 15), fill=INK)
            brow_y = -78 - (14 if surprised else 0)
            d.line([mid + side * 34 * u, mid + (brow_y + 8) * u,
                    mid + side * 92 * u, mid + (brow_y - 4) * u], fill=INK,
                   width=round(u * 11))
        d.arc(box(0, 34, 18, 14), 20, 160, fill=INK, width=round(u * 5))

    mouth_y = 96
    if mouth > 0.06:
        open_h = 10 + 62 * mouth
        width = 56 - 12 * mouth
        d.ellipse(box(0, mouth_y, width, open_h / 2), fill=(66, 20, 30), outline=INK,
                  width=stroke)
        if mouth > 0.4:
            d.chord(box(0, mouth_y + open_h * 0.28, width * 0.6, open_h * 0.24), 180, 360,
                    fill=(212, 90, 102))
    else:
        d.arc(box(0, mouth_y - 18, 40, 22), 30, 150, fill=INK, width=round(u * 7))
    return layer


def _emblem(d: ImageDraw.ImageDraw, c: _Canvas, at: Point) -> None:
    x, y = at
    for side in (-1, 1):
        for i, (dx, dy) in enumerate(((40, -46), (52, -10), (50, 26), (36, 60))):
            knee = (x + side * dx * 0.6, y + dy * 0.5 - 10)
            foot = (x + side * dx, y + dy)
            w = round(c.r(8 if i in (0, 3) else 7))
            d.line([c.p(x, y + (i - 1.5) * 6), c.p(*knee), c.p(*foot)], fill=INK, width=w)
    d.ellipse(c.box(x, y - 22, 14, 16), fill=INK)
    d.ellipse(c.box(x, y + 14, 20, 30), fill=INK)


def draw_frame(scene: Scene, t: float, mouth: float) -> Image.Image:
    look = scene.look
    c = _Canvas(scene.width, scene.height)
    img = Image.new("RGB", c.size, look.background)
    d = ImageDraw.Draw(img)

    g = scene.gesture_at(t)
    breathe = 4 * math.sin(t * 2 * math.pi * 0.25)
    bob = -12 * mouth + 4 * math.sin(t * 2 * math.pi * 0.5)
    nod = g.nod * math.sin(t * 2 * math.pi * 2.0)
    lift = g.shoulders + breathe * 0.5

    d.ellipse(c.box(0, 140, 520, 520), fill=look.spotlight)

    top = 228 - lift
    stroke = round(c.r(7))
    # torso: a broad rounded block, side panels in the secondary colour
    d.rounded_rectangle([*c.p(-236, top - 30), *c.p(236, 1500)], radius=c.r(130),
                        fill=look.primary, outline=INK, width=stroke)
    for side in (-1, 1):
        panel = [c.p(side * 236, top + 120), c.p(side * 150, top + 260),
                 c.p(side * 165, 1500), c.p(side * 236, 1500)]
        d.polygon(panel, fill=look.secondary, outline=INK, width=stroke)
    if look.masked and look.pattern == "web":
        _emblem(d, c, (0, top + 190))
    d.rounded_rectangle([*c.p(-62, 110 + bob * 0.3), *c.p(62, top + 6)], radius=c.r(24),
                        fill=look.head, outline=INK, width=stroke)

    wobble = g.wobble * math.sin(t * 2 * math.pi * g.wobble_hz) * (0.4 + 0.6 * mouth)
    count = min(3, 1 + int((t % scene.span) / max(0.5, scene.span / 3)))
    arms = []
    for side, arm in ((-1, g.left), (1, g.right)):
        shoulder = (side * 196, top + 46)
        elbow = _toward(shoulder, side, arm.upper, 176)
        fore = arm.fore + wobble * side
        wrist = _toward(elbow, side, fore, 156)
        arms.append((side, shoulder, elbow, wrist, fore, arm))

    if g.prop == "bar":
        lw, rw = arms[0][3], arms[1][3]
        y = (lw[1] + rw[1]) / 2
        _limb(d, c, (lw[0] - 130, y), (rw[0] + 130, y), 22, (70, 74, 82))
        for x in (lw[0] - 118, rw[0] + 118):
            d.rounded_rectangle(c.box(x, y, 22, 64), radius=c.r(10), fill=(48, 50, 58),
                                outline=INK, width=stroke)

    def draw_arm(entry: tuple) -> None:
        side, shoulder, elbow, wrist, fore, arm = entry
        _limb(d, c, shoulder, elbow, 92, look.secondary)
        _limb(d, c, elbow, wrist, 82, look.primary)
        _hand(d, c, wrist, side, fore, arm, look, count)

    # hands raised to head height go in front of the head, the rest behind it
    raised = [a for a in arms if a[3][1] < 140]
    for entry in arms:
        if entry not in raised:
            draw_arm(entry)
    # deltoids cover the arm joints
    for side in (-1, 1):
        d.ellipse(c.box(side * 196, top + 46, 70, 70), fill=look.secondary, outline=INK,
                  width=stroke)

    head = _head(scene, c, mouth, scene.blink(t))
    head = head.rotate(g.tilt + 2.5 * math.sin(t * 1.3), resample=Image.Resampling.BICUBIC)
    hx, hy = c.p(0, bob + nod)
    img.paste(head, (round(hx - head.width / 2), round(hy - head.height / 2)), head)

    for entry in raised:
        draw_arm(entry)

    return img.reduce(SUPERSAMPLE)


# --------------------------------------------------------------------------- provider


class PuppetVideo:
    """Video provider that draws the character in code. No key, no network."""

    def __init__(self, model: str = "", options: dict[str, Any] | None = None):
        options = options or {}
        self.short_side = int(options.get("short_side", 720))
        self.pattern = str(options.get("pattern", ""))
        self.identity = make_identity("puppet", "v2", options)
        self._looks: dict[Path, Look] = {}
        self._lock = threading.Lock()

    def _look(self, image: Path) -> Look:
        with self._lock:
            if image not in self._looks:
                self._looks[image] = read_look(image, self.pattern)
            return self._looks[image]

    def animate(self, req: AnimateRequest, out: Path) -> Path:
        out_w, out_h = req.size
        scale = self.short_side / min(out_w, out_h)
        width, height = round(out_w * scale) // 2 * 2, round(out_h * scale) // 2 * 2
        frames = max(1, round(req.duration * FPS))
        mouth = loudness(req.audio, frames)
        scene = Scene(
            width=width,
            height=height,
            look=self._look(req.image),
            gestures=plan_gestures(req.action),
            duration=req.duration,
            emotion=req.emotion.lower(),
            seed=zlib.crc32(req.action.encode("utf-8")),
        )

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
            "-r", str(FPS), "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            str(out),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        stdin = proc.stdin
        if stdin is None:
            raise ProviderError("could not open a pipe to ffmpeg")
        broken = False
        try:
            for i in range(frames):
                stdin.write(draw_frame(scene, i / FPS, mouth[i]).tobytes())
        except BrokenPipeError:
            broken = True  # ffmpeg stopped early; its own message is raised below
        finally:
            stdin.close()
        stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.wait() != 0 or broken:
            raise ProviderError(f"puppet render failed: {stderr.strip()[-400:]}")
        return out
