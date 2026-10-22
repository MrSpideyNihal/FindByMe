# FindByMe

Character-driven AI video pipeline. Set up your cast once (a reference image and a voice
sheet per character), then turn any topic into a finished, captioned MP4 where your
characters talk, gesture and react: couple videos, family skits, kids' explainers,
educational reels, or your own style.

```
topic ─► script ─► voice ─► captions ─► animated shots ─► final.mp4
         (LLM)     (TTS,    (word        (image + voice     cover.jpg
                   checked  timestamps)   to video model)   frames.jpg
                   by STT)                                  post.txt
```

- **One-time setup.** Say how many characters, give each a reference image and an optional
  voice sheet, pick a language. Done.
- **Characters that move.** Every line carries an `action` (hands, head, body), and the
  video model is asked for natural gestures, not a frozen talking head.
- **Any model, same output.** The script is validated against a fixed schema, prompts live
  in the repo, and every provider sits behind a small interface. Switching from OpenAI to
  Gemini, or between video models, is a config change.
- **Fast.** Each line is voiced, animated and fitted in its own worker. Outputs are cached
  by content, so editing one line and re-rendering only regenerates that line.
- **CLI and web UI.** Same project, same pipeline, either way.

## Install

Needs Python 3.10+ and [ffmpeg](https://ffmpeg.org/download.html) on your PATH.

```bash
pip install "findbyme[ui] @ git+https://github.com/MrSpideyNihal/FindByMe"
```

## Quick start

```bash
mkdir my-videos && cd my-videos
findbyme init
```

`init` asks:

1. How many characters will be in your videos?
2. For each one: name, role, reference image, voice sheet (type it, point to a `.md`
   file, or press Enter for the default).
3. Language and aspect ratio.

Then add API keys (copy `.env.example` to `.env`) and make a video:

```bash
findbyme make "why kids should save a little money every week" --style family
```

You see the script first and can render it, or stop and edit `runs/<run>/plan.json`, then:

```bash
findbyme render latest
```

Prefer a browser?

```bash
findbyme ui
```

Without any API keys every stage runs on offline mocks, so you can try the whole flow
(and the tests run) before spending anything. Mock output is a placeholder: a tone for
the voice and a slow pan over the reference image.

## Commands

| Command | What it does |
| --- | --- |
| `findbyme init` | One-time cast, voice and language setup |
| `findbyme make "topic" -s family` | Script, review, render |
| `findbyme script "topic" -s kids` | Script only, saved as `runs/<run>/plan.json` |
| `findbyme render [run\|latest]` | Render a plan; `--no-cache` regenerates everything |
| `findbyme cast` / `cast add` / `cast remove <id>` | Manage characters |
| `findbyme styles` | List styles |
| `findbyme set render.aspect 16:9` | Change any setting, validated |
| `findbyme doctor` | Check ffmpeg, cast files and API keys |
| `findbyme ui` | Web UI on http://127.0.0.1:7860 |

## Styles

Built in: `couple`, `family`, `kids`, `educational`, `explainer`, `skit`.

A style is a short YAML file: tone, structure, target length, line count and the look of
the setting. Add `styles/<name>.yaml` to your project to create a new one or override a
built-in:

```yaml
name: recipe
description: Two friends cook one dish, one explains, one tastes.
seconds: 50
lines: {min: 6, max: 12}
tone: Relaxed, hungry, a little competitive.
structure:
  - Show the finished dish first.
  - Three quick steps, each with one tip.
  - The taste test reaction.
look: Home kitchen, warm light, hands busy with ingredients.
```

## Voice sheets

A voice sheet describes how a character talks. It shapes both what the writer gives them
to say and how the TTS model says it. See [docs/voice-sheets.md](docs/voice-sheets.md).

## Providers

Each stage is configured in `findbyme.yaml`. `init` picks defaults from the keys it finds.

| Stage | Built-in providers |
| --- | --- |
| Script (`llm`) | `openai`, `anthropic`, `gemini`, `openrouter`, `groq`, `ollama`, any OpenAI-compatible URL |
| Voice (`tts`) | `openai`, `elevenlabs` |
| Timing (`stt`) | `openai` (Whisper word timestamps) |
| Animation (`video`) | `fal`, `replicate`, with any image+audio model via an input template |

Any stage can also load your own class with `provider: my_package.module:MyClass`.
Full reference, video model templates and examples: [docs/providers.md](docs/providers.md).

## Why the output does not depend on the model

- The writer must return one fixed JSON shape (`findbyme/plan.py`). Invalid replies are
  sent back with the exact error until they pass.
- Prompts, styles and voice sheets are files in the repo and the project, not something
  an agent improvises.
- `plan.json` is the hand-off point. Once a script is approved, rendering it uses no text
  model at all.
- Every voice take is transcribed and compared to the script; a take that misreads is
  regenerated.
- `run.json` records which provider and model produced each run.

## Using it from an AI agent

Claude Code, Antigravity, Codex or any other agent should drive the CLI, not reimplement
the steps. [AGENTS.md](AGENTS.md) has the instructions; `CLAUDE.md` and `GEMINI.md` point
to it.

## Project layout

```
my-videos/
  findbyme.yaml          cast, language, providers, render settings
  .env                   API keys (not committed)
  cast/<id>/             reference image and voice.md per character
  styles/                optional custom styles
  runs/<timestamp-topic>/
    plan.json            the script, editable
    audio/ shots/ segments/
    final.mp4 cover.jpg frames.jpg post.txt run.json
  .findbyme-cache/       content-addressed voice takes and shots
```

## Development

```bash
pip install -e ".[ui,dev]"
pytest
ruff check .
fixmyslop scan .
```

## License

MIT
