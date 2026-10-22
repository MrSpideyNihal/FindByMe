---
name: make-video
description: Make a character video with FindByMe in this folder. Use when the user asks for a video, reel, short, skit or explainer about any topic, wants to change a video's script, or wants to re-render one. You write the script when there is no script API key.
---

Follow AGENTS.md in this folder step by step. In short:

1. `findbyme doctor`
2. `findbyme brief "<topic>" --style <style>` (or `findbyme script ...` when a script key is set)
3. Read `runs/<run>/brief.md`, write the JSON script to `runs/<run>/plan.json`, run `findbyme plan <run>` until it passes
4. Show the lines to the user and wait for approval
5. `findbyme render <run>`, check `frames.jpg` and `run.json`, then hand over `final.mp4`, `cover.jpg` and `post.txt`
