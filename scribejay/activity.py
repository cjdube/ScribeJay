"""Shared helpers for the daily activity reviews (daily_chrome_learnings,
daily_youtube_learnings, ai_chat_learnings).

They all follow the same shape — resolve the prior day (scribejay.core.dates),
compact the fetched data to bound the prompt for the model, draft with the
model, then persist (scribejay.sinks.vault). This module holds the compaction
half; persist_or_email lives in scribejay.sinks.vault since it's a sink
operation, not a compaction one.

Mirrors the compaction slice of LocalLLMAgent's agent/activity_log.py.
"""

import re
from urllib.parse import urlparse, urlunparse

from scribejay.core import config
from scribejay.core.urls import safe_url

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


# ---- picking pages worth fetching -------------------------------------------
#
# Deliberately deterministic Python, not a model call. The alternative — asking
# the small local model at 5:15 AM which pages look interesting — buys nothing
# a sort cannot do, and adds a call that can time out, return empty, or hand
# back a number that maps to no page.

# Words that make a path a session rather than a subject. Fetching one gets a
# login wall at best and a logged-out shell at worst, and neither says anything
# about the day.
#
# Matched against the *tokens* of each path segment ("/reset-password" ->
# {"reset", "password"}), not as raw substrings: a substring test misses
# "/reset-password" while "/descartes" would match "cart". The cost is that a
# genuine article at "/blog/password-managers" is skipped too — one page out of
# five, on a heuristic whose failure is a missing bullet, not a wrong one.
_AUTH_WORDS = frozenset({
    "login", "signin", "logout", "signout", "register", "signup",
    "account", "accounts", "settings", "preferences", "admin", "checkout",
    "cart", "billing", "subscribe", "password", "oauth", "auth",
})


def _is_session_path(path: str) -> bool:
    return any(token in _AUTH_WORDS
               for segment in path.lower().split("/")
               for token in re.split(r"[-_]", segment))

# Extensions whose bytes are not readable text. A PDF is the interesting one:
# it often IS the thing read, but neither the local extractor nor the summary
# prompt handles one, so it is skipped rather than fetched and thrown away.
_BINARY_SUFFIXES = (
    ".pdf", ".zip", ".dmg", ".pkg", ".tar", ".gz", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".mp4", ".mp3", ".mov", ".wav", ".csv", ".xlsx", ".doc", ".docx",
)

# Hosts that are this machine or its network. Nothing here is a page the user
# "read about"; several are dev servers whose content is the user's own work.
_PRIVATE_HOST_PREFIXES = ("127.", "10.", "192.168.", "169.254.", "0.")


