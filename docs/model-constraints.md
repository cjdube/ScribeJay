# Writing code against a small local model

ScribeJay's default backend is a small on-device model. Three failure modes
have each cost weeks of silent breakage, and each one produced a rule in
[AGENTS.md](../AGENTS.md)'s *Small-local-model constraints* section.

AGENTS.md carries the rule and the measured result, because that's what's
needed while writing code. This file carries the evidence — the incidents, the
replay counts, the reasoning — because that's what's needed when someone is
about to decide the rule doesn't apply to their case. It usually does.

The three share a shape worth naming up front: **the failure is invisible.** In
every case the model returned something well-formed, the task exited 0, and no
alert fired. Nothing looked wrong until someone went looking.

## `think=False` for template-filling calls

**Rule:** pass `think=False` for any call that fills in a template — a
classification, a score, a fixed output format. Pass `logger=` too.

The model's thinking tokens come out of the same `num_predict` budget as its
answer. A call that reasons too long doesn't return a truncated answer — it
returns **empty content**, and the caller reports it as a parse failure. The
symptom points at parsing; the cause is budget.

**Incident.** `calendar_colorizer` crashed on an empty response. The same
budget exhaustion in a task that degrades instead of raising is worse: it
writes a short page and says nothing.

**Measured**, on a 40-item batch classification:

| | Runs producing output | Speed |
|---|---|---|
| thinking on | 0 of 3 | — |
| thinking off | 3 of 3 | 5x faster |

**Where thinking stays on: nowhere, today.** Every model call in this repo
passes `think=False`, because every one of them fills a template — a colorId, a
60-character blurb, a two-section daily page. Before turning it on for a new
call, measure it.

**"Analysis" is not the test.** This is the part that gets rationalized away. A
prompt that asks the model to *judge* something sounds like reasoning — but if
the thing being judged and the standard it is judged against are **both already
in the prompt**, that is comparison, not chain-of-thought. Turning thinking off
for that shape of call has also made the output cite the supplied material more
specifically, not less.

The test is not "is this analysis?" but **"must the model reason past what the
prompt already contains?"**

**Logging.** `scribejay/core/model.py:_ollama_chat` warns when a generation
reaches `num_predict` and names this cause — but only if it has a logger. A
call without `logger=` fails silently by construction.

**On the cloud backends:** only the Ollama path acts on `think`. Gemini's
equivalent knob is `SCRIBEJAY_GEMINI_THINKING_BUDGET` (already 0 by default,
and some models reject 0 — see `scribejay/core/backends/gemini.py`). OpenRouter
translates `think=False` into `reasoning: {enabled: false}`; see
[llm-backend.md](llm-backend.md).

## Degrading on bad model output must be logged

**Rule:** if a parse yields fewer results than inputs — not just zero — log it
at WARNING with the counts and the raw length.

"Degrade, don't crash" is right for a dead feed, whose absence is obvious in
the resulting page. Applied to model output it hides the bug instead, because
a page with 8 of 10 bullets looks exactly like a page with 10.

**Incidents**, all silent for weeks:

- a mis-copied event id matched no event, so that event was never coloured
- a batch scoring 8 of 10 items lost two of them with no trace
- a parse that degraded to an empty result still wrote its page, unscored

A scheduled task that silently produces *less* is worse than one that fails,
because only the failure pushes an alert.

**Reference implementations.** `calendar_colorizer.py:111` warns per event it
could not colour; `claude_time_blocks.py:187` warns when a block falls back to
`working session`; `daily_commits.py:174` warns when a day that *had* commits
produced a draft with no bullets. Each names the counts, so a thin page is
visible in the log sweep rather than only in the vault.

## Dates, arithmetic and structure stay in Python

**Rule:** never ask the model for a date, a duration, a total, or a URL. Gather
in Python, hand the model bounded text, and assemble the file in Python around
what it returns.

Weekday and day-window arithmetic is the classic case. It is exactly the shape
a language model answers confidently and wrongly, and the wrong answer is a
*valid* date — so nothing raises, nothing logs, and the only person who notices
is someone who already knew the right answer.

**Where this is enforced here:**

| Concern | Owner |
|---|---|
| Local day windows over UTC sources | `scribejay/core/dates.py:local_timezone` — [timezones.md](timezones.md) |
| Commit and line totals | `scribejay/journal.py:commit_totals_line` |
| Time-block boundaries and rounding | `scribejay/claude_time_blocks.py` |
| The video list and its links | `scribejay/journal.py`, via `core/urls.py:safe_url` |
| Section headers, ordering, file names | every task's write step |

**Scheme-validate every URL** before rendering it into Markdown or HTML. A
model handed a page title and a link will sometimes return a link that is
neither. `safe_url` returns `""` for anything that is not `http`/`https`, and
the caller renders the title unlinked instead — so a `javascript:` string can
never reach a written file, and a bad URL costs its link rather than the page.

The model's job is the sentence a human would have to write by hand. Everything
a `for` loop can do, a `for` loop does.

## Related

- [AGENTS.md](../AGENTS.md) — the rules themselves, in short form
- [opaque-identifiers.md](opaque-identifiers.md) — the id-copying rule, and why
  it compounds with the thinking budget
- [timezones.md](timezones.md) — the day-window half of "Python owns dates"
- [llm-backend.md](llm-backend.md) — the backend seam and each backend's knobs
