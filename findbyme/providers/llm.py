"""Text models used for script writing and shot planning."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import ProviderError, make_identity
from .http import api_key, request

# Known OpenAI-compatible endpoints. Anything else works through `options.base_url`.
OPENAI_COMPATIBLE = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "ollama": ("http://localhost:11434/v1", ""),
}


class OpenAICompatibleLLM:
    def __init__(self, provider: str, model: str, options: dict[str, Any]):
        base_url, key_env = OPENAI_COMPATIBLE.get(provider, ("", "OPENAI_API_KEY"))
        self.base_url = options.get("base_url", base_url).rstrip("/")
        self.key_env = options.get("api_key_env", key_env)
        if not self.base_url:
            raise ProviderError(f"llm provider {provider!r} needs options.base_url")
        self.model = model
        self.temperature = float(options.get("temperature", 0.4))
        self.seed = options.get("seed", 7)
        self.identity = make_identity(provider, model, options)

    def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> str:
        headers = {"Authorization": f"Bearer {api_key(self.key_env)}"} if self.key_env else {}
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if self.seed is not None:
            body["seed"] = self.seed
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        url = f"{self.base_url}/chat/completions"
        data = request("POST", url, headers=headers, json=body).json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"unexpected chat response: {json.dumps(data)[:300]}") from exc


class AnthropicLLM:
    def __init__(self, model: str, options: dict[str, Any]):
        self.model = model or "claude-sonnet-5-5"
        self.temperature = float(options.get("temperature", 0.4))
        self.max_tokens = int(options.get("max_tokens", 8000))
        self.base_url = options.get("base_url", "https://api.anthropic.com").rstrip("/")
        self.identity = make_identity("anthropic", self.model, options)

    def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> str:
        if json_mode:
            system += "\n\nRespond with a single JSON object and nothing else."
        data = request(
            "POST",
            f"{self.base_url}/v1/messages",
            headers={
                "x-api-key": api_key("ANTHROPIC_API_KEY"),
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
        ).json()
        return "".join(block.get("text", "") for block in data.get("content", []))


_BRIEF_RE = re.compile(r"<brief>(.*?)</brief>", re.S)


class MockLLM:
    """Offline stand-in that writes a plausible script from the brief in the prompt.

    Lets the whole pipeline run without API keys, for demos and tests.
    """

    identity = "mock:llm"

    def __init__(self, model: str = "", options: dict[str, Any] | None = None):
        # Mocks take the same constructor arguments as real providers.
        del model, options

    def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> str:
        match = _BRIEF_RE.search(prompt)
        if not match:
            raise ProviderError("mock llm needs a <brief> block in the prompt")
        brief = json.loads(match.group(1))
        cast = brief["cast"]
        topic = brief["topic"]
        beats = [
            f"Okay, quick question: {topic}?",
            "Wait, I always wondered about that.",
            "So here is the simple version.",
            "That actually makes a lot of sense.",
            "And the best part is you can try it today.",
            "Let's do it together then.",
        ]
        actions = [
            ("curious", "leans in and raises one hand, palm open"),
            ("surprised", "eyebrows up, points at the other person"),
            ("confident", "counts on fingers while explaining"),
            ("happy", "nods slowly and smiles"),
            ("excited", "spreads both hands wide"),
            ("warm", "puts a hand on their chest and laughs"),
        ]
        count = max(2, min(len(beats), int(brief.get("max_lines", 6))))
        lines = []
        for i in range(count):
            emotion, action = actions[i % len(actions)]
            lines.append({
                "speaker": cast[i % len(cast)]["id"],
                "text": beats[i],
                "emotion": emotion,
                "action": action,
                "camera": "medium close-up" if i % 2 else "medium shot",
            })
        return json.dumps({
            "title": topic[:60].strip().capitalize(),
            "caption": f"{topic.strip().capitalize()}, explained in under a minute.",
            "hashtags": ["learn", "family", "explained"],
            "lines": lines,
        })
