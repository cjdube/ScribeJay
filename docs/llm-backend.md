# LLM backend selection

Every ScribeJay task's one model call goes through `scribejay/core/model.py`.
The default is local Ollama (the local-first design); a cloud backend (Gemini
or OpenRouter) is opt-in per task.

## The privacy tradeoff — read this first

With the default Ollama backend, nothing about your day leaves the Mac mini
at runtime. **Selecting a cloud backend sends that task's gathered input to
the provider** — commit messages, sent-mail headers, browsing history,
whatever the task feeds the model. That is Google for `gemini`, and for
`openrouter` it is OpenRouter *plus* whichever model provider they route you
to. Switch deliberately, and prefer the per-task override below over the
global one so you only send what you mean to.

## Selecting a backend

Two settings, resolved as
**explicit per-task override → global default → `ollama`**. Set either in
`scribejay settings`, or as an environment variable for one run
([configuration.md](configuration.md)):

- `SCRIBEJAY_LLM_BACKEND` — the global default for every task. Unset (or
  `ollama`) keeps everything local. Set to `gemini` or `openrouter` to route
  all tasks to the cloud.
- `SCRIBEJAY_<TASK_KEY>_BACKEND` — overrides the global for one task only.
  This is the recommended way to use the cloud: point just the task where
  quality matters most at a cloud model, leave the rest local.

Wired task keys: `AI_CHAT_LEARNINGS`, `CALENDAR_COLORIZER`,
`CLAUDE_TIME_BLOCKS`, `DAILY_CHROME_LEARNINGS`, `DAILY_COMMITS`,
`DAILY_YOUTUBE_LEARNINGS`. (`strava_download` and `daily_correspondence` use
no model at all — pure field mapping, see [architecture.md](architecture.md).)

```
# every task local except calendar_colorizer, which goes to the cloud
SCRIBEJAY_LLM_BACKEND=ollama
SCRIBEJAY_CALENDAR_COLORIZER_BACKEND=gemini
```

There is deliberately **no** fallback to any variable name from the codebase
this split out of — a silent fallback would hide a missed setup. Every run logs the backend
it resolved to and where that came from, on its first line:

```
backend: gemini (from SCRIBEJAY_CALENDAR_COLORIZER_BACKEND)
backend: ollama (default) (from unset)
```

## Applying a change

Settings are read fresh by each launchd job on every run, so a task needs no
restart — the next run picks up the change. To exercise it immediately, run the
task by hand:

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

## OpenRouter config

OpenRouter fronts every frontier model behind one OpenAI-compatible endpoint
and one key, which is why it is the only "bring your own frontier model" path
here rather than a separate backend per provider.

- Key: `OPENROUTER_API_KEY`, from <https://openrouter.ai/keys>. It resolves
  through the same layers as every other credential (env var -> Keychain), so
  the settings screen can store it in the Keychain.
- `OPENROUTER_MODEL` — a slug from <https://openrouter.ai/models>, e.g.
  `anthropic/claude-sonnet-5` (the default) or `openai/gpt-5`. A wrong slug is
  an HTTP error naming the slug, not a silent substitution.
- `OPENROUTER_MAX_OUTPUT_TOKENS` — ceiling on one reply, default 8192.
- Dependency: none. It is `requests`, which is already required.
- **It is billed per token.** The `backend:` line and the
  `openrouter_chat model=… served=…` line together say what you paid for on
  every run — `served` is what OpenRouter actually routed to, which need not
  be the slug you asked for.

### Using an OpenRouter preset

A [preset](https://openrouter.ai/settings/presets) bundles a model choice,
provider routing, a system prompt and generation parameters under one slug, and
OpenRouter accepts it *in place of* a model. So it needs no code here — set
`OPENROUTER_MODEL` to the slug with its `@preset/` prefix:

```
OPENROUTER_MODEL=@preset/free-model-preset
```

The `served=` half of the log line is what tells you where the preset actually
sent you:

```
openrouter_chat model=@preset/free-model-preset served=dots-studio/dots-3-note-preview:free
```

**A request parameter beats the preset's copy of it.** OpenRouter shallow-merges
the two and the request wins, so the `max_tokens` ScribeJay always sends
overrides a preset's own output cap, as does `reasoning` on a `think=False`
call. The preset's model, routing and system prompt are unaffected. If a preset
aims at free models with small output caps, lower
`OPENROUTER_MAX_OUTPUT_TOKENS` to match rather than expecting the preset to
supply it.

Reasoning is left at the model's own default, except on a call that passes
`think=False` (every template-filling call — see
[model-constraints.md](model-constraints.md)), which sends
`reasoning: {enabled: false}`. Reasoning tokens are billed and, on some
models, drawn from the same output budget, so a reasoning model left on can
return an empty draft. That case logs a WARNING naming it rather than writing
a blank page quietly.

## Adding another provider

Add a `_<provider>_chat` function in `scribejay/core/backends/<provider>.py`
with the same signature and canonical return shape as
`_ollama_chat`/`_gemini_chat`/`_openrouter_chat`, and a branch in
`model._llm_chat`. Add its rows to `core/schema.py` (the key with
`secret=True`) and the new value to `SCRIBEJAY_LLM_BACKEND`'s `choices`, and
an egress guard to `tests/conftest.py`. Translate messages/format inside the
new function; don't touch the callers.

Most of the time you do not need to: OpenRouter already reaches the model,
and a direct provider backend only earns its place when you need something
OpenRouter does not pass through.

## Related

- [architecture.md](architecture.md) — the pipeline shape and the one
  model-call choke point
- [model-constraints.md](model-constraints.md) — `think=False`, budget, and
  the other small-model rules that apply regardless of backend
