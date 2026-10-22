"""Stage 1: topic to validated script."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from ..config import Project, language_name
from ..plan import Plan, schema_hint
from ..providers import LLM, ProviderError
from ..styles import Style

# Measured on the voices FindByMe uses: Hindi and Hinglish Edge voices read about 1.9
# words a second, OpenAI English voices about 2.4. Scripts slightly short beat too long.
WORDS_PER_SECOND = 2.0

SYSTEM = """\
You write scripts for short, character-driven videos that are animated from reference
photos. Every line becomes one shot of the speaking character, so:

- Write spoken lines only. No narration, stage directions or sound effects inside "text".
- Each line is one to three short sentences that sound natural out loud.
- Match each character's voice sheet: vocabulary, pace, quirks.
- Give every line a concrete "action": what their hands, head and body visibly do while
  they talk (pointing, counting on fingers, shrugging, leaning in, laughing). Never leave
  a character standing still. Vary actions from line to line.
- Use only the character ids you are given as "speaker".
- Stay factual. Do not invent statistics, quotes or sources.
- Return one JSON object that matches the schema exactly. No markdown, no commentary.
"""


def build_prompt(
    project: Project, style: Style, topic: str, notes: str = "", seconds: int | None = None
) -> str:
    seconds = seconds or style.seconds
    cast = [
        {
            "id": c.id,
            "name": c.name,
            "role": c.role,
            "voice_sheet": project.voice_sheet(c),
        }
        for c in project.characters
    ]
    brief = {
        "topic": topic,
        "style": style.name,
        "language": language_name(project.language),
        "target_seconds": seconds,
        "target_words": round(seconds * WORDS_PER_SECOND),
        "min_lines": style.lines.min,
        "max_lines": style.lines.max,
        "cast": cast,
    }
    parts = [
        f"Write a {seconds}-second {style.name} video about: {topic}",
        f"Length matters: about {round(seconds * WORDS_PER_SECOND)} spoken words in total. "
        "The voice reads roughly two words a second; do not go over.",
        f"Language for every spoken line: {language_name(project.language)}.",
        f"Tone: {style.tone}" if style.tone else "",
        "Structure:\n" + "\n".join(f"- {step}" for step in style.structure)
        if style.structure else "",
        f"Look and setting: {style.look}" if style.look else "",
        f"Extra direction from the creator: {notes}" if notes else "",
        f"<brief>{json.dumps(brief, ensure_ascii=False)}</brief>",
        f"JSON schema:\n{schema_hint()}",
    ]
    return "\n\n".join(p for p in parts if p)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(raw: str) -> dict:
    """Pull the JSON object out of a reply, tolerating code fences and stray prose."""
    fenced = _FENCE_RE.search(raw)
    text = fenced.group(1) if fenced else raw
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("reply contains no JSON object")
    return json.loads(text[start : end + 1])


def brief_document(
    project: Project, style: Style, topic: str, notes: str = "", seconds: int | None = None
) -> str:
    """Everything a writer needs, as one self-contained prompt: paste it into any chat
    assistant, or let a coding agent read it, and the reply is the script."""
    return (
        f"{SYSTEM}\n"
        f"{build_prompt(project, style, topic, notes, seconds)}\n\n"
        "Reply with the JSON object only.\n"
    )


def parse_reply(project: Project, style_name: str, topic: str, raw: str) -> Plan:
    """Validate a script reply from any source: a model, an agent or a person."""
    data = extract_json(raw)
    data.update(topic=topic, style=style_name, language=project.language)
    plan = Plan.model_validate(data)
    plan.check_cast(project)
    return plan


def write_script(
    project: Project,
    llm: LLM,
    style: Style,
    topic: str,
    notes: str = "",
    seconds: int | None = None,
    attempts: int = 3,
) -> Plan:
    prompt = build_prompt(project, style, topic, notes, seconds)
    feedback = ""
    for _ in range(attempts):
        raw = llm.complete(SYSTEM, prompt + feedback, json_mode=True)
        try:
            return parse_reply(project, style.name, topic, raw)
        except (ValueError, ValidationError) as exc:
            # Tell the model exactly what was wrong; most models fix it on the next try.
            feedback = (
                f"\n\nYour previous reply was rejected: {str(exc)[:800]}\n"
                "Return the corrected JSON object only."
            )
    raise ProviderError(f"script model gave no valid script after {attempts} attempts{feedback}")
