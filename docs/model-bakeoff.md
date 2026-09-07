# Gemini vs Gemma — what the local model costs the two learnings pages

Run on 7 September 2026, against 7 Chrome days and 7 YouTube days.

**Result: keep Gemini, and fall back to Ollama on a 429.** The blind read went
11–1–2 to Gemini across 14 pairs, and 7–0 on Chrome. Gemini costs about
**$0.09 a month** for both jobs, so there is nothing to save by moving. The
failure that started this was an empty prepaid balance, and a fallback fixes
that without giving up the better page.

## 1. Why we ran it

On 3–6 September 2026 both daily learnings tasks failed. The Gemini API
returned `429 RESOURCE_EXHAUSTED` — "Your prepayment credits are depleted".
Seven failed rows are in `logs/usage.jsonl`, and the same days are in
`logs/daily_youtube_learnings.log`.

Both tasks select Gemini through `model.per_task` in `~/.scribejay/config.json`:

| Setting | Value |
|---|---|
| `model.per_task.daily_chrome_learnings` | `gemini` |
| `model.per_task.daily_youtube_learnings` | `gemini` |
| `model.gemini_model` | `gemini-3.7-flash` |
| `model.ollama_model` (the local option) | `gemma4:26b-mlx` |
| Total Gemini spend in the ledger at that point | **$0.12** |

Note the size of that number. **This was a reliability failure, not a cost
one.** Twelve cents is the whole lifetime spend. Nothing about the outage
argues for a cheaper model; it argues for a task that survives a dead API.

So the question was narrow: if both tasks move to the local Gemma model, how
much page quality is lost?

## 2. Method

Two arms, both tasks, seven days each:

| Arm | Backend | Model |
|---|---|---|
| A | `gemini` | `gemini-3.7-flash` |
| B | `ollama` | `gemma4:26b-mlx` |

**Whole-pipeline arms.** For `daily_chrome_learnings` the arm's own backend
wrote both the per-page notes and the final draft — 2 to 4 model calls a day.
That is what "switch to Gemma" actually means. YouTube is one call a day.

**One gather and one fetch per day, shared.** `gather()` ran once per day and
`web_fetch.fetch_pages()` ran once; both arms drafted from the identical frozen
data and the identical page list. The fetcher was not the variable. Both arms
recorded the same call count on every day, which is the check that this held.

**Same prompts, same `think=False`.** The harness imported
`DRAFT_SYSTEM_PROMPT` and `SUMMARY_SYSTEM_PROMPT` from the task modules rather
than copying prompt text.

**Blinded.** Each day's two drafts were written as `P` and `Q` under a fresh
per-day shuffle, with the mapping in a `-key.json` that stayed closed until the
read finished. Craig read all 14 pairs on a page that showed the drafts and
nothing else — no timings, no scores, no arm names.

**The caveat, stated up front:** the automated score is a guard rail. The blind
read decides. [docs/web-fetch.md](web-fetch.md) reached the same conclusion for
the same reason, and this run is a sharper example of it — see section 5.

### Two corrections made mid-run

**Quiet days measure nothing.** The first Chrome pass drew 4 of 7 days with
about two visits each. Two models writing about two links write the same page.
A `MIN_SITES = 5` floor now walks past those days, and Chrome was re-run. The
seven days reported here all cleared it.

**The score was reading Python's work.** The drafters originally scored the
assembled page. But `pages_read_section()` and `videos_section()` are written by
deterministic Python, so scoring the page counted Python's bullets as the
model's and scored the model's own inputs as "grounded". The drafters now return
the model text separately from the page, and only the model text is scored.

## 3. Reliability — the column that answers the original problem

| | Chrome A (Gemini) | Chrome B (Gemma) | YouTube A (Gemini) | YouTube B (Gemma) |
|---|---|---|---|---|
| Failed calls | 0 | 0 | 0 | 0 |
| Days with no page | 0 | 0 | 0 | 0 |
| Template-clean drafts | 5 / 7 | 5 / 7 | **7 / 7** | 5 / 7 |
| Mean seconds per day | 4.3 | 10.0 | 3.1 | 2.1 |
| Worst day, seconds | 6.4 | **14.0** | 9.6 | 3.0 |

Neither arm ever produced an empty or unusable draft. Gemma is slower on
Chrome — it makes the same 2–4 calls but each one takes longer — and 14 seconds
is still nothing against a 5:15 job that has the morning to itself. **Speed is
not a reason to reject either arm.**

Gemma's two YouTube template misses are the same miss twice: the prompt asks for
2–4 theme bullets and it wrote 1. On 3 September only one video was Liked, so
one bullet is arguably the honest answer and the prompt is what is wrong. On
21 August there were more, and the single bullet lost material.

The important reliability number is not in this table. It is that arm A depends
on a prepaid balance that hit zero and took four mornings with it, and arm B
depends on nothing outside the Mac mini.

## 4. Cost and tokens

Per day, mean over 7 days. Chrome totals cover all 2–4 calls.

