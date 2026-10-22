# Providers

Every stage reads one block in `findbyme.yaml`:

```yaml
providers:
  llm:   {provider: openai, model: gpt-4.1, options: {temperature: 0.4}}
  tts:   {provider: openai, model: gpt-4o-mini-tts}
  stt:   {provider: openai, model: whisper-1}
  video: {provider: fal, model: fal-ai/bytedance/omnihuman, options: {...}}
```

New projects use `provider: auto` for every stage: the provider is chosen from the keys
present when you render (see the order below), so adding a key to `.env` later is enough.
Set a provider by name to pin it, for example `findbyme set providers.llm.provider gemini`
or in the Settings page of the UI. With `auto`, a `model` or `options` you set still apply.

`auto` order: script uses Anthropic, then OpenAI, Gemini, OpenRouter; voice uses
ElevenLabs, then OpenAI, then free Edge voices; timing uses OpenAI; video uses fal, then
Replicate. A stage with nothing available falls back to an offline placeholder. Run `findbyme doctor` afterwards; it reports missing keys before you render.

## Script writer (`llm`)

| provider | key | notes |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | |
| `anthropic` | `ANTHROPIC_API_KEY` | default model `claude-sonnet-5-5` |
| `gemini` | `GEMINI_API_KEY` | uses Google's OpenAI-compatible endpoint |
| `openrouter` | `OPENROUTER_API_KEY` | any model on OpenRouter |
| `groq` | `GROQ_API_KEY` | |
| `ollama` | none | local models at `http://localhost:11434/v1` |
| `openai-compatible` | `options.api_key_env` | set `options.base_url` |
| `mock` | none | offline, for demos and tests |

Options: `temperature` (default 0.4), `seed` (default 7, sent where supported),
`base_url`, `api_key_env`, `max_tokens` (Anthropic).

Low temperature and a fixed seed keep scripts consistent between runs. The schema check
and retry loop in `findbyme/pipeline/script.py` keeps them consistent between models.

## Voice (`tts`)

| provider | key | `tts_voice` on each character |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | an OpenAI voice name: `coral`, `ash`, `sage`, `verse`, `ballad`, `echo`, `shimmer`, `alloy`, ... |
| `elevenlabs` | `ELEVENLABS_API_KEY` | an ElevenLabs voice id |
| `edge` | none | an Edge voice name such as `hi-IN-MadhurNeural` (`edge-tts --list-voices`); otherwise one is picked for the language |

Edge voices are free and need no key, but ignore voice sheets and emotions. They report
their own word timings, so captions are exact without a transcription key.

With `gpt-*` speech models the character's voice sheet and the line's emotion are sent as
voice direction. ElevenLabs options: `voice_settings` (stability, similarity_boost, ...).

Settings that affect quality:

- `render.voice_takes` (default 2): maximum takes per line.
- `render.max_word_error` (default 0.15): a take whose transcript differs from the script
  by more than this triggers another take. The best take wins.

## Timing (`stt`)

`openai` with `whisper-1` returns word timestamps used for captions and the take check.
The script is passed as a prompt so names and spellings stay consistent.

## Animation (`video`)

Video models change fast and each names its inputs differently, so the request body is a
template:

```yaml
video:
  provider: fal
  model: fal-ai/bytedance/omnihuman
  options:
    inputs:
      image_url: "{image}"
      audio_url: "{audio}"
    output: video.url        # where the video URL is in the response
    timeout: 1200            # seconds to wait for one shot
```

Placeholders:

| placeholder | value |
| --- | --- |
| `{image}` | the character's reference image as a data URI |
| `{audio}` | the line's chosen voice take as a data URI |
| `{prompt}` | shot direction built from the line's action, emotion and camera |
| `{duration}` | line length in seconds (stays a number) |
| `{aspect}` | `9:16`, `16:9`, `1:1` or `4:5` |

Without a video key, `auto` uses `puppet`: a flat 2D character drawn in code with the
reference image's colours. The mouth follows the loudness of the voice, the eyes blink, the
head bobs and tilts, and each line's `action` picks the arm and hand gestures (shrug,
point, count on fingers, wave, lift, salute, hand on chest, and so on). Options:
`pattern: web` for a web-patterned mask and chest emblem, `short_side` for the drawing
resolution (default 720).

Pick a model that takes an image and audio and returns a talking, moving person
(audio-driven avatar models). Models that also accept a text prompt get better gestures:
add `prompt: "{prompt}"` to `inputs`. Check each model's page on fal or Replicate for its
exact input names and the output path, then copy them into the template.

Replicate works the same way; `model` is `owner/name` or `owner/name:version`, and
`output` is usually empty (the output is the URL itself).

Whatever length the model returns, each shot is trimmed or held on its last frame to
exactly match its line, and the voice track always comes from the chosen take, so lip
sync is checked against one audio source.

## Your own provider

Any stage can load a class from your own code:

```yaml
video:
  provider: my_studio.video:LocalAvatar
  model: v2
  options: {gpu: 0}
```

The class is constructed as `LocalAvatar(model, options)` and needs an `identity` string
(used in cache keys) plus the stage's method:

```python
class LocalAvatar:
    def __init__(self, model: str, options: dict):
        self.identity = f"local-avatar:{model}"

    def animate(self, request, out):          # findbyme.providers.AnimateRequest
        ...                                   # write an mp4 to `out`
        return out
```

The other interfaces are in `findbyme/providers/base.py`:
`LLM.complete(system, prompt, json_mode)`,
`TTS.synthesize(text, voice, instructions, language, out)`,
`STT.transcribe(audio, language, hint)`.
