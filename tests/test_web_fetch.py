"""Tests for scribejay/sources/web_fetch.py — the refusal rule, the thinness
rule, and the disk cache.

The autouse guard in conftest.py blocks the fetcher suite-wide; every test here
re-patches it, and a test that does not leaves it blocked on purpose — that is
what proves the cached path makes no request at all.
"""

import json

import pytest
import requests

import scribejay.sources.web_fetch as wf

# Captured at import, which happens before any autouse fixture runs — so this
# is the real function, not conftest's egress guard. A test that means to
# exercise the real fetch asks for the fixture below and gets it back; every
# other test leaves the guard in place, which is what makes "this path made no
# request" an assertion rather than a hope.
_REAL_FETCH_LOCAL = wf.fetch_local


@pytest.fixture
def real_local(monkeypatch):
    monkeypatch.setattr(wf, "fetch_local", _REAL_FETCH_LOCAL)
    # The suite makes no DNS lookups. Every name is public unless a test
    # answers _resolve itself.
    monkeypatch.setattr(wf, "_resolve", lambda host: {"93.184.215.14"})


def _ok(text="x" * 1000, title="A Title"):
    return lambda url, timeout: {"title": title, "text": text}


# ---- what comes back --------------------------------------------------------

def test_a_good_page_comes_back_with_its_text(monkeypatch):
    monkeypatch.setattr(wf, "fetch_local", _ok())
    result = wf.fetch_page("https://example.com/a")
    assert result["text"] == "x" * 1000
    assert result["title"] == "A Title"
    assert result["cached"] is False


def test_a_fetch_error_is_returned_not_raised(monkeypatch):
    monkeypatch.setattr(wf, "fetch_local", lambda url, timeout: {"error": "boom"})
    assert wf.fetch_page("https://example.com/a")["error"] == "boom"


def test_a_non_http_url_is_refused_before_any_request():
    assert "error" in wf.fetch_page("javascript:alert(1)")


# ---- a shell is not a page --------------------------------------------------

def test_a_page_too_thin_to_summarise_is_an_error(monkeypatch):
    """A cookie banner, a nav bar, a JavaScript app that never rendered. There
    is no second fetcher to try, and summarising page furniture would spend a
    model call to learn nothing."""
    monkeypatch.setattr(wf, "fetch_local", _ok(text="tiny"))
    result = wf.fetch_page("https://example.com/spa")
    assert "only 4 chars" in result["error"]
    assert "text" not in result


def test_a_thin_page_is_still_cached_so_it_is_not_re_fetched(monkeypatch):
    """The bytes were already spent, and a shell today is still a shell on the
    next re-run. The caller sees the same error either way."""
    calls = []
    monkeypatch.setattr(wf, "fetch_local",
                        lambda url, timeout: calls.append(url) or {"title": "", "text": "tiny"})
    assert "error" in wf.fetch_page("https://example.com/spa")
    assert "error" in wf.fetch_page("https://example.com/spa")
    assert len(calls) == 1


def test_a_page_just_over_the_line_is_kept(monkeypatch):
    """The paired case, so the test above cannot pass by rejecting everything."""
    monkeypatch.setattr(wf, "fetch_local", _ok(text="y" * wf.MIN_USEFUL_CHARS))
    assert "error" not in wf.fetch_page("https://example.com/short")


# ---- refusals ---------------------------------------------------------------

class _Response:
    def __init__(self, status=200, headers=None, body=b""):
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html"}
        self._body = body
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


# ---- where the request lands -----------------------------------------------
#
# Every other guard in the pipeline runs against the url the user VISITED.
# These are about the urls a redirect points at, which a remote page chooses —
# spelling included.

_PAGE = b"<html><body>" + b"word " * 400 + b"</body></html>"


def _route(monkeypatch, pages):
    """requests.get answering by url. Returns the list of urls actually
    requested, which is what the guard exists to keep short."""
    sent = []

    def _get(url, **kwargs):
        # The whole fix rests on this: with requests following redirects, every
        # hop is requested before the guard can see it.
        assert kwargs.get("allow_redirects") is False
        sent.append(url)
        return pages[url]

    monkeypatch.setattr(wf.requests, "get", _get)
    return sent


