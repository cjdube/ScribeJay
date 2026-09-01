"""Fetch the readable text of one web page.

The daily Chrome review knows which pages were read but not what they said —
`sources/chrome.py` hands on a domain, a title and a path, and the model has to
guess the rest. This module closes that gap for a handful of pages a day.

**Local first, on purpose.** AGENTS.md's data-sourcing policy prefers free and
official sources and rules out paid SaaS for data, and ScribeJay's whole
premise is that nothing leaves the Mac unless the user opts in. So the default
backend is an ordinary `requests.get` from this machine — the same request the
browser already made — with readable text pulled out by `trafilatura`.

**Firecrawl is the opt-in fallback**, for the pages a plain GET cannot render:
a JavaScript app that ships an empty shell. It runs only when a
FIRECRAWL_API_KEY is set, and only after the local attempt came back too thin.
Switching it on means the selected urls *and their content* leave the machine;
`docs/web-fetch.md` says so in the words a user reads.

**A block is a no, not a puzzle.** A 403, a bot wall, or a robots-style refusal
means this page is skipped. ScribeJay does not retry through a stealth proxy,
and `proxy: "basic"` is the only proxy setting Firecrawl is ever asked for.

**Results are cached on disk** under `~/.scribejay`, keyed by url and pruned to
14 days. Re-running a day — a backfill, or the same day twice while comparing
output — then costs no request and no credit.

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
from scribejay.core.http import http_error, print_result, resolve_key
from scribejay.core.store import atomic_write_json, load_json, locked
from scribejay.core.urls import safe_url

logger = logging.getLogger(__name__)

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"

# What one page may contribute. Bounds the summarize call's prompt, and bounds
# how much untrusted text is ever held at once. Deliberately smaller than the
# model's context: this is raw material for a three-line summary, not a
# document to be reproduced.
MAX_TEXT_CHARS = 4000

# Below this, a "successful" fetch is a shell — a cookie banner, a nav bar, a
# JavaScript app that never rendered. The local backend returning this little
# is what makes Firecrawl worth trying, if the user has switched it on.
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


def _cache_key(url: str, backend: str) -> str:
    """Backend is part of the key. The whole point of the bake-off is that
    local and Firecrawl return different text for the same url, and one cache
    entry for both would quietly serve whichever ran first."""
    return f"{backend}::{url}"


def _cache_get(url: str, backend: str):
    entry = load_json(cache_path(), {}).get(_cache_key(url, backend))
    if not isinstance(entry, dict) or "text" not in entry:
        return None
    return {"url": url, "title": entry.get("title", ""), "text": entry["text"],
            "backend": backend, "cached": True}


def _cache_put(url: str, backend: str, title: str, text: str) -> None:
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
            data[_cache_key(url, backend)] = {
                "title": title, "text": text, "fetched_at": now.isoformat(),
            }
            atomic_write_json(path, data)
    except OSError as e:
        # A cache that cannot be written costs a repeat fetch, never a run.
        logger.warning("could not write the web-fetch cache at %s: %s", path, e)


# ---- local backend ----------------------------------------------------------

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
                       "extra to use the local fetcher")
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


# ---- firecrawl backend ------------------------------------------------------

class QuotaExhausted(Exception):
    """Raised so the caller stops asking. A 402 or a 429 means every further
    request this run is wasted latency at 5:15 AM, and partial results already
    in hand are worth keeping."""


def fetch_firecrawl(url: str, timeout: float, api_key: str | None = None) -> dict:
    """Fetch through Firecrawl. `{"title","text"}` or an error.

    `proxy: "basic"` and nothing else — the stealth modes exist to get past a
    site that said no, and AGENTS.md rules that out. `maxAge: 0` disables
    Firecrawl's own cache so a credit buys today's page, not a stale one; this
    module's disk cache is what stops the repeat spending.
    """
    key = resolve_key("FIRECRAWL_API_KEY", api_key)
    if not key:
        return {"error": "FIRECRAWL_API_KEY not set (checked arg, config/.env, "
                         "env var, Keychain)"}
    try:
        response = requests.post(
            FIRECRAWL_SCRAPE_URL,
            timeout=timeout,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True,
                  "proxy": "basic", "maxAge": 0, "timeout": int(timeout * 1000)},
        )
        if response.status_code in (402, 429):
            raise QuotaExhausted(f"firecrawl HTTP {response.status_code}")
        response.raise_for_status()
        payload = response.json()
    except QuotaExhausted:
        raise
    except Exception as e:
        return http_error(e)

    if not payload.get("success", True):
        return {"error": f"firecrawl: {payload.get('error', 'unsuccessful response')}"}
    data = payload.get("data") or {}
    text = (data.get("markdown") or "").strip()
    if not text:
        return {"error": "firecrawl returned no markdown"}
    title = ((data.get("metadata") or {}).get("title") or "")
    return {"title": title, "text": text[:MAX_TEXT_CHARS]}


# ---- the seam ---------------------------------------------------------------

def fetch_page(url: str, timeout: float | None = None, backend: str = "auto",
               use_cache: bool = True) -> dict:
    """One page's readable text, from whichever backend can get it.

    `backend`:
      - "auto"      local first, Firecrawl only if local came back too thin
                    AND a key is set. The production path.
      - "local"     this machine only. Never spends a credit.
      - "firecrawl" Firecrawl only. Used by the bake-off to compare fairly.

    Returns {"url", "title", "text", "backend", "cached"} or {"error", "url"}.
    Never raises except QuotaExhausted, which the caller must catch to stop
    spending.
    """
    url = safe_url(url)
    if not url:
        return {"error": "not an http(s) url", "url": url}
    if timeout is None:
        timeout = float(config.getenv("SCRIBEJAY_WEB_FETCH_TIMEOUT"))

    attempts = {"auto": ("local", "firecrawl"),
                "local": ("local",),
                "firecrawl": ("firecrawl",)}.get(backend)
    if attempts is None:
        return {"error": f"unknown backend: {backend}", "url": url}

    last_error = "no backend ran"
    for name in attempts:
        if use_cache:
            hit = _cache_get(url, name)
            if hit:
                return hit

        if name == "firecrawl" and not resolve_key("FIRECRAWL_API_KEY"):
            # Not an error in "auto": it is the ordinary state of an install
            # that never switched Firecrawl on.
            last_error = last_error if backend == "auto" else "FIRECRAWL_API_KEY not set"
            continue

        result = fetch_local(url, timeout) if name == "local" else \
            fetch_firecrawl(url, timeout)

        if "error" in result:
            last_error = result["error"]
            logger.info("web fetch (%s) failed for %s: %s", name, url, last_error)
            continue

        text = result["text"]
        if len(text) < MIN_USEFUL_CHARS and name != attempts[-1]:
            # Thin, and there is another backend to try. Cache it anyway — the
            # bytes were already paid for, and the bake-off's "local" arm wants
            # exactly this result.
            _cache_put(url, name, result.get("title", ""), text)
            last_error = f"only {len(text)} chars of readable text"
            logger.info("web fetch (%s) thin for %s: %s", name, url, last_error)
            continue

        _cache_put(url, name, result.get("title", ""), text)
        return {"url": url, "title": result.get("title", ""), "text": text,
                "backend": name, "cached": False}

    return {"error": last_error, "url": url}


def fetch_pages(candidates: list, timeout: float | None = None,
                backend: str = "auto", logger_=None) -> tuple[list, dict]:
    """Fetch every candidate. Returns (pages, stats).

    Partial success is success: a page that fails is dropped and the rest are
    returned. A quota refusal stops the loop and keeps what is already in hand,
    because every further request would fail the same way.
    """
    log = logger_ or logger
    pages, stats = [], {"attempted": 0, "fetched": 0, "failed": 0,
                        "cached": 0, "quota_stopped": False,
                        "seconds": 0.0}
    started = time.monotonic()
    for candidate in candidates:
        stats["attempted"] += 1
        try:
            result = fetch_page(candidate["url"], timeout=timeout, backend=backend)
        except QuotaExhausted as e:
            log.warning("web fetch stopped early: %s — keeping the %d page(s) "
                        "already fetched", e, len(pages))
            stats["quota_stopped"] = True
            break
        if "error" in result:
            stats["failed"] += 1
            continue
        stats["fetched"] += 1
        stats["cached"] += 1 if result.get("cached") else 0
        pages.append({**candidate, "title": result.get("title") or candidate.get("title", ""),
                      "text": result["text"], "backend": result["backend"]})
    stats["seconds"] = round(time.monotonic() - started, 1)
    return pages, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--backend", default="auto",
                        choices=("auto", "local", "firecrawl"))
    parser.add_argument("--no-cache", action="store_true",
                        help="fetch for real even if this url is cached")
    parser.add_argument("--timeout", type=float, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        result = fetch_page(args.url, timeout=args.timeout, backend=args.backend,
                            use_cache=not args.no_cache)
    except QuotaExhausted as e:
        result = {"error": str(e), "url": args.url}
    return print_result(result)


if __name__ == "__main__":
    sys.exit(main())
