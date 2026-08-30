"""ScribeJay — the journaling agent.

ScribeJay keeps the record of what actually happened. Strava activities logged
onto the calendar, yesterday's events colour-coded after the fact, Claude/Codex
working time turned into AI Session Time Blocks, and a daily page in the
Obsidian vault built from Chrome history, YouTube Likes, Claude/Codex/Gemini
chats and the commits made.

## Shape

ScribeJay is a PIPELINE agent, not a tool-calling one: gather -> one
`complete_text()` call -> write. It has no tool registry and no `advance()`.
Keep it that way — a tool budget built for an interactive turn has no reason
to exist here.

Journaling is "write down what was done". Applying yesterday's activity to
notes and projects is reasoning, and a different job; it does not belong in
this repo.

## Standalone

`scribejay/core/` is ScribeJay's own settings/model/logging/notify/store/http/
dates/google seam — nothing here imports from another repo. See
docs/architecture.md for the module layout.

## Model

ScribeJay resolves its own backend (`scribejay/core/model.py`) —
`SCRIBEJAY_LLM_BACKEND` and the per-task `SCRIBEJAY_<TASK>_BACKEND`
overrides. Local Ollama by default; Gemini and OpenRouter are opt-in per task.
See docs/llm-backend.md.
"""
