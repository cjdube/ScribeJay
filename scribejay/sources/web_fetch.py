"""Fetch the readable text of one web page.

The daily Chrome review knows which pages were read but not what they said —
`sources/chrome.py` hands on a domain, a title and a path, and the model has to
guess the rest. This module closes that gap for a handful of pages a day.

**Local, and only local.** AGENTS.md's data-sourcing policy prefers free and
official sources and rules out paid SaaS for data, and ScribeJay's whole
premise is that nothing leaves the Mac unless the user opts in. So the fetch is
an ordinary `requests.get` from this machine — the same request the browser
already made — with readable text pulled out by `trafilatura`. A hosted scraper
was measured against it and lost; `docs/web-fetch.md` records the numbers.

**A block is a no, not a puzzle.** A 403, a bot wall, or a robots-style refusal
means this page is skipped. ScribeJay does not retry a refusal by another route.

**A shell is not a page.** A "successful" fetch under MIN_USEFUL_CHARS is a
cookie banner or a JavaScript app that never rendered. It reads as an error
here, so no model call is spent summarising page furniture.

**Results are cached on disk** under `~/.scribejay`, keyed by url and pruned to
14 days. Re-running a day — a backfill, or the same day twice while comparing
output — then costs no second request.

Everything degrades to `{"error": ...}` rather than raising, like every other
source module.

Usage:
    python -m scribejay.sources.web_fetch https://example.com/some/page
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

from scribejay.core import config
from scribejay.core.http import http_error, print_result
from scribejay.core.store import atomic_write_json, load_json, locked
from scribejay.core.urls import safe_url

logger = logging.getLogger(__name__)

# What one page may contribute. Bounds the summarize call's prompt, and bounds
# how much untrusted text is ever held at once. Deliberately smaller than the
# model's context: this is raw material for a three-line summary, not a
# document to be reproduced.
MAX_TEXT_CHARS = 4000

# Below this, a "successful" fetch is a shell — a cookie banner, a nav bar, a
# JavaScript app that never rendered. Treated as a failure so the page is
# dropped rather than costing a model call to summarise nothing.
MIN_USEFUL_CHARS = 400

# A page bigger than this is a download, a dump or a trap. Read in chunks and
# stop, rather than pulling it all into memory to then truncate.
MAX_RESPONSE_BYTES = 3_000_000

# Plain and honest. Not a browser string chosen to look like something else —
# see the module docstring on blocks.
USER_AGENT = "ScribeJay/1.0 (personal journaling agent; +https://github.com/cjdube/ScribeJay)"

CACHE_NAME = "web_fetch_cache.json"
CACHE_TTL_DAYS = 14


# ---- cache ------------------------------------------------------------------

def cache_path():
    """Under ~/.scribejay via resolve_path, never beside the source tree —
    installed as a tool, that would be site-packages, which a reinstall wipes.
    A function rather than a constant so a test's redirect is seen."""
    return config.resolve_path(CACHE_NAME)


def _cache_get(url: str):
    entry = load_json(cache_path(), {}).get(url)
    if not isinstance(entry, dict) or "text" not in entry:
        return None
    return {"url": url, "title": entry.get("title", ""), "text": entry["text"],
            "cached": True}