def _redirect(to, status=302):
    return _Response(status=status, headers={"Location": to})


@pytest.mark.parametrize("target", [
    "http://127.0.0.1:8420/dashboard",
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.1.10/admin",
    "http://10.0.0.5/",
    "http://172.20.1.1/",
    "http://100.64.0.1/",
    "http://mymac.local/notes",
    "http://localhost/secret",
    # The same places, spelled the way a redirect gets to spell them.
    "http://2130706433/",
    "http://0x7f000001/",
    "http://localhost./secret",
    "http://[::1]/",
    "http://[fe80::1]/",
    "http://[fd00::1]/",
    "http://[::ffff:127.0.0.1]/",
])
def test_a_redirect_to_a_private_host_is_never_requested(monkeypatch, real_local, target):
    """A public page answers with a 302 to this machine. The body must not
    reach the vault — and the GET must not reach the dev server either."""
    sent = _route(monkeypatch, {"https://good.example/post": _redirect(target)})
    result = wf.fetch_local("https://good.example/post", timeout=5)
    assert "redirected to a private host" in result["error"]
    assert sent == ["https://good.example/post"]


def test_a_public_name_that_resolves_private_is_refused(monkeypatch, real_local):
    """No spelling rule can catch this one: the name is ordinary, and only DNS
    knows it points at 10.0.0.5."""
    monkeypatch.setattr(wf, "_resolve", lambda host: (
        {"10.0.0.5"} if host == "sneaky.example" else {"93.184.215.14"}))
    sent = _route(monkeypatch, {
        "https://good.example/post": _redirect("http://sneaky.example/")})
    assert "10.0.0.5" in wf.fetch_local("https://good.example/post", timeout=5)["error"]
    assert sent == ["https://good.example/post"]


def test_one_private_address_among_public_ones_is_enough_to_refuse(monkeypatch, real_local):
    monkeypatch.setattr(wf, "_resolve", lambda host: {"93.184.215.14", "127.0.0.1"})
    sent = _route(monkeypatch, {})
    assert "127.0.0.1" in wf.fetch_local("https://good.example/post", timeout=5)["error"]
    assert sent == []


def test_a_name_that_does_not_resolve_is_refused(monkeypatch, real_local):
    """Fail closed. The request would fail anyway."""
    def _nxdomain(host):
        raise OSError("nodename nor servname provided")
    monkeypatch.setattr(wf, "_resolve", _nxdomain)
    sent = _route(monkeypatch, {})
    assert "does not resolve" in wf.fetch_local("https://good.example/post", timeout=5)["error"]
    assert sent == []


def test_a_refused_redirect_is_logged(monkeypatch, real_local, caplog):
    """A silently dropped page reads exactly like a page that was never worth
    fetching. The operator needs to be able to tell those apart."""
    _route(monkeypatch, {"https://good.example/post": _redirect("http://127.0.0.1/x")})
    with caplog.at_level("WARNING", logger=wf.logger.name):
        wf.fetch_local("https://good.example/post", timeout=5)
    assert "127.0.0.1" in caplog.text


def test_an_ordinary_redirect_is_still_followed(monkeypatch, real_local):
    """http to https, and a trailing slash, are most of the redirects a real
    browsing history contains. A relative Location is resolved against the
    url that sent it."""
    sent = _route(monkeypatch, {
        "http://good.example/post": _redirect("https://good.example/post", 301),
        "https://good.example/post": _redirect("/post/", 308),
        "https://good.example/post/": _Response(body=_PAGE),
    })
    assert "error" not in wf.fetch_local("http://good.example/post", timeout=5)
    assert sent == ["http://good.example/post", "https://good.example/post",
                    "https://good.example/post/"]


def test_a_redirect_to_another_scheme_is_refused(monkeypatch, real_local):
    _route(monkeypatch, {"https://good.example/post": _redirect("file:///etc/passwd")})
    assert "non-http" in wf.fetch_local("https://good.example/post", timeout=5)["error"]


