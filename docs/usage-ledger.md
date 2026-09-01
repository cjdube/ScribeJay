# The usage ledger

Every model call ScribeJay makes appends one JSON line to
`<SCRIBEJAY_LOGS_DIR>/usage.jsonl`. Nothing in this repo reads it.

## Why it exists

The numbers were always there. `scribejay/core/model.py` has logged
`ollama_chat model=... prompt_tokens=... eval_tokens=...` on every local call
for months, and the Gemini and OpenRouter backends log the same. But all of it
is prose inside a file that rotates at 8MB, so nothing can sum it and none of
it survives. The ledger keeps the same numbers as data.

## Where the row is written

`scribejay/core/model.py:_llm_chat` — the one seam all three backends already
pass through. That is the point: a fourth backend cannot be added and then
quietly go unmeasured.

Each backend attaches its counts to the message it returns under a private
`"_usage"` key, and `_llm_chat` **pops** it off before the message reaches
`complete_text`. The pop is load-bearing even though this repo is single-turn:
the key is private, callers get the message dict verbatim, and a caller that
keeps it is one refactor away.

A call that raises is still recorded, with `ok=false` and the exception
string, before the exception propagates unchanged.

## The row

One JSON object per line, appended, never rewritten in place. **The field
names are a contract** — a reader outside this repo depends on them.

| Field | Type | Value |
| --- | --- | --- |
| `ts` | string | Local ISO-8601 to the second. Naive local, matching what `logging` writes beside it. |
| `agent` | string | Always `"scribejay"`. The only field that says which codebase the row came from. |
| `task` | string | `logger.name` — `setup_logger` names each logger after its task. |
| `caller` | string | `"complete_text"`, the one entry point. |
| `backend` | string | `"ollama"`, `"gemini"` or `"openrouter"`. |
| `model` | string | The model the backend used. For OpenRouter, the model it **served**, not the slug asked for. |
| `prompt_tokens` | int or null | |
| `output_tokens` | int or null | OpenRouter reports this as `completion_tokens`; it is mapped. |
| `thinking_tokens` | int or null | Gemini only. Null is normal — the API often reports none. |
| `num_ctx` | int or null | Ollama only. |
| `duration_ms` | int | Wall clock measured around the call here, not reported by the backend. |
| `finish_reason` | string or null | `"stop"` / `"length"` (Ollama, OpenRouter), `"MAX_TOKENS"` (Gemini). |
| `tools_offered` | int | Always `0` — `complete_text` is tool-free by design. Kept so the row shape matches. |
| `ok` | bool | `false` when the call raised. |
| `error` | string or null | `"ClassName: message"` on the failure path. |
| `cost_usd` | float or null | `0.0` for ollama. Estimated for the cloud backends. **`null` when the model is not in the price table.** |

## Cost is an estimate, and `null` is not zero

`_PRICES` in `scribejay/core/usage_ledger.py` is a hand-maintained table of USD
per **million** tokens, keyed by model-name prefix, longest prefix wins. It
goes stale. The provider's own console is the billing record.

Two rules worth stating out loud:

- **Thinking tokens are not added on top.** Every provider here already counts
  them inside its output total; adding them again double-bills exactly the
  calls that reason the most.
- **A model that matches nothing records `cost_usd = null`, never `0.0`.**
  "We don't know what this cost" and "this was free" are different answers, and
  a zero reads as the second. The reader counts nulls separately, which is how
  a stale table announces itself.

OpenRouter slugs are vendor-qualified (`anthropic/claude-...`,
`google/gemini-...`) and need their own entries. Until one is added, OpenRouter
rows come through unpriced — which is correct, and visible.

## Size

The file prunes itself on write. Once it passes `SCRIBEJAY_USAGE_MAX_BYTES`
(default 5,000,000) it is rewritten inside the same lock, keeping only rows
newer than `SCRIBEJAY_USAGE_RETENTION_DAYS` (default 90).

Size is the trigger and age is the rule: a `stat()` per call is cheap, a 5MB
rewrite is not, so a busy day pays the rewrite about once and a quiet one never
pays it at all. A row whose `ts` is missing or unparseable is **kept** — "I
can't tell how old this is" is not evidence that it is old.

## Two things it will not do

- **It never raises.** `record()` wraps its whole body and logs at DEBUG.
  Accounting is not worth a failed journal entry.
- **It is never read from here.** There is deliberately no "how many tokens did
  I use" task or CLI command in this repo. The ledger is written here and read
  somewhere else.

## Where the file lands

`SCRIBEJAY_LOGS_DIR` currently resolves to `<checkout>/logs` on this machine,
because `~/.scribejay/config.json` sets `"logs_dir": "logs"` and a relative path
resolves against the checkout when one exists. The schema default is
`~/.scribejay/logs`, which is where an installed-as-a-tool copy puts it.

Either way the ledger sits beside the run logs, which is the right home for it.
Both `logs/usage.jsonl` and `logs/*.lock` are gitignored: neither existing rule
covered them, since `logs/*.log*` does not match `.jsonl` and `config/*.lock`
does not reach `logs/`.
