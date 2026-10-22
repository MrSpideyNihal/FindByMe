# Making videos in this folder

This folder is a FindByMe project: `findbyme.yaml` holds the cast, language and settings,
`cast/` holds each character's reference image and voice sheet, and every video gets its
own folder in `runs/`.

When the user asks for a video, use the `findbyme` command. Do not call video, voice or
text APIs yourself and do not edit media with ffmpeg by hand.

## Steps

1. Run `findbyme doctor` and tell the user about anything marked `!!`.
2. Pick a style from `findbyme styles` that fits the request (couple, family, kids,
   educational, explainer, skit, or one in `styles/`).
3. Start the video:
   - With a script API key: `findbyme script "<topic>" --style <style>` writes
     `runs/<run>/plan.json` itself. Go to step 5.
   - Without one (`doctor` shows `llm: auto: manual`): `findbyme brief "<topic>" --style
     <style>` and note the run name it prints. You are the script writer.
4. Write the script. Read `runs/<run>/brief.md` completely: it has the writing rules, the
   cast with their voice sheets, the language and the exact JSON shape. Write the JSON to
   `runs/<run>/plan.json`, then run `findbyme plan <run>` and fix anything it reports.
   - `speaker` must be a cast id from the brief.
   - Write `text` in the project language, exactly as it should be spoken.
   - Every line needs a concrete, visible `action` (hands, head, body). Vary them.
   - Stay factual; do not invent numbers or quotes.
5. Show the user the lines (`findbyme plan <run>` prints them) and wait for approval.
   Apply their edits to `plan.json` and run `findbyme plan <run>` again.
6. Run `findbyme render <run>`. If lines fail, run it again; finished lines are cached.
7. Look at `runs/<run>/frames.jpg` and `runs/<run>/run.json` (`word_error` above 0.15
   on a line means the voice may have misread it). Then give the user `final.mp4`,
   `cover.jpg` and the caption in `post.txt`.

Placeholder stages (no key) are listed before rendering. Tell the user which keys would
replace them; do not try to work around them.

## Other requests

- Change the cast: `findbyme cast`, `findbyme cast add`, `findbyme cast remove <id>`,
  or the Cast page in `findbyme ui`.
- Change settings: `findbyme set <key> <value>`, for example `render.aspect 16:9`.
- Start over: `findbyme reset` (asks first; videos in `runs/` are kept).
