"""Tests for scribejay/sources/web_fetch.py — backend selection, the refusal
and quota rules, and the disk cache.

The autouse guard in conftest.py blocks both backends suite-wide; every test
here re-patches the one it means to exercise, and never the other. A test that
needs neither leaves both blocked on purpose — that is what proves the disabled
and cached paths make no request at all.
"""

import json

import pytest
import requests

import scribejay.sources.web_fetch as wf

# Captured at import, which happens before any autouse fixture runs — so these
# are the real functions, not conftest's egress guard. A test that means to
# exercise the real backend asks for the fixture below and gets it back; every
# other test leaves the guard in place, which is what makes "this path made no
# request" an assertion rather than a hope.
_REAL_FETCH_LOCAL = wf.fetch_local
_REAL_FETCH_FIRECRAWL = wf.fetch_firecrawl


@pytest.fixture
def real_local(monkeypatch):
    monkeypatch.setattr(wf, "fetch_local", _REAL_FETCH_LOCAL)


@pytest.fixture
def real_firecrawl(monkeypatch):
    monkeypatch.setattr(wf, "fetch_firecrawl", _REAL_FETCH_FIRECRAWL)


@pytest.fixture
def no_key(monkeypatch):
    """No Firecrawl key anywhere. The ordinary state of a local-only install."""
    monkeypatch.setattr(wf, "resolve_key", lambda name, arg=None: None)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(wf, "resolve_key", lambda name, arg=None: "fc-test-key")


def _ok(text="x" * 1000, title="A Title"):
    return lambda url, timeout: {"title": title, "text": text}


# ---- backend selection ------------------------------------------------------

def test_local_only_never_touches_firecrawl(monkeypatch, no_key):
    monkeypatch.setattr(wf, "fetch_local", _ok())
    result = wf.fetch_page("https://example.com/a", backend="local")
    assert result["backend"] == "local"
    assert result["text"] == "x" * 1000
    # fetch_firecrawl is still the conftest guard: reaching it would raise.


def test_auto_falls_through_to_firecrawl_when_local_is_thin(monkeypatch, with_key):
    """A JavaScript shell is the case Firecrawl exists for: the GET succeeds
    and returns almost no readable text."""
    monkeypatch.setattr(wf, "fetch_local", _ok(text="tiny"))
    monkeypatch.setattr(wf, "fetch_firecrawl",
                        lambda url, timeout: {"title": "T", "text": "y" * 900})
    result = wf.fetch_page("https://example.com/spa", backend="auto")
    assert result["backend"] == "firecrawl"


def test_auto_keeps_a_good_local_result_and_never_spends_a_credit(monkeypatch, with_key):
    monkeypatch.setattr(wf, "fetch_local", _ok())
    result = wf.fetch_page("https://example.com/article", backend="auto")
    assert result["backend"] == "local"


def test_auto_without_a_key_returns_the_local_error(monkeypatch, no_key):
    monkeypatch.setattr(wf, "fetch_local", lambda url, timeout: {"error": "boom"})
    result = wf.fetch_page("https://example.com/a", backend="auto")
    assert result["error"] == "boom"


def test_thin_local_is_kept_when_there_is_no_firecrawl_to_try(monkeypatch, no_key):
    """`local` is the last backend, so a short page is a result, not a failure.
    Some pages really are short."""
    monkeypatch.setattr(wf, "fetch_local", _ok(text="short but real"))
    result = wf.fetch_page("https://example.com/a", backend="local")
    assert result["text"] == "short but real"


def test_a_non_http_url_is_refused_before_any_request():
    assert "error" in wf.fetch_page("javascript:alert(1)", backend="local")


# ---- refusals and quota -----------------------------------------------------

class _Response:
    def __init__(self, status=200, headers=None, body=b"", payload=None):
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html"}
        self._body = body
        self._payload = payload
        self.encoding = "utf-8"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)

    def iter_content(self, chunk_size=0):
        yield self._body

    def json(self):
        return self._payload


