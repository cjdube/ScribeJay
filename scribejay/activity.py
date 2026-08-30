"""Shared helpers for the daily activity reviews (daily_chrome_learnings,
daily_youtube_learnings, ai_chat_learnings).

They all follow the same shape — resolve the prior day (scribejay.core.dates),
compact the fetched data to bound the prompt for the model, draft with the
model, then persist (scribejay.sinks.vault). This module holds the compaction
half; persist_or_email lives in scribejay.sinks.vault since it's a sink
operation, not a compaction one.

Mirrors the compaction slice of LocalLLMAgent's agent/activity_log.py.
"""

from scribejay.core import config

# Prompt-bounding caps. Daily volume is much smaller than a weekly run, so
# these rarely bind — but they keep a heavy browsing day (or a link-dump video
# description) from blowing past the model's context window. If you route a
# task back to a small local model, check the output: the failure mode is
# silent — a template section just vanishes from the draft.
MAX_CHROME_SITES = 40
MAX_YOUTUBE_VIDEOS = 25
MAX_YOUTUBE_DESC_CHARS = 500

# Page paths kept per site. The paths are what let the review say more than the
# tab title did — "/gemini-api/docs/models" plus "/gemini-api/docs/pricing" is a
# comparison, where the title alone is just "Gemini API".
MAX_PAGES_PER_SITE = 6

# Domains the user doesn't want reviewed (volunteer-admin portals, M365).
_EXCLUDED_DOMAINS = [
    d.lower() for d in config.section("learnings").get("excluded_domains", [])
    if isinstance(d, str) and d
]

# Subject matter the user doesn't want reviewed at all, whatever it's hosted on.
_EXCLUDED_KEYWORDS = [
    k.lower() for k in config.section("learnings").get("excluded_keywords", [])
    if isinstance(k, str) and k
]


def _is_excluded(domain: str) -> bool:
    """True if `domain` is an excluded domain or a subdomain of one, so a single
    "sharepoint.com" entry covers every tenant. The port is stripped first:
    Chrome's netloc carries one for local servers ("127.0.0.1:8420")."""
    host = (domain or "").lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in _EXCLUDED_DOMAINS)


def is_excluded_text(text: str) -> bool:
    """True if `text` contains an excluded keyword. A plain case-insensitive
    substring test — deliberately blunt, because the point is to keep a subject
    out of the vault entirely rather than to classify it precisely."""
    blob = (text or "").lower()
    return any(k in blob for k in _EXCLUDED_KEYWORDS)


def compact_sites(sites: list) -> list:
    """Drop excluded domains, trim to the top visited sites, and replace the full
    `url` (long, and mostly tracking query strings) with the site's top page
    paths. Excluding before the cap means MAX_CHROME_SITES budgets reviewable
    sites rather than being spent on filtered ones.

    `pages` is present only when the caller asked fetch_chrome_history for
    pages_per_domain > 1, so a site without it degrades to domain+title."""
    kept = [s for s in sites
            if not _is_excluded(s.get("domain", ""))
            and not is_excluded_text(s.get("title", ""))]
    top = sorted(kept, key=lambda s: s.get("visits", 0), reverse=True)[:MAX_CHROME_SITES]
    out = []
    for s in top:
        entry = {"domain": s.get("domain"), "title": s.get("title"), "visits": s.get("visits")}
        # A single excluded page on an otherwise reviewable site drops just that
        # path, not the whole site.
        paths = [p.get("path") for p in (s.get("pages") or [])
                 if p.get("path") and not is_excluded_text(p["path"])][:MAX_PAGES_PER_SITE]
        if paths:
            entry["pages"] = paths
        out.append(entry)
    return out


def compact_videos(videos: list) -> list:
    """Keep only the fields the model needs from each liked video and truncate
    the description, bounding the prompt the same way compact_sites does."""
    return [
        {
            "title": v.get("title"),
            "channel": v.get("channel"),
            "description": (v.get("description") or "")[:MAX_YOUTUBE_DESC_CHARS],
            "url": v.get("url"),
        }
        for v in videos[:MAX_YOUTUBE_VIDEOS]
    ]
