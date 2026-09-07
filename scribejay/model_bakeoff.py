"""Two backends drafting the same days, scored side by side.

**Temporary.** This module exists to answer one question — how much page
quality is lost by moving `daily_chrome_learnings` and `daily_youtube_learnings`
off Gemini and onto the local Gemma model — and is deleted once the answer is
written into `docs/model-bakeoff.md`. Nothing in the scheduled path imports it.
It is the same shape as the fetcher bake-off that settled `docs/web-fetch.md`
(recoverable as `git show a41805a^:scribejay/bakeoff.py`).

    Arm       Backend   Model
    gemini    gemini    whatever GEMINI_MODEL resolves to
    ollama    ollama    whatever OLLAMA_MODEL resolves to

Why the question is worth measuring rather than arguing: on 3-6 September 2026
both tasks failed with `429 RESOURCE_EXHAUSTED` — depleted prepayment credits —
and wrote no page for four days. Total Gemini spend in `logs/usage.jsonl` at
that point was $0.12, so this is a reliability decision, not a cost one.

**One gather, one fetch.** Every arm for a given day drafts from the same frozen
Chrome history and the same fetched pages, so the comparison measures the
models rather than the day's browsing or the fetcher's luck.

**A whole-pipeline arm.** For the chrome task the arm's own backend writes the
per-page summaries AND the final draft, because that is what "switch to Gemma"
actually means. `notes_chars` is recorded per arm so the report can still say
whether a difference came from the notes or from the draft.

**Blind by construction.** The drafts are written as V/Z in a per-day shuffled
order, with the mapping in a key file. Read them, rank them, then open the key.
The fetcher bake-off's lesson, written into docs/web-fetch.md, was that the
automated score could not separate the arms and the blind read decided. Expect
the same here. The numbers below are guard rails, not a verdict.

**Writes nothing a real run writes.** No vault page, no email, no push, and its
own logger name — `cli/doctor.py:last_run` and a sibling repo both read
`logs/<task>.log` for the run-boundary lines, and a bake-off must never appear
there as a scheduled run that happened.

Usage:
    python -m scribejay.model_bakeoff --task both --days 7
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scribejay import daily_chrome_learnings as chrome
from scribejay import daily_youtube_learnings as youtube
from scribejay.activity import candidate_urls, compact_sites
from scribejay.core import config, usage_ledger
from scribejay.core.dates import prior_day
from scribejay.core.logs import setup_logger
from scribejay.core.model import complete_text, warm_model
from scribejay.journal import has_substantive_content, pages_read_section, videos_section

ARMS = ("gemini", "ollama")
BLIND_LETTERS = ("V", "Z")
TASKS = ("daily_chrome_learnings", "daily_youtube_learnings")

DEFAULT_DAYS = 7
# A day with two visits produces one bullet from any model. Comparing two
# drafts of nothing measures nothing, and four such days in a seven-day sample
# is how a bake-off reports a tie it never actually tested. Chrome only —
# YouTube's own filter is "was anything Liked", which is already this test.
MIN_SITES = 5
# How far back to walk looking for days that have data. YouTube Likes land on
# roughly half of all days, so a 7-day sample needs more than 7 days of runway.
MAX_LOOKBACK = 45

_BULLET = re.compile(r"^\s*-\s+")
_BOLD_TOPIC = re.compile(r"\*\*(.+?)\*\*")
# A version, a count, a date, a price — the tokens a vague bullet never has.
_CONCRETE = re.compile(r"\d")
_HEADING = re.compile(r"^#{2,3}\s+(.*)$", re.M)
# A bracket the model was told to fill in and left behind. The prompts say
# "do not include the literal brackets", so one that survives is a failure to
# follow the template, not a stylistic choice.
_UNFILLED = re.compile(r"\[[A-Z][^\]]*\]")

# The one bracketed string the chrome prompt asks for verbatim, brackets and
# all, when a section has nothing in it. Flagging it as an unfilled placeholder
# would mark every quiet day as a template failure — and a quiet day is the one
# case where both models are supposed to produce the identical line.
_SANCTIONED = "[No qualifying items for this section]"
_NONE_BULLET = "**None:**"

# The headings each task's prompt demands, and the bullet count it asks for.
TEMPLATES = {
    "daily_chrome_learnings": {
        "headings": ("Tools & Tech Encountered", "Product & Strategy"),
        "bullets": (2, 4),
    },
    "daily_youtube_learnings": {
        "headings": ("Themes Explored",),
        "bullets": (2, 4),
    },
}

# Long enough that a normal draft never repeats one by accident, short enough
# to catch the degenerate loop a small model falls into.
_NGRAM = 6


def out_dir():
    """Under ~/.scribejay, not the source tree — see AGENTS.md on stores."""
    return config.resolve_path("model-bakeoff")


# ---- scoring ----------------------------------------------------------------

def _score(text: str, source_blob: str) -> dict:
    """What can be counted without a human reading anything.

    Carried over unchanged from the fetcher bake-off, including its known
    weakness: `grounded` counts how often a bolded bullet topic appears
    word-for-word in that arm's own source data, which punishes accurate
    synthesis. A bullet titled "Agentic Retrieval" scores zero even when it is
    the best line on the page. Read it as a hallucination smoke alarm, never as
    a quality ranking.
    """
    bullets = [ln.strip() for ln in text.splitlines()
               if _BULLET.match(ln) and "**None:**" not in ln]
    topics = [m.group(1).rstrip(":").strip() for ln in bullets
              for m in [_BOLD_TOPIC.search(ln)] if m]
    blob = source_blob.lower()
    grounded = [t for t in topics if t.lower() in blob]
    concrete = [b for b in bullets if _CONCRETE.search(b)]
    return {
        "bullets": len(bullets),
        "mean_bullet_chars": round(sum(len(b) for b in bullets) / len(bullets), 1)
        if bullets else 0.0,
        "grounded_pct": round(100 * len(grounded) / len(topics), 1) if topics else 0.0,
        "specific_pct": round(100 * len(concrete) / len(bullets), 1) if bullets else 0.0,
        "ungrounded_topics": [t for t in topics if t.lower() not in blob],
    }


def _repeat_ratio(text: str) -> float:
    """Share of 6-word windows this draft has already used, as a percentage.

    The local model's characteristic failure is not a wrong answer, it is a
    loop — the YouTube prompt already contains "Never repeat a word or phrase"
    because of it. A clean draft scores near 0; a looping one climbs fast.
    """
    words = re.findall(r"\w+", text.lower())
    if len(words) < _NGRAM * 2:
        return 0.0
    grams = [tuple(words[i:i + _NGRAM]) for i in range(len(words) - _NGRAM + 1)]
    return round(100 * (1 - len(set(grams)) / len(grams)), 1)


def _sections(text: str) -> dict:
    """{heading: [bullet, ...]} for the draft's own sections.

    Per section, not one flat count: "four bullets" is a pass when they are
    two and two and a fail when they are four and none, and a total cannot
    tell those apart.
    """
    out, current = {}, None
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            current = heading.group(1).strip()
            out.setdefault(current, [])
        elif current is not None and _BULLET.match(line):
            out[current].append(line.strip())
    return out


def _template_ok(text: str, task: str) -> dict:
    """Did the draft obey the template it was handed?

    Reported per rule rather than as one boolean, because "wrote five bullets"
    and "left a [Placeholder] in" are different problems with different fixes,
    and a single pass/fail cannot tell an operator which happened.
    """
    spec = TEMPLATES[task]
    found = _sections(text)
    extra = [h for h in found if h not in spec["headings"]
             and not h.startswith("Daily Log")
             and not h.startswith("YouTube Learnings")
             and h not in ("Videos Liked", "Pages Read")]

    low, high = spec["bullets"]
    in_range = True
    for heading in spec["headings"]:
        bullets = found.get(heading, [])
        # One "**None:**" bullet is what the prompt asks for when a section has
        # nothing in it. That is the template being followed, not broken.
        if len(bullets) == 1 and _NONE_BULLET in bullets[0]:
            continue
        if not low <= len(bullets) <= high:
            in_range = False

    return {
        "headings_present": all(h in found for h in spec["headings"]),
        "extra_sections": extra,
        "unfilled_placeholders": _UNFILLED.findall(text.replace(_SANCTIONED, "")),
        "bullets_in_range": in_range,
    }


def _template_clean(checks: dict) -> bool:
    return (checks["headings_present"] and not checks["extra_sections"]
            and not checks["unfilled_placeholders"] and checks["bullets_in_range"])


# ---- the usage ledger -------------------------------------------------------

def _ledger_offset() -> int:
    """Where the ledger ends right now, so an arm's own rows can be read back."""
    path = usage_ledger.LEDGER_PATH
    return path.stat().st_size if path.exists() else 0