@pytest.mark.parametrize("status", [401, 403, 407, 429, 451])
def test_a_refusal_is_reported_and_never_worked_around(monkeypatch, real_local, status):
    """AGENTS.md: a site that says no is skipped. There is no stealth retry to
    assert the absence of, so this asserts the shape that keeps it that way —
    a blocked page becomes an error dict, and fetch_local returns."""
    monkeypatch.setattr(wf.requests, "get", lambda *a, **k: _Response(status=status))
    result = wf.fetch_local("https://example.com/a", timeout=5)
    assert result["error"] == f"blocked: HTTP {status}"


def test_a_non_text_response_is_not_extracted(monkeypatch, real_local):
    monkeypatch.setattr(wf.requests, "get", lambda *a, **k: _Response(
        headers={"Content-Type": "application/pdf"}))
    assert "not a text page" in wf.fetch_local("https://example.com/a.pdf", timeout=5)["error"]


def test_a_network_error_becomes_an_error_dict_not_an_exception(monkeypatch, real_local):
    def _boom(*a, **k):
        raise requests.exceptions.Timeout("too slow")
    monkeypatch.setattr(wf.requests, "get", _boom)
    assert "error" in wf.fetch_local("https://example.com/a", timeout=5)


@pytest.mark.parametrize("status", [402, 429])
def test_firecrawl_quota_raises_so_the_caller_stops_asking(monkeypatch, real_firecrawl, with_key, status):
    monkeypatch.setattr(wf.requests, "post", lambda *a, **k: _Response(status=status))
    with pytest.raises(wf.QuotaExhausted):
        wf.fetch_firecrawl("https://example.com/a", timeout=5)


def test_fetch_pages_stops_on_quota_but_keeps_what_it_has(monkeypatch):
    calls = []

    def _fetch(url, timeout=None, backend="auto"):
        calls.append(url)
        if len(calls) == 1:
            return {"url": url, "title": "T", "text": "body", "backend": "firecrawl",
                    "cached": False}
        raise wf.QuotaExhausted("firecrawl HTTP 402")

    monkeypatch.setattr(wf, "fetch_page", _fetch)
    candidates = [{"domain": "a.com", "path": "/1", "url": "https://a.com/1", "title": ""},
                  {"domain": "b.com", "path": "/2", "url": "https://b.com/2", "title": ""},
                  {"domain": "c.com", "path": "/3", "url": "https://c.com/3", "title": ""}]
    pages, stats = wf.fetch_pages(candidates, backend="firecrawl")

    assert len(pages) == 1                 # the success is kept
    assert len(calls) == 2                 # the third was never attempted
    assert stats["quota_stopped"] is True


def test_fetch_pages_drops_a_failure_and_keeps_going(monkeypatch):
    def _fetch(url, timeout=None, backend="auto"):
        if "bad" in url:
            return {"error": "blocked: HTTP 403", "url": url}
        return {"url": url, "title": "T", "text": "body", "backend": "local", "cached": False}

    monkeypatch.setattr(wf, "fetch_page", _fetch)
    pages, stats = wf.fetch_pages([
        {"domain": "a.com", "path": "/1", "url": "https://a.com/bad", "title": ""},
        {"domain": "b.com", "path": "/2", "url": "https://b.com/good", "title": ""},
    ])
    assert [p["url"] for p in pages] == ["https://b.com/good"]
    assert (stats["fetched"], stats["failed"]) == (1, 1)


def test_firecrawl_asks_for_basic_proxy_and_no_provider_cache(monkeypatch, real_firecrawl, with_key):
    """The stealth proxy modes exist to get past a site that refused. Asking for
    one would be the scraping-SaaS workaround AGENTS.md rules out, so the
    request body is asserted rather than left to a comment."""
    sent = {}

    def _post(url, **kwargs):
        sent.update(kwargs["json"])
        return _Response(payload={"success": True,
                                  "data": {"markdown": "z" * 900,
                                           "metadata": {"title": "T"}}})

    monkeypatch.setattr(wf.requests, "post", _post)
    wf.fetch_firecrawl("https://example.com/a", timeout=5)
    assert sent["proxy"] == "basic"
    assert sent["maxAge"] == 0