def test_a_redirect_loop_stops(monkeypatch, real_local):
    sent = _route(monkeypatch, {
        "https://good.example/a": _redirect("https://good.example/b"),
        "https://good.example/b": _redirect("https://good.example/a"),
    })
    assert "redirects" in wf.fetch_local("https://good.example/a", timeout=5)["error"]
    assert len(sent) == wf.MAX_REDIRECTS + 1


# ---- many pages -------------------------------------------------------------

def test_fetch_pages_drops_a_failure_and_keeps_going(monkeypatch):
    def _fetch(url, timeout=None):
        if "bad" in url:
            return {"error": "blocked: HTTP 403", "url": url}
        return {"url": url, "title": "T", "text": "body", "cached": False}

    monkeypatch.setattr(wf, "fetch_page", _fetch)
    pages, stats = wf.fetch_pages([
        {"domain": "a.com", "path": "/1", "url": "https://a.com/bad", "title": ""},
        {"domain": "b.com", "path": "/2", "url": "https://b.com/good", "title": ""},
    ])
    assert [p["url"] for p in pages] == ["https://b.com/good"]
    assert (stats["fetched"], stats["failed"]) == (1, 1)


# ---- cache ------------------------------------------------------------------

def test_a_cached_url_is_not_fetched_again(monkeypatch):
    calls = []

    def _counting(url, timeout):
        calls.append(url)
        return {"title": "T", "text": "x" * 1000}

    monkeypatch.setattr(wf, "fetch_local", _counting)
    first = wf.fetch_page("https://example.com/a")
    second = wf.fetch_page("https://example.com/a")

    assert len(calls) == 1
    assert second["cached"] is True
    assert second["text"] == first["text"]


def test_use_cache_false_forces_a_real_fetch(monkeypatch):
    calls = []
    monkeypatch.setattr(wf, "fetch_local",
                        lambda url, timeout: calls.append(url) or {"title": "", "text": "x" * 1000})
    wf.fetch_page("https://example.com/a")
    wf.fetch_page("https://example.com/a", use_cache=False)
    assert len(calls) == 2


def test_expired_entries_are_pruned_on_write(monkeypatch):
    path = wf.cache_path()
    path.write_text(json.dumps({
        "https://old.com/a": {"title": "", "text": "old",
                              "fetched_at": "2020-01-01T00:00:00+00:00"},
    }))
    monkeypatch.setattr(wf, "fetch_local", _ok())
    wf.fetch_page("https://new.com/a")

    stored = json.loads(path.read_text())
    assert "https://old.com/a" not in stored
    assert "https://new.com/a" in stored


def test_an_unwritable_cache_costs_a_repeat_fetch_not_the_run(monkeypatch):
    monkeypatch.setattr(wf, "cache_path",
                        lambda: wf.config.resolve_path("no/such/dir/cache.json"))
    monkeypatch.setattr(wf, "fetch_local", _ok())
    result = wf.fetch_page("https://example.com/a")
    assert result["text"] == "x" * 1000


# ---- bounds -----------------------------------------------------------------

def test_extracted_text_is_capped(monkeypatch, real_local):
    monkeypatch.setattr(wf, "_extract", lambda html, url: ("T", "z" * 99999))
    monkeypatch.setattr(wf.requests, "get", lambda *a, **k: _Response(body=b"<html></html>"))
    assert len(wf.fetch_local("https://example.com/a", 5)["text"]) == wf.MAX_TEXT_CHARS


def test_a_missing_trafilatura_reads_as_no_text(monkeypatch):
    """An install that never switched web fetch on does not have the optional
    extra. That must be an empty result, not an ImportError up through a 4:15
    AM task."""
    import builtins
    real_import = builtins.__import__

    def _no_trafilatura(name, *a, **k):
        if name.startswith("trafilatura"):
            raise ImportError("no module named trafilatura")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_trafilatura)
    assert wf._extract("<html>hi</html>", "https://e.com") == ("", "")