def _ledger_since(offset: int, logger) -> list:
    """The rows appended since `offset`.

    The ledger rewrites itself when it passes SCRIBEJAY_USAGE_MAX_BYTES, which
    invalidates a byte offset. That shows up as a file shorter than the offset;
    say so and return nothing rather than parsing a row from the middle of a
    line and reporting a token count that is silently wrong.
    """
    path = usage_ledger.LEDGER_PATH
    if not path.exists():
        return []
    if path.stat().st_size < offset:
        logger.warning("usage.jsonl was pruned mid-run — token and cost figures "
                       "for this arm are missing, not zero")
        return []
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read().decode("utf-8", "replace")
    rows = []
    for line in raw.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _ledger_totals(rows: list) -> dict:
    """Tokens, wall clock and money for one arm's calls.

    `cost_usd` is None for a row the price table does not cover, and summing
    those as zero would report a paid arm as free. A None anywhere makes the
    total None, which reads as "unknown" instead of "nothing".
    """
    costs = [r.get("cost_usd") for r in rows if r.get("ok")]
    total = None if any(c is None for c in costs) else round(sum(costs), 6)
    return {
        "calls": len(rows),
        "failed_calls": sum(1 for r in rows if not r.get("ok")),
        "errors": [r.get("error") for r in rows if not r.get("ok")],
        "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in rows),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in rows),
        "model_ms": sum(r.get("duration_ms") or 0 for r in rows),
        "cost_usd": total,
        "models": sorted({r.get("model") for r in rows if r.get("model")}),
    }


