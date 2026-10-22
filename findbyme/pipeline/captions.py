"""Stage 3: word-timed captions as an ASS subtitle file with the spoken word highlighted."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import CaptionStyle
from ..providers import Word


@dataclass(frozen=True)
class TimedWord:
    text: str
    start: float
    end: float


def align(script_text: str, heard: list[Word], duration: float) -> list[TimedWord]:
    """Show the script's spelling with the transcript's timing.

    If the transcript has the same number of words, timings map one to one. Otherwise the
    script words are spread over the spoken span, weighted by length.
    """
    shown = script_text.split()
    if not shown:
        return []
    if len(heard) == len(shown):
        return [TimedWord(s, w.start, w.end) for s, w in zip(shown, heard, strict=True)]

    start = heard[0].start if heard else 0.0
    end = heard[-1].end if heard else duration
    weights = [len(word) + 2 for word in shown]
    step = (end - start) / sum(weights)
    out, cursor = [], start
    for word, weight in zip(shown, weights, strict=True):
        out.append(TimedWord(word, cursor, cursor + weight * step))
        cursor += weight * step
    return out


def _ass_color(hex_color: str) -> str:
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    return f"&H00{b}{g}{r}".upper()


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = round(seconds * 100)
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def build_ass(
    segments: list[tuple[float, list[TimedWord]]],
    style: CaptionStyle,
    size: tuple[int, int],
) -> str:
    """``segments`` is a list of (offset in final video, words of that line)."""
    width, height = size
    font_size = style.size or round(min(width, height) * 0.068)
    alignment = {"bottom": 2, "middle": 5, "top": 8}[style.position]
    margin_v = round(height * (0.2 if style.position == "bottom" else 0.1))
    outline = max(2, font_size // 14)

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Word,{style.font},{font_size},{_ass_color(style.color)},"
        f"{_ass_color(style.highlight)},{_ass_color(style.outline)},&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},1,{alignment},"
        f"{round(width * 0.08)},{round(width * 0.08)},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    highlight = _ass_color(style.highlight)
    events = []
    per_group = style.words_per_group
    for offset, words in segments:
        for g in range(0, len(words), per_group):
            group = words[g : g + per_group]
            for i, word in enumerate(group):
                start = offset + word.start
                # hold each word until the next one starts, so captions never flicker
                end = offset + (group[i + 1].start if i + 1 < len(group) else word.end)
                parts = [
                    f"{{\\c{highlight}}}{_escape(w.text)}{{\\r}}" if j == i else _escape(w.text)
                    for j, w in enumerate(group)
                ]
                events.append(
                    f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Word,,0,0,0,,"
                    + " ".join(parts)
                )
    return header + "\n".join(events) + "\n"
