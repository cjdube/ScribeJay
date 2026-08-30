"""ScribeJay — the journaling agent.

ScribeJay keeps the record of what actually happened. Strava activities logged
onto the calendar, yesterday's events colour-coded after the fact, Claude/Codex
working time turned into AI Session Time Blocks, and a daily page in the
Obsidian vault built from Chrome history, YouTube Likes, Claude/Codex/Gemini
chats and the commits made.

Wren (in the sibling LocalLLMAgent repo) is the interactive agent: she READS
the record ScribeJay writes — through the calendar and the wiki — and acts on
request. That is the seam. **ScribeJay writes the record, Wren reads it.**
Wren's tasks/daily_synthesis.py is deliberately NOT journaling and stays with
her: journaling is "write down what was done"; synthesis applies yesterday's
activity to notes and projects, which is reasoning.

## Shape

ScribeJay is a PIPELINE agent, not a tool-calling one: gather -> one
`complete_text()` call -> write. It has no tool registry and no `advance()`.
Keep it that way — the whole point of the split from Wren was that a tool
budget built for an interactive turn has no reason to exist here.

## Standalone

ScribeJay runs with no Wren installed. `scribejay/core/` is its own
settings/model/logging/notify/store/http/dates/google seam — nothing here
imports from a sibling repo. See docs/architecture.md for the module layout,
and docs/reviews/scribejay-split-plan.md (LocalLLMAgent repo) for how the
split happened, if you need the history.

## Model

ScribeJay resolves its own backend (`scribejay/core/model.py`) —
`SCRIBEJAY_LLM_BACKEND` and the per-task `SCRIBEJAY_<TASK>_BACKEND`
overrides. Local Ollama by default; a future OpenRouter backend is one
environment variable and one function away. See docs/llm-backend.md.
"""