# ---- one day's shared input -------------------------------------------------

def gather_chrome(day, logger, min_sites: int = MIN_SITES) -> dict | None:
    """One Chrome gather and one web fetch, to be shared by every arm.

    None when the day has too little browsing to tell two models apart. The
    fetch happens here, outside any arm, because the fetcher is not the
    variable under test — the fetcher bake-off already settled it.
    """
    sites = chrome.gather(day, logger)
    chrome_sites = compact_sites(sites)
    if len(chrome_sites) < min_sites:
        logger.info(f"{day}: {len(chrome_sites)} sites, under the {min_sites} "
                    "needed to tell two drafts apart — skipping")
        return None

    pages = []
    if chrome.web_fetch_enabled():
        from scribejay.sources import web_fetch

        candidates = candidate_urls(sites, chrome.max_pages(logger))
        if candidates:
            pages, stats = web_fetch.fetch_pages(candidates, logger_=logger)
            logger.info(f"{day}: fetched {len(pages)} of {len(candidates)} pages "
                        f"({stats['cached']} from cache) — shared by every arm")
    else:
        logger.info(f"{day}: web fetch is off; every arm drafts from paths alone")

    return {"chrome_sites": chrome_sites, "pages": pages}


def gather_youtube(day, logger) -> dict | None:
    videos = youtube.gather(day, logger)
    if not videos:
        logger.info(f"{day}: no Likes — nothing to compare, skipping")
        return None
    return {"videos": videos}


GATHERERS = {"daily_chrome_learnings": gather_chrome,
             "daily_youtube_learnings": gather_youtube}


# ---- one arm ----------------------------------------------------------------

def draft_chrome(day, shared: dict, arm: str, logger) -> tuple[str, str, str, dict]:
    """Returns (page, model text, source blob, meta) for one arm.

    Two texts, deliberately. The page is what the vault would actually have
    received — the draft plus the deterministic Pages Read section — and is
    what the blind reader judges. The model text is the draft alone, and is
    what gets scored: `pages_read_section` and `videos_section` are written by
    Python from the source data, so scoring the assembled page would count
    Python's bullets as the model's and score its own inputs as "grounded".
    The blob is what the grounding check reads against.
    """
    warm_model(logger=logger, backend=arm)
    summaries = chrome.summarize_pages(shared["pages"], logger, arm) if shared["pages"] else []
    notes_block = chrome.page_notes_block(summaries)

    started = time.monotonic()
    text = complete_text(
        system_prompt=chrome.DRAFT_SYSTEM_PROMPT,
        user_prompt=chrome.build_prompt(day, shared["chrome_sites"], notes_block),
        logger=logger, backend=arm, think=False,
    )
    draft_seconds = round(time.monotonic() - started, 1)

    page = text
    if summaries and has_substantive_content(text):
        page = text.rstrip() + "\n\n" + pages_read_section(summaries)

    meta = {"draft_seconds": draft_seconds,
            "notes_chars": len(notes_block),
            "pages_summarized": len(summaries),
            "pages_offered": len(shared["pages"]),
            "wrote_a_page": has_substantive_content(text)}
    return page, text, str(shared["chrome_sites"]) + notes_block, meta


def draft_youtube(day, shared: dict, arm: str, logger) -> tuple[str, str, str, dict]:
    warm_model(logger=logger, backend=arm)
    videos = shared["videos"]
    prompt = youtube.build_prompt(day, videos)

    started = time.monotonic()
    text = complete_text(
        system_prompt=youtube.DRAFT_SYSTEM_PROMPT, user_prompt=prompt,
        logger=logger, backend=arm, think=False,
    )
    draft_seconds = round(time.monotonic() - started, 1)

    usable = youtube._looks_usable(text)
    if usable:
        page = f"{text}\n\n{videos_section(videos)}\n"
    else:
        page = f"## YouTube Learnings: {day:%B %-d, %Y}\n\n{videos_section(videos)}\n"

    meta = {"draft_seconds": draft_seconds, "videos": len(videos),
            "wrote_a_page": usable}
    return page, text, prompt, meta