def test_firecrawl_without_a_key_is_an_error_not_a_call(real_firecrawl, no_key):
    assert "FIRECRAWL_API_KEY not set" in wf.fetch_firecrawl("https://e.com/a", 5)["error"]


# ---- cache ------------------------------------------------------------------

def test_a_cached_url_is_not_fetched_again(monkeypatch, no_key):
    calls = []

    def _counting(url, timeout):
        calls.append(url)
        return {"title": "T", "text": "x" * 1000}

    monkeypatch.setattr(wf, "fetch_local", _counting)
    first = wf.fetch_page("https://example.com/a", backend="local")
    second = wf.fetch_page("https://example.com/a", backend="local")

    assert len(calls) == 1
    assert second["cached"] is True
    assert second["text"] == first["text"]


def test_the_two_backends_do_not_share_a_cache_entry(monkeypatch, with_key):
    """The bake-off compares local text against Firecrawl text for the same
    url. One entry for both would silently serve whichever ran first, and the
    comparison would be of one fetcher against itself."""
    monkeypatch.setattr(wf, "fetch_local", _ok(text="local text " + "x" * 500))
    monkeypatch.setattr(wf, "fetch_firecrawl",
                        lambda url, timeout: {"title": "T", "text": "firecrawl text " + "y" * 500})

    local = wf.fetch_page("https://example.com/a", backend="local")
    fire = wf.fetch_page("https://example.com/a", backend="firecrawl")
    assert local["text"].startswith("local text")
    assert fire["text"].startswith("firecrawl text")


def test_use_cache_false_forces_a_real_fetch(monkeypatch, no_key):
    calls = []
    monkeypatch.setattr(wf, "fetch_local",
                        lambda url, timeout: calls.append(url) or {"title": "", "text": "x" * 1000})
    wf.fetch_page("https://example.com/a", backend="local")
    wf.fetch_page("https://example.com/a", backend="local", use_cache=False)
    assert len(calls) == 2


def test_expired_entries_are_pruned_on_write(monkeypatch, no_key):
    path = wf.cache_path()
    path.write_text(json.dumps({
        "local::https://old.com/a": {"title": "", "text": "old",
                                     "fetched_at": "2020-01-01T00:00:00+00:00"},
    }))
    monkeypatch.setattr(wf, "fetch_local", _ok())
    wf.fetch_page("https://new.com/a", backend="local")

    stored = json.loads(path.read_text())
    assert "local::https://old.com/a" not in stored
    assert "local::https://new.com/a" in stored


def test_an_unwritable_cache_costs_a_repeat_fetch_not_the_run(monkeypatch, no_key):
    monkeypatch.setattr(wf, "cache_path",
                        lambda: wf.config.resolve_path("no/such/dir/cache.json"))
    monkeypatch.setattr(wf, "fetch_local", _ok())
    result = wf.fetch_page("https://example.com/a", backend="local")
    assert result["text"] == "x" * 1000


# ---- bounds -----------------------------------------------------------------

def test_extracted_text_is_capped(monkeypatch, real_local, no_key):
    monkeypatch.setattr(wf, "_extract", lambda html, url: ("T", "z" * 99999))
    monkeypatch.setattr(wf.requests, "get", lambda *a, **k: _Response(body=b"<html></html>"))
    assert len(wf.fetch_local("https://example.com/a", 5)["text"]) == wf.MAX_TEXT_CHARS


def test_a_missing_trafilatura_reads_as_no_text(monkeypatch):
    """An install that never switched web fetch on does not have the optional
    extra. That must be an empty result, not an ImportError up through a 5:15
    AM task."""
    import builtins
    real_import = builtins.__import__

    def _no_trafilatura(name, *a, **k):
        if name.startswith("trafilatura"):
            raise ImportError("no module named trafilatura")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_trafilatura)
    assert wf._extract("<html>hi</html>", "https://e.com") == ("", "")
