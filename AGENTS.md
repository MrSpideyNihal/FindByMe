# Instructions for AI agents

These apply to any agent working with FindByMe: Claude Code, Antigravity, Codex, Cursor
or others. The point is that the output depends on the project files, not on which agent
or model is driving.

## Making videos for a user

Drive the CLI. Do not reimplement stages, call provider APIs directly or write ffmpeg
commands by hand.

1. In the project folder (the one with `findbyme.yaml`), run `findbyme doctor`. Fix what
   it reports or tell the user which key is missing.
2. Write the script: `findbyme script "<topic>" --style <style> [--notes "..."]`.
   Use `findbyme styles` to see the options.
3. Show the user the script (`runs/<run>/plan.json`). Wait for approval. If they want
   changes, edit `plan.json` directly: keep the schema, keep `speaker` values to ids
   from `findbyme cast`, give every line a concrete `action`.
4. Render: `findbyme render <run>`. Failed lines are listed; finished ones are cached, so
   re-running only retries the failures.
5. Check `frames.jpg` (contact sheet) and `run.json` (`word_error` per line; anything
   above 0.15 deserves a listen) before calling it done.
6. Hand over `final.mp4`, `cover.jpg` and `post.txt`.

Several videos can be rendered in parallel from separate shells; runs never share files.

## Changing settings

Use `findbyme set <dotted.key> <value>`; it validates before saving. Examples:
`render.aspect 16:9`, `providers.llm.provider gemini`, `render.captions.enabled false`.

## Working on the code

- Python 3.10+, `pip install -e ".[ui,dev]"`.
- Run `pytest`, `ruff check .` and `fixmyslop scan .` before every commit. All three must
  be clean.
- New providers go in `findbyme/providers/`, implement the protocol in `base.py`, and get
  a mock-backed test. Never add a stage that only works with one vendor.
- The plan schema in `findbyme/plan.py` is the contract between the writer and the
  renderer. Changing it is a breaking change: update the prompt, the mock and the UI
  editor together.
- Keep the UI dependency-free (plain HTML, CSS, JS). Insert user text with
  `textContent`, never `innerHTML`.