DRAFTERS = {"daily_chrome_learnings": draft_chrome,
            "daily_youtube_learnings": draft_youtube}


# ---- a day, every arm -------------------------------------------------------

def run_day(task: str, day, logger) -> dict:
    """Draft `day` once per arm, write the blinded files, return the row."""
    directory = out_dir()
    directory.mkdir(parents=True, exist_ok=True)

    shared = GATHERERS[task](day, logger)
    if shared is None:
        return {}

    row = {"task": task, "day": str(day),
           "run_at": datetime.now(timezone.utc).isoformat(), "arms": {}}
    pages = {}
    for arm in ARMS:
        logger.info(f"{task} {day}: arm {arm}")
        offset = _ledger_offset()
        started = time.monotonic()
        try:
            page, text, blob, meta = DRAFTERS[task](day, shared, arm, logger)
        except Exception as e:
            # One arm's backend failing on one day is a result, not a crash.
            # It is in fact the exact result that started this bake-off.
            logger.warning(f"{task} {day}: arm {arm} failed "
                           f"({type(e).__name__}: {e})")
            row["arms"][arm] = {"failed": f"{type(e).__name__}: {e}",
                                "usage": _ledger_totals(_ledger_since(offset, logger))}
            pages[arm] = f"[arm {arm} failed: {type(e).__name__}: {e}]\n"
            continue
        pages[arm] = page
        checks = _template_ok(text, task)
        row["arms"][arm] = {
            **meta,
            **_score(text, blob),
            "repeat_pct": _repeat_ratio(text),
            "template": checks,
            "template_clean": _template_clean(checks),
            "total_seconds": round(time.monotonic() - started, 1),
            "usage": _ledger_totals(_ledger_since(offset, logger)),
        }

    # Shuffled per day, so a reader who works out one day's order learns
    # nothing about the next.
    shuffled = list(ARMS)
    random.shuffle(shuffled)
    key = dict(zip(BLIND_LETTERS, shuffled))
    for letter, arm in key.items():
        (directory / f"{task}-{day}-{letter}.md").write_text(pages[arm])
    (directory / f"{task}-{day}-key.json").write_text(json.dumps(key, indent=2))
    row["key"] = key

    with open(directory / "bakeoff.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")

    logger.info(f"{task} {day}: wrote {len(ARMS)} blinded drafts to {directory}")
    for arm in ARMS:
        a = row["arms"][arm]
        if "failed" in a:
            logger.info(f"  arm {arm}: FAILED — {a['failed']}")
            continue
        logger.info(
            f"  arm {arm}: {a['bullets']} bullets, {a['grounded_pct']}% grounded, "
            f"{a['specific_pct']}% specific, {a['repeat_pct']}% repeated, "
            f"{a['draft_seconds']}s, {a['usage']['prompt_tokens']} prompt tokens, "
            f"cost {a['usage']['cost_usd']}"
            + ("" if a["wrote_a_page"] else " [NO PAGE]")
            + ("" if a["template_clean"] else " [OFF TEMPLATE]"))
    return row


def run(task: str, wanted: int, logger) -> list:
    """Walk backwards from yesterday until `wanted` days have produced a row."""
    _, _, yesterday = prior_day()
    rows = []
    for n in range(MAX_LOOKBACK):
        if len(rows) >= wanted:
            break
        day = yesterday - timedelta(days=n)
        row = run_day(task, day, logger)
        if row:
            rows.append(row)
    if len(rows) < wanted:
        logger.warning(f"{task}: only {len(rows)} of {wanted} days had data "
                       f"within {MAX_LOOKBACK} days")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default="both", choices=(*TASKS, "both"))
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"days with data to compare per task; default {DEFAULT_DAYS}")
    args = parser.parse_args()

    # Its own logger, never a task's: cli/doctor.py:last_run and a sibling repo
    # both read logs/<task>.log for the run-boundary lines.
    logger = setup_logger("model_bakeoff")
    logger.info(f"Starting model bake-off: arms {ARMS}, {args.days} days per task")

    tasks = TASKS if args.task == "both" else (args.task,)
    for task in tasks:
        run(task, args.days, logger)

    logger.info(f"Model bake-off complete — read the drafts in {out_dir()} "
                "BEFORE opening any key file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