| | Chrome A (Gemini) | Chrome B (Gemma) | YouTube A (Gemini) | YouTube B (Gemma) |
|---|---|---|---|---|
| Prompt tokens | 4,638 | 4,651 | 434 | 448 |
| Output tokens | 473 | 342 | 100 | 80 |
| Cost per day | $0.00257 | $0.00 | $0.00038 | $0.00 |
| Projected per month | **$0.077** | $0.00 | **$0.011** | $0.00 |

Both jobs together cost Gemini about **$0.09 a month**, or about **$1.06 a
year**. The prompt token counts are within 0.3% of each other, which is the
second check that both arms saw the same input.

There is no cost case for moving. A year of Gemini for both tasks costs less
than the outage cost in lost mornings.

## 5. Auto-score — and why it is not the decider

Mean over 7 days.

| | Chrome A (Gemini) | Chrome B (Gemma) | YouTube A | YouTube B |
|---|---|---|---|---|
| Bullets per day | 5.1 | 3.4 | 2.0 | 1.7 |
| Mean bullet chars | 227 | 161 | 247 | 218 |
| Grounded % | 48.6 | **57.4** | 0.0 | 0.0 |
| Specific % | 22.6 | 20.7 | 0.0 | 0.0 |
| Repeat % | 0.0 | 0.0 | 0.0 | 0.0 |

Two things to read here, and both are about the score rather than the models.

**On Chrome the score points the wrong way.** Gemma scores 8.8 points *better*
on grounding, and lost the blind read 7–0. Grounding counts how much of a
bullet's wording appears verbatim in the source. A shorter, more literal bullet
scores higher, and Gemma's bullets are 29% shorter. The metric rewards
restating the input; the reader wants synthesis. `docs/web-fetch.md` said the
automated score could not separate three arms. This run is worse than that: it
separates two arms and gets the order backwards.

**On YouTube the score is meaningless, not just weak.** Both arms read 0.0%
grounded on every day. That is not a tie — the YouTube prompt asks for *themes
that group videos*, so a good theme name ("Stakeholder Management") never
appears verbatim in any video title by construction. The number cannot be
anything but zero. It should not be computed for this task.

The repeat check was the one thing worth having: the YouTube prompt warns Gemma
against looping, and 0.0% on both arms says it did not.

## 6. The blind read

14 pairs, letters shuffled per day, key opened afterwards.

| Day | Task | Chose | Was |
|---|---|---|---|
| 2026-08-28 | Chrome | Q | Gemini |
| 2026-08-29 | Chrome | P | Gemini |
| 2026-08-30 | Chrome | P | Gemini |
| 2026-08-31 | Chrome | P | Gemini |
| 2026-09-01 | Chrome | Q | Gemini |
| 2026-09-02 | Chrome | P | Gemini |
| 2026-09-03 | Chrome | P | Gemini |
| 2026-08-15 | YouTube | P | **Gemma** |
| 2026-08-21 | YouTube | P | Gemini |
| 2026-08-23 | YouTube | Q | Gemini |
| 2026-08-25 | YouTube | Q | Gemini |
| 2026-08-28 | YouTube | tie | — |
| 2026-09-01 | YouTube | tie | — |
| 2026-09-03 | YouTube | Q | Gemini |

**Tally**

| | Gemini | Gemma | No difference |
|---|---|---|---|
| Chrome | **7** | 0 | 0 |
| YouTube | 4 | 1 | 2 |
| Overall | **11** | 1 | 2 |

A 7–0 sweep on Chrome is not a style preference. The samples below show what
the reader was reacting to. YouTube at 4–1–2 is close enough that a Gemma page
there is a real page, not a degraded one — which is what makes a fallback
workable.

## 7. Side by side

### Chrome, 30 August — the widest gap

Gemini (arm A):

> **OpenRouter:** Evaluated API usage and configuration presets across
> workspaces, including reviewing free-tier model offerings and activity
> metrics for LLM routing.
> **GitHub:** Inspected project repositories and commit history for
> LocalLLMAgent, ScribeJay, and NotebookSync, focusing on LocalLLMAgent
> documentation and recent code updates.
> **Google NotebookLM / Gemini:** Accessed specific notebook instances to
> manage structured reference material […]
> **Tailscale & Self-Hosted Infrastructure:** Interacted with local network
> services via Tailscale to access the Wren Dashboard […]
>
> *Product & Strategy* — **Meta AI Strategy & Organizational Restructuring:**
> Reviewed coverage on Meta's internal leadership messaging regarding workforce
> restructuring and resource allocation tied to artificial intelligence
> initiatives.

Gemma (arm B):

> **OpenRouter:** Accessed activity logs, workspace presets, and free model
> configurations to manage AI model interactions.
> **GitHub:** Reviewed the LocalLLMAgent repository, including documentation
> and recent commit history.
>
> *Product & Strategy* — **None:** [No qualifying items for this section]

