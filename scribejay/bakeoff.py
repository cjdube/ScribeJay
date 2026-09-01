"""Four ways to draft the same day, scored side by side.

**Temporary.** This module exists to answer one question — is fetching pages
worth it, and if so, whose fetcher and in what shape — and is deleted once the
answer is written into `docs/web-fetch.md`. Nothing in the scheduled path
imports it; `daily_chrome_learnings.py` pulls it in only for `--bakeoff`.

    Arm  Fetcher     What reaches the draft prompt
    A    none        paths and titles only — today's behaviour
    B    local       local model summaries — the shipped design
    C    firecrawl   local model summaries

Two comparisons, each isolating one variable:

- **A vs B** — does fetching help at all?
- **B vs C** — is Firecrawl's extraction better than this machine's? Same
  pipeline on both sides, so the only difference is the text.

There was a fourth arm: Firecrawl text as raw excerpts rather than summaries,
which is the shape AGENTS.md's untrusted-content rule forbids. It ran once, on
five days, to measure what that rule costs. It cost nothing — it produced the
least grounded bullets of any arm on every single day — so the code that made
it possible is gone. `docs/web-fetch.md` keeps the numbers.

**One gather, one fetch.** All three arms draft from the same frozen Chrome
data, so the comparison measures the arms rather than the model's run-to-run
variance.

**Blind by construction.** The three drafts are written as W/X/Y in a shuffled
order with the mapping in a key file. Read them, rank them, then open the key.
"""

import json
import random
import re
import time
from datetime import datetime, timezone

from scribejay.activity import compact_sites
from scribejay.core import config
from scribejay.core.model import backend as scribejay_backend, complete_text, warm_model
from scribejay.daily_chrome_learnings import (DRAFT_SYSTEM_PROMPT, build_prompt,
                                              enrich, gather, max_pages)

ARMS = ("A", "B", "C")
BLIND_LETTERS = ("W", "X", "Y")

_BULLET = re.compile(r"^\s*-\s+")
_BOLD_TOPIC = re.compile(r"\*\*(.+?)\*\*")
# A version, a count, a date, a price — the tokens a vague bullet never has.
_CONCRETE = re.compile(r"\d")


def out_dir():
    """Under ~/.scribejay, not the source tree — see AGENTS.md on stores."""
    return config.resolve_path("bakeoff")


def _score(text: str, source_blob: str) -> dict:
    """What can be counted without a human reading anything.

    `grounded` is the one that matters: the share of bolded bullet topics that
    actually appear somewhere in that arm's own source data. A bullet about a
    tool no page mentioned is invention, and invention is the cost side of
    every enrichment arm.
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


def _draft(day, chrome_sites, notes_block, logger, backend) -> tuple[str, dict]:
    prompt = build_prompt(day, chrome_sites, notes_block)
    num_ctx = int(config.getenv("OLLAMA_NUM_CTX"))
    num_predict = int(config.getenv("OLLAMA_NUM_PREDICT"))
    # Rough, and rough is enough: ~4 chars per token is the standard estimate,
    # and what is being watched for is an order-of-magnitude problem.
    est_tokens = (len(DRAFT_SYSTEM_PROMPT) + len(prompt)) // 4
    room = num_ctx - num_predict

    started = time.monotonic()
    text = complete_text(system_prompt=DRAFT_SYSTEM_PROMPT, user_prompt=prompt,
                         logger=logger, backend=backend, think=False)
    meta = {
        "prompt_chars": len(prompt),
        "est_prompt_tokens": est_tokens,
        "input_room_tokens": room,
        # Ollama trims an over-long prompt from the front, taking the system
        # prompt with it — the draft comes back missing a whole section and
        # nothing anywhere says why. If an arm trips this, that IS its result.
        "near_context_limit": est_tokens > room * 0.9,
        "draft_seconds": round(time.monotonic() - started, 1),
    }
    return text, meta


def run(day, logger) -> dict:
    """Draft `day` three ways, write the blinded files, return the row."""
    directory = out_dir()
    directory.mkdir(parents=True, exist_ok=True)
    backend = scribejay_backend("daily_chrome_learnings")
    limit = max_pages(logger)

    sites = gather(day, logger)
    chrome_sites = compact_sites(sites)
    if not chrome_sites:
        logger.warning(f"{day}: no browsing — nothing to compare, skipping")
        return {}

    warm_model(logger=logger, backend=backend)
    base_blob = str(chrome_sites)

    logger.info(f"{day}: arm B — local fetch")
    notes_local, stats_local = enrich(sites, logger, backend, limit, fetch_backend="local")

    logger.info(f"{day}: arm C — firecrawl fetch")
    notes_fire, stats_fire = enrich(sites, logger, backend, limit, fetch_backend="firecrawl")

    arms = {
        "A": ("", base_blob),
        "B": (notes_local, base_blob + notes_local),
        "C": (notes_fire, base_blob + notes_fire),
    }

    row = {"day": str(day), "run_at": datetime.now(timezone.utc).isoformat(),
           "sites": len(chrome_sites), "limit": limit,
           "fetch": {"local": stats_local, "firecrawl": stats_fire},
           "arms": {}}
    drafts = {}
    for arm in ARMS:
        notes_block, blob = arms[arm]
        logger.info(f"{day}: drafting arm {arm} ({len(notes_block)} chars of notes)")
        text, meta = _draft(day, chrome_sites, notes_block, logger, backend)
        drafts[arm] = text
        row["arms"][arm] = {**meta, **_score(text, blob),
                            "notes_chars": len(notes_block)}

    # Shuffle per day, so a reader who works out one day's order learns nothing
    # about the next.
    shuffled = list(ARMS)
    random.shuffle(shuffled)
    key = dict(zip(BLIND_LETTERS, shuffled))
    for letter, arm in key.items():
        (directory / f"{day}-{letter}.md").write_text(drafts[arm])
    (directory / f"{day}-key.json").write_text(json.dumps(key, indent=2))
    row["key"] = key

    with open(directory / "bakeoff.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")

    logger.info(f"{day}: wrote {len(ARMS)} blinded drafts to {directory}")
    for arm in ARMS:
        a = row["arms"][arm]
        logger.info(f"  arm {arm}: {a['bullets']} bullets, "
                    f"{a['grounded_pct']}% grounded, {a['specific_pct']}% specific, "
                    f"{a['est_prompt_tokens']} est tokens"
                    + (" [NEAR CONTEXT LIMIT]" if a["near_context_limit"] else ""))
    return row
