"""Stage 2: voice every line, check each take against the script, keep the best one."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .. import media
from ..cache import Cache, key, place
from ..config import Character, Project, stt_language
from ..plan import Line
from ..providers import STT, TTS, Word
from ..providers.tts import words_sidecar

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class LineAudio:
    path: Path
    duration: float
    words: list[Word]
    word_error: float
    takes: int


def tokens(text: str) -> list[str]:
    return [t.casefold() for t in _TOKEN_RE.findall(text)]


def word_error_rate(expected: str, heard: str) -> float:
    """Levenshtein distance over words, divided by the script's word count."""
    ref, hyp = tokens(expected), tokens(heard)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def direction(project: Project, char: Character, line: Line) -> str:
    """Voice direction sent to the TTS model: the character's sheet plus this line's mood."""
    return (
        f"{project.voice_sheet(char)}\n"
        f"Emotion for this line: {line.emotion}.\n"
        "Read the text exactly, word for word."
    )


def _words_path(audio: Path) -> Path:
    return audio.with_suffix(".words.json")


def _load_words(path: Path) -> list[Word]:
    return [Word(**w) for w in json.loads(path.read_text(encoding="utf-8"))]


def voice_line(
    project: Project,
    tts: TTS,
    stt: STT,
    cache: Cache,
    line: Line,
    out: Path,
) -> LineAudio:
    char = project.character(line.speaker)
    instructions = direction(project, char, line)
    lang = stt_language(project.language)
    settings = project.render

    best: tuple[float, Path, list[Word]] | None = None
    take = 0
    for take in range(1, settings.voice_takes + 1):
        digest = key(tts.identity, stt.identity, char.tts_voice, instructions, line.text, take)
        cached = cache.hit("voice", digest, ".wav")
        if cached and _words_path(cached).exists():
            wav, words = cached, _load_words(_words_path(cached))
        else:
            wav = cache.path("voice", digest, ".wav")
            raw = tts.synthesize(
                line.text,
                voice=char.tts_voice,
                instructions=instructions,
                language=project.language,
                out=wav.with_suffix(".tts.wav"),
            )
            media.to_wav(raw, wav)
            raw.unlink(missing_ok=True)
            spoken = words_sidecar(raw)
            if spoken.exists() and stt.identity.startswith("mock"):
                # The voice provider reported its own word timings; better than a guess.
                words = _load_words(spoken)
            else:
                words = stt.transcribe(wav, language=lang, hint=line.text)
            spoken.unlink(missing_ok=True)
            _words_path(wav).write_text(
                json.dumps([w.__dict__ for w in words], ensure_ascii=False), "utf-8"
            )

        error = word_error_rate(line.text, " ".join(w.text for w in words))
        if best is None or error < best[0]:
            best = (error, wav, words)
        if error <= settings.max_word_error:
            break

    assert best is not None
    error, wav, words = best
    place(wav, out)
    return LineAudio(out, media.duration(out), words, error, take)