Same day, same frozen history, same fetched pages. Gemma dropped Tailscale and
NotebookLM entirely, and wrote "None" over a Meta AI strategy story that was in
its input. That is a **miss**, not a shorter style. It is also why the
grounding score misled: two accurate bullets score well and the day is still
gone from the journal.

### YouTube, 15 August — the one day Gemma won

Gemma (arm B, chosen):

> **Stakeholder Management:** This cluster focuses on interpersonal strategies
> for navigating organizational dynamics. These lessons provide frameworks for
> exercising influence and maintaining leadership presence within product teams.
> **Product Leadership Trends:** These videos examine the high-level
> perspectives of experienced industry veterans. […]

Gemini (arm A):

> **Forward-Looking Product Strategy:** Explores emerging operational frameworks
> and mindsets from experienced executives, highlighting how anticipating
> technological shifts shapes long-term roadmap resilience.
> **Cross-Functional Influence & Alignment:** Focuses on actionable
> communication strategies to build stakeholder buy-in, manage organizational
> friction, and drive consensus across diverse teams.

Both are fine. The gap that opens on Chrome does not open here, because the
YouTube task hands the model a short list of titles and asks for two themes —
a job small enough that the small model does it.

## 8. Recommendation

**Keep Gemini on both tasks. Add an automatic fall back to Ollama when the
model call fails.**

The numbers that decide it:

- Blind read **11–1–2** to Gemini, and **7–0** on Chrome. Moving both tasks to
  Gemma buys a measurably worse Chrome page.
- Gemini costs **$0.09 a month** for both jobs. There is no saving to bank.
- Gemma never failed and never wrote an empty page in 14 drafts, and on YouTube
  it drew 4–1–2. A Gemma page is a real page — good enough for a bad morning,
  not good enough for every morning.

That combination is exactly the case for a fallback and against a move. This
bake-off needed no extra measurement to justify it: sections 6 and 7 already
show what the fallback output looks like, on both tasks, on fourteen real days.

**Not chosen, and why:**

- *Stay on Gemini and just keep the balance topped up.* Rejected: it is the
  status quo that lost four mornings, and it makes a written journal depend on
  someone noticing a balance.
- *Move both tasks to Gemma.* Rejected: it pays a 7–0 quality loss on Chrome to
  save $1.06 a year.

**What the fallback has to do**, when it is built:

- Live at the `core/model.py` choke point, so all eight tasks inherit it.
- Trigger on the API failing, not on a specific status code — the 429 was this
  outage; a 500 or a timeout costs the same morning.
- Log WARNING with the reason and the backend it switched to. A silent fallback
  is a task that quietly got worse, and AGENTS.md says degrading is only safe
  if it is logged.
- Record the fallback backend in `logs/usage.jsonl`, so the ledger keeps saying
  which model actually wrote the page.
- Never fall back for a task the user pointed at a cloud model *for privacy
  reasons in reverse* — falling back is always toward local, never outward, so
  this direction is safe by construction.

## 9. Still open

- **The YouTube prompt asks for 2–4 bullets regardless of how many videos were
  Liked.** On a one-video day both arms are being asked for the wrong thing.
  Gemma writes one bullet and gets flagged; Gemini writes two and pads. The
  prompt should scale its bullet count to the input.
- **`grounded_pct` should not be computed for YouTube.** It is structurally
  zero. Leaving it in a report invites someone to read a real number into it.
- **Grounding rewards restatement.** It ranked Gemma above Gemini on Chrome and
  the blind read went the other way 7–0. Any future bake-off should treat it as
  a floor check ("did the model invent things?") and never as a ranking.
- **Seven days is a small sample, and one reader.** The Chrome 7–0 is clear
  enough to act on. The YouTube 4–1–2 is not; it would flip on two different
  days.
- **Gemma was not tested at a larger size.** `gemma4:31b-mlx` was deliberately
  left out to keep the read at 14 pairs. If the fallback page ever needs to be
  better, that is the next thing to measure.

## The harness

`scribejay/model_bakeoff.py` and `tests/test_model_bakeoff.py` were written for
this run and deleted once this document existed, the same way
`scribejay/bakeoff.py` was deleted after `docs/web-fetch.md` (commit `a41805a`).
The numbers are the record. To recover it:

```bash
git log --diff-filter=D --oneline -- scribejay/model_bakeoff.py
```

The drafts themselves are still on disk at `~/.scribejay/model-bakeoff/`:
14 pairs, 14 key files, and `bakeoff.jsonl` with one row per day per task.

Full report, with all 14 pairs side by side:
<https://claude.ai/code/artifact/98484847-9b1e-4624-bc54-53eb31279691>

## Related

- [docs/llm-backend.md](llm-backend.md) — how a task selects its backend
- [docs/web-fetch.md](web-fetch.md) — the previous bake-off, and the blind-read
  method this one reused
- [docs/model-constraints.md](model-constraints.md) — designing around the small
  local model
- [docs/usage-ledger.md](usage-ledger.md) — where the cost and token numbers
  came from