def _cache_put(url: str, title: str, text: str) -> None:
    """Store one result and prune anything past the TTL. Pruning on write is
    the convention for every polling store here: nothing else ever runs, so a
    store that only grows is a store that grows forever."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=CACHE_TTL_DAYS)).isoformat()
    path = cache_path()
    try:
        with locked(path):
            data = load_json(path, {})
            data = {k: v for k, v in data.items()
                    if isinstance(v, dict) and v.get("fetched_at", "") >= cutoff}
            data[url] = {
                "title": title, "text": text, "fetched_at": now.isoformat(),
            }
            atomic_write_json(path, data)
    except OSError as e:
        # A cache that cannot be written costs a repeat fetch, never a run.
        logger.warning("could not write the web-fetch cache at %s: %s", path, e)


# ---- the fetch --------------------------------------------------------------

def _extract(html: str, url: str) -> tuple[str, str]:
    """(title, text) from raw HTML, or ("", "") when nothing readable is there.

    trafilatura is imported here, not at module scope, and is an optional
    extra — the same shape google-genai already has for the Gemini backend. An
    install that never switches web fetch on never needs the dependency, and a
    missing one has to read as "no text", not as an ImportError up through a
    5:15 AM task.
    """
    try:
        import trafilatura
        from trafilatura.metadata import extract_metadata
    except ImportError:
        logger.warning("trafilatura is not installed — install the 'webfetch' "
                       "extra to use the fetcher")
        return "", ""

    text = trafilatura.extract(html, url=url, include_comments=False,
                               include_tables=False, favor_precision=True) or ""
    title = ""
    try:
        meta = extract_metadata(html)
        title = (getattr(meta, "title", "") or "") if meta else ""
    except Exception:
        # Metadata is a nicety; the text is the point.
        title = ""
    return title, text


def fetch_local(url: str, timeout: float) -> dict:
    """Fetch and extract from this machine. `{"title","text"}` or an error."""
    try:
        # `with`, not a finally: requests.get itself can raise, and a finally
        # closing a name that was never bound turns a timeout into a NameError.
        with requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            stream=True,
        ) as response:
            if response.status_code in (401, 403, 407, 429, 451):
                # A refusal. Recorded and dropped — never retried by another route.
                return {"error": f"blocked: HTTP {response.status_code}"}
            response.raise_for_status()

            content_type = (response.headers.get("Content-Type") or "").lower()
            if "html" not in content_type and "text" not in content_type:
                return {"error": f"not a text page: {content_type or 'unknown type'}"}

            chunks, total = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                chunks.append(chunk)
                total += len(chunk)
                if total >= MAX_RESPONSE_BYTES:
                    break
            html = b"".join(chunks).decode(response.encoding or "utf-8", "replace")
    except Exception as e:
        return http_error(e)

    title, text = _extract(html, url)
    if not text:
        return {"error": "no readable text extracted"}
    return {"title": title, "text": text[:MAX_TEXT_CHARS]}


# ---- the seam ---------------------------------------------------------------

def fetch_page(url: str, timeout: float | None = None,
               use_cache: bool = True) -> dict:
    """One page's readable text.

    Returns {"url", "title", "text", "cached"} or {"error", "url"}. Never
    raises — a failing source reads as empty to callers, like every other one.
    """
    url = safe_url(url)
    if not url:
        return {"error": "not an http(s) url", "url": url}
    if timeout is None:
        timeout = float(config.getenv("SCRIBEJAY_WEB_FETCH_TIMEOUT"))

    hit = _cache_get(url) if use_cache else None
    if hit is None:
        result = fetch_local(url, timeout)
        if "error" in result:
            logger.info("web fetch failed for %s: %s", url, result["error"])
            return {"error": result["error"], "url": url}
        # Stored before the thinness check below, not after: the bytes were
        # already spent, and a shell today is still a shell on the next re-run.
        _cache_put(url, result.get("title", ""), result["text"])
        hit = {"url": url, "title": result.get("title", ""),
               "text": result["text"], "cached": False}

    # After the cache, so a thin page stored earlier is rejected on every later
    # read too. Checking only the fresh path would let one cached shell be
    # summarised for the next fourteen days.
    if len(hit["text"]) < MIN_USEFUL_CHARS:
        error = f"only {len(hit['text'])} chars of readable text"
        logger.info("web fetch thin for %s: %s", url, error)
        return {"error": error, "url": url}

    return hit


def fetch_pages(candidates: list, timeout: float | None = None,
                logger_=None) -> tuple[list, dict]:
    """Fetch every candidate. Returns (pages, stats).

    Partial success is success: a page that fails is dropped and the rest are
    returned.
    """
    log = logger_ or logger
    pages, stats = [], {"attempted": 0, "fetched": 0, "failed": 0,
                        "cached": 0, "seconds": 0.0}
    started = time.monotonic()
    for candidate in candidates:
        stats["attempted"] += 1
        result = fetch_page(candidate["url"], timeout=timeout)
        if "error" in result:
            stats["failed"] += 1
            continue
        stats["fetched"] += 1
        stats["cached"] += 1 if result.get("cached") else 0
        pages.append({**candidate,
                      "title": result.get("title") or candidate.get("title", ""),
                      "text": result["text"]})
    stats["seconds"] = round(time.monotonic() - started, 1)
    if stats["failed"]:
        log.info("web fetch: %d of %d page(s) could not be read",
                 stats["failed"], stats["attempted"])
    return pages, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--no-cache", action="store_true",
                        help="fetch for real even if this url is cached")
    parser.add_argument("--timeout", type=float, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return print_result(fetch_page(args.url, timeout=args.timeout,
                                   use_cache=not args.no_cache))


if __name__ == "__main__":
    sys.exit(main())
