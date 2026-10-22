import json
from pathlib import Path

import pytest

from findbyme import media, styles
from findbyme.config import CaptionStyle, Character, Project, default_providers
from findbyme.pipeline import Pipeline, PipelineError
from findbyme.pipeline.captions import align, build_ass
from findbyme.pipeline.script import build_prompt, extract_json, write_script
from findbyme.pipeline.voice import word_error_rate
from findbyme.plan import Plan
from findbyme.providers import ProviderError, Stack, Word

from .conftest import needs_ffmpeg


@pytest.fixture
def project(tmp_path: Path, portrait: Path) -> Project:
    for cid in ("mom", "kid"):
        (tmp_path / "cast" / cid).mkdir(parents=True)
        (tmp_path / "cast" / cid / "ref.png").write_bytes(portrait.read_bytes())
    proj = Project(
        characters=[
            Character(id="mom", name="Priya", role="mother", image="cast/mom/ref.png",
                      tts_voice="coral"),
            Character(id="kid", name="Aarav", role="son", image="cast/kid/ref.png",
                      tts_voice="ash"),
        ],
        providers=default_providers({}),
    )
    proj.render.short_side = 360
    proj.render.concurrency = 3
    proj._root = tmp_path
    proj.save()
    return proj


def test_builtin_styles_load():
    names = set(styles.available())
    assert {"couple", "family", "kids", "educational", "explainer", "skit"} <= names
    with pytest.raises(KeyError, match="unknown style"):
        styles.load("nope")


def test_project_style_overrides_builtin(tmp_path):
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "family.yaml").write_text("seconds: 20\ntone: dry\n", "utf-8")
    assert styles.load("family", tmp_path).seconds == 20


def test_prompt_carries_cast_and_language(project):
    project.language = "hinglish"
    prompt = build_prompt(project, styles.load("family"), "why we save money")
    assert "Hinglish" in prompt
    assert '"id": "mom"' in prompt
    assert "<brief>" in prompt


def test_extract_json_handles_fences_and_prose():
    assert extract_json('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('here: {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("no json here")


class FlakyLLM:
    identity = "flaky"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system, prompt, *, json_mode=False):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_write_script_retries_with_feedback(project):
    good = json.dumps({"title": "T", "lines": [{"speaker": "mom", "text": "Hello there."}]})
    bad_speaker = json.dumps({"title": "T", "lines": [{"speaker": "dad", "text": "Hi."}]})
    llm = FlakyLLM(["not json", bad_speaker, good])
    plan = write_script(project, llm, styles.load("family"), "topic")
    assert plan.lines[0].speaker == "mom"
    assert "rejected" in llm.prompts[2] and "dad" in llm.prompts[2]


def test_write_script_gives_up(project):
    llm = FlakyLLM(["nope"] * 3)
    with pytest.raises(ProviderError, match="no valid script"):
        write_script(project, llm, styles.load("family"), "topic")


def test_word_error_rate():
    assert word_error_rate("Hello, world!", "hello world") == 0
    assert word_error_rate("one two three four", "one three four") == 0.25
    assert word_error_rate("नमस्ते दोस्तों", "नमस्ते दोस्तों") == 0


def test_align_uses_script_spelling():
    heard = [Word("hello", 0.0, 0.4), Word("world", 0.5, 0.9)]
    out = align("Hello, World!", heard, 1.0)
    assert [(w.text, w.start) for w in out] == [("Hello,", 0.0), ("World!", 0.5)]

    spread = align("one two three", heard, 1.0)
    assert spread[0].start == 0.0 and spread[-1].end == pytest.approx(0.9)


def test_build_ass_highlights_each_word():
    words = align("a b c d", [], 2.0)
    ass = build_ass([(1.0, words)], CaptionStyle(words_per_group=2), (1080, 1920))
    dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue")]
    assert len(dialogue) == 4
    assert dialogue[0].startswith("Dialogue: 0,0:00:01.00,")
    assert "{\\c&H0000D4FF}a{\\r} b" in dialogue[0]
    assert "PlayResY: 1920" in ass


@needs_ffmpeg
def test_end_to_end_offline_render(project):
    pipeline = Pipeline(project)
    run_dir, plan = pipeline.write("why the sky is blue", "family")
    final = pipeline.render(run_dir)

    assert final.exists()
    expected = sum(
        media.duration(p) for p in sorted((run_dir / "audio").glob("*.wav"))
    ) + project.render.gap_seconds * len(plan.lines)
    assert media.duration(final) == pytest.approx(expected, abs=0.3)
    assert media.has_audio(final)
    for name in ("cover.jpg", "frames.jpg", "captions.ass", "post.txt", "run.json"):
        assert (run_dir / name).exists(), name
    manifest = json.loads((run_dir / "run.json").read_text())
    assert len(manifest["lines"]) == len(plan.lines)
    assert manifest["providers"]["video"] == "mock:video"


class CountingTTS:
    def __init__(self, inner):
        self.inner, self.calls = inner, 0
        self.identity = inner.identity

    def synthesize(self, *args, **kwargs):
        self.calls += 1
        return self.inner.synthesize(*args, **kwargs)


@needs_ffmpeg
def test_rerender_after_edit_reuses_cache(project):
    stack = Stack(project.providers)
    stack.tts = CountingTTS(stack.tts)
    pipeline = Pipeline(project, stack=stack)
    run_dir, plan = pipeline.write("cache test", "couple")
    pipeline.render(run_dir)
    first = stack.tts.calls

    plan.lines[0].text = "A completely different first line."
    plan.save(run_dir)
    pipeline.render(run_dir)
    assert stack.tts.calls == first + 1


@needs_ffmpeg
def test_failed_lines_are_reported(project):
    class BrokenVideo:
        identity = "broken"

        def animate(self, request, out):
            raise ProviderError("model offline")

    stack = Stack(project.providers)
    stack.video = BrokenVideo()
    pipeline = Pipeline(project, stack=stack)
    run_dir, _ = pipeline.write("failure", "explainer")
    with pytest.raises(PipelineError, match="model offline"):
        pipeline.render(run_dir)


def test_plan_rejects_unknown_speaker(project):
    plan = Plan(title="t", lines=[{"speaker": "ghost", "text": "boo"}])
    with pytest.raises(ValueError, match="ghost"):
        plan.check_cast(project)