def _is_private_host(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    if not host or host in ("localhost", "::1") or host.endswith(".local"):
        return True
    if host.startswith(_PRIVATE_HOST_PREFIXES):
        return True
    # 172.16.0.0/12 — the one private range a string prefix cannot express.
    parts = host.split(".")
    if len(parts) == 4 and parts[0] == "172":
        try:
            return 16 <= int(parts[1]) <= 31
        except ValueError:
            return False
    return False


# Path shapes that mean "somebody published this", used as an allow list.
#
# The first version of this module rejected what looked private and fetched
# everything else. Measured over five real days it spent 10 of 25 picks on
# sign-in walls — Yahoo Mail, Airbnb, TripIt, Cloudflare, a hospital patient
# portal — and most of the rest on receipts and search forms. A reject list
# fails open: the next portal the user signs in to is not on it, and its url
# goes out to a fetcher anyway.
#
# So the test is inverted. A page is fetched only if its path looks like
# published writing. Anything unclassifiable is skipped, which costs a bullet
# and never costs a private url.

# Sections that publish. A page under one of these is allowed a one-word final
# segment, because "/gemini-api/docs/models" is exactly the page worth reading
# and its slug says nothing on its own.
_CONTENT_MARKERS = (
    "/blog/", "/blogs/", "/docs/", "/doc/", "/documentation/", "/article",
    "/news/", "/guide", "/tutorial", "/reference/", "/wiki/", "/help/",
    "/learn/", "/post", "/story/", "/faq/", "/manual/", "/handbook/",
)

# A long digit or hex run is an identifier, never a title: an order id, a
# reservation code, a build uuid.
_ID_RUN = re.compile(r"[0-9a-f]{8,}|\d{5,}")

_PAGE_SUFFIX = re.compile(r"\.(html?|php|aspx?|jsp)$")


def _looks_published(path: str) -> bool:
    """True if `path` has the shape of something written for readers."""
    segments = [s for s in path.lower().split("/") if s]
    if not segments:
        return False
    last = _PAGE_SUFFIX.sub("", segments[-1])
    words = [w for w in re.split(r"[-_.]", last) if w.isalpha() and len(w) >= 2]

    # A headline turned into a path — "googles-gemini-has-a-branding-problem".
    # Three real words is the line: "rental-search" and "test-results" fall
    # under it, and so does every opaque id.
    if len(words) >= 3:
        return True

    # Otherwise it has to sit in a section that publishes, and still not be an
    # identifier.
    joined = "/" + "/".join(segments) + "/"
    if any(marker in joined for marker in _CONTENT_MARKERS):
        return bool(words) and not _ID_RUN.search(last)
    return False


# Sections that publish, but publish nothing this journal is for.
#
# This one IS a reject list, and deliberately so. The allow list above answers a
# privacy question — never send a url that might be private — where failing open
# is unacceptable. This answers a relevance question, where failing open costs a
# wasted fetch and a wasted model call, nothing more.
#
# It exists because the two are not the same test. "thinking-of-ending-things"
# and "rigatoni-with-marinated-tomatoes-and-burrata" are published writing by
# every measure `_looks_published` applies. Over the same five days they and
# three like them took 5 of 16 page notes. DRAFT_SYSTEM_PROMPT already tells the
# model to drop fitness, social, travel and household items; this drops them
# before they cost anything, and before `journal.py:pages_read_section` prints
# them into the vault, which — unlike the draft prompt — has no judgment.
#
# Matched as whole path segments, so "ent" needs to be a section rather than the
# start of "enterprise".
_OFF_TOPIC_SECTIONS = frozenset({
    "sports", "sport", "mlb", "nfl", "nba", "nhl", "wnba", "ncaa", "mls",
    "soccer", "golf", "tennis", "olympics", "fitness",
    "entertainment", "ent", "celebrity", "celebrities", "movies", "tv", "music",
    "horoscope", "recipes", "recipe", "food", "cooking", "lifestyle",
    "travel", "fashion", "weather",
})


def _is_off_topic(path: str) -> bool:
    return any(segment in _OFF_TOPIC_SECTIONS for segment in path.lower().split("/"))


def _fetch_url(url: str) -> str:
    """The same page with its query string and fragment removed.

    An article renders the same without `?utm_source=`, and the query string is
    where the identifiers live — an order id, a reservation code, an SSO
    `state` token. Dropping it is the difference between sending a page address
    to a fetcher and sending a piece of the user's account.
    """
    parts = urlparse(url)
    return urlunparse((parts.scheme, parts.netloc, parts.path, "", "", ""))


def _page_score(path: str, visits: int) -> int:
    """Higher is more worth fetching.

    Two signals, both already stated in words in the draft prompt:

    - **Depth.** "/gemini-api/docs/models" is an article; "/blog" is an index.
      Capped at four segments, past which more depth says nothing more.
    - **Few visits.** A page opened once was read on purpose. A page opened
      nine times is a dashboard someone keeps landing on. This inverts the
      order fetch_chrome_history returns, which is exactly the point.
    """
    depth = min(len([p for p in path.split("/") if p]), 4)
    return depth * 10 - min(max(visits, 0), 10)


def candidate_urls(sites: list, limit: int) -> list[dict]:
    """The `limit` pages most worth fetching, as {domain, title, path, url}.

    Applies the same exclusion rules `compact_sites` does — a subject the user
    told ScribeJay to leave out must not be sent to a fetcher, least of all a
    third-party one — plus the scheme, host and file-type guards a network call
    needs and a prompt does not, `_looks_published`, which is the one that keeps
    a signed-in portal out, and `_is_off_topic`, which keeps the sports desk out.

    The returned url has no query string: see `_fetch_url`.

    Spreads across domains before it goes deep into one: five pages should be
    five things looked into, not five tabs of the same doc site.
    """
    by_domain: dict[str, list[dict]] = {}
    for site in sites:
        domain = site.get("domain") or ""
        if _is_excluded(domain) or is_excluded_text(site.get("title", "")):
            continue
        for page in site.get("pages") or []:
            path, url = page.get("path") or "", safe_url(page.get("url") or "")
            if not url or not path or is_excluded_text(path):
                continue
            if _is_private_host(urlparse(url).hostname or ""):
                continue
            lowered = path.lower()
            if _is_session_path(lowered):
                continue
            if lowered.endswith(_BINARY_SUFFIXES):
                continue
            if not _looks_published(lowered):
                continue
            if _is_off_topic(lowered):
                continue
            by_domain.setdefault(domain, []).append({
                "domain": domain,
                "title": site.get("title") or "",
                "path": path,
                "url": _fetch_url(url),
                "_score": _page_score(path, page.get("visits", 0)),
            })

    for pages in by_domain.values():
        pages.sort(key=lambda p: p["_score"], reverse=True)

    # Round-robin: every domain's best page before any domain's second.
    order = sorted(by_domain.values(), key=lambda ps: ps[0]["_score"], reverse=True)
    out = []
    for rank in range(max((len(ps) for ps in order), default=0)):
        for pages in order:
            if rank < len(pages):
                out.append({k: v for k, v in pages[rank].items() if k != "_score"})
                if len(out) == limit:
                    return out
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
