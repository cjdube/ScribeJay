# LLM backend selection

*Split from LocalLLMAgent's `docs/llm-backend.md` — that doc also covers
Wren's chat/`bg_worker` backend chain (`WREN_*`), which has no counterpart
here. This is ScribeJay's own half.*

Every ScribeJay task's one model call goes through `scribejay/core/model.py`.
The default is local Ollama (the local-first design); a cloud backend
(currently Gemini) is opt-in per task.

## The privacy tradeoff — read this first

With the default Ollama backend, nothing about your day leaves the Mac mini
at runtime. **Selecting a cloud backend sends that task's gathered input to
the provider** (Google, for Gemini) — commit messages, sent-mail headers,
browsing history, whatever the task feeds the model. Switch deliberately, and
prefer the per-task override below over the global one so you only send what
you mean to.

## Selecting a backend

Two environment variables in `config/.env`, resolved as
**explicit per-task override → global default → `ollama`**:

- `SCRIBEJAY_LLM_BACKEND` — the global default for every task. Unset (or
  `ollama`) keeps everything local. Set to `gemini` to route all tasks to the
  cloud.
- `SCRIBEJAY_<TASK_KEY>_BACKEND` — overrides the global for one task only.
  This is the recommended way to use the cloud: point just the task where
  quality matters most at Gemini, leave the rest local.

Wired task keys: `AI_CHAT_LEARNINGS`, `CALENDAR_COLORIZER`,
`CLAUDE_TIME_BLOCKS`, `DAILY_CHROME_LEARNINGS`, `DAILY_COMMITS`,
`DAILY_YOUTUBE_LEARNINGS`. (`strava_download` and `daily_correspondence` use
no model at all — pure field mapping, see [architecture.md](architecture.md).)

```
# every task local except calendar_colorizer, which goes to the cloud
SCRIBEJAY_LLM_BACKEND=ollama
SCRIBEJAY_CALENDAR_COLORIZER_BACKEND=gemini
```

There is deliberately **no** fallback to any Wren-style `WREN_*` variable — a
silent fallback would hide a missed `.env` setup. Every run logs the backend
it resolved to and where that came from, on its first line:

```
backend: gemini (from SCRIBEJAY_CALENDAR_COLORIZER_BACKEND)
backend: ollama (default) (from unset)
```

## Applying a change

`config/.env` is read fresh by each launchd job on every run, so a task needs
no restart — the next run picks up the change. To exercise it immediately,
run the task by hand:

```bash
.venv/bin/python -m scribejay.daily_chrome_learnings
```

Verify by reading the task's log — the `backend:` line names the resolved
backend and its source, and a `gemini_chat model=…` or `ollama_chat model=…`
line confirms which one actually ran.

## Gemini config

- Key: `GEMINI_API_KEY` or `GOOGLE_API_KEY` (the SDK checks both).
- Dependency: `google-genai`, imported lazily so a purely local install never
  loads it.

## Adding another provider

Add a `_<provider>_chat` function in `scribejay/core/model.py` with the same
signature and canonical return shape as `_ollama_chat`/`_gemini_chat`, and a
branch in `_llm_chat`. Translate messages/format inside the new function;
don't touch the callers.

## Related

- [architecture.md](architecture.md) — the pipeline shape and the one
  model-call choke point
- [model-constraints.md](model-constraints.md) — `think=False`, budget, and
  the other small-model rules that apply regardless of backend
