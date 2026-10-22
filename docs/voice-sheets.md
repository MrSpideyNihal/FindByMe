# Voice sheets

A voice sheet is a few lines of plain text describing how one character talks. It is used
twice:

1. The script writer reads it, so the character's lines use their words and rhythm.
2. The TTS model gets it as voice direction (with `gpt-*` speech models), plus the emotion
   of each line.

Skip it and the character gets a neutral default.

## What to put in

- Age and where they are from, if it affects the accent.
- Pace and energy: slow and calm, fast and cheeky.
- Words or phrases they use a lot.
- How they mix languages, if they do.
- Anything they never do.

Keep it under ten lines. Specific beats long.

## Examples

A parent in a family channel:

```
Mother in her late thirties from Pune. Warm, unhurried, a little teasing with the kids.
Speaks Hinglish: Hindi for feelings and jokes, English for school and money words.
Says "accha, suno" before explaining something. Never shouts, even when annoyed.
```

A kid:

```
Eight-year-old boy, high energy, talks fast and runs sentences together.
Asks "but why?" a lot. Short words only. Laughs at his own jokes.
```

An educational host:

```
Young guy from Delhi, casual Hinglish like Indian tech creators. Friendly, fast,
a bit cheeky. Short pauses. Says "GPT" as "G P T". Reads the script exactly.
```

There is a template in [examples/voice-sheet.md](../examples/voice-sheet.md).
