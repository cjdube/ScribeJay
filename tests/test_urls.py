"""Tests for scribejay/core/urls.py — the scheme allow-list guarding every
externally sourced URL before it reaches HTML or Markdown, and the private-host
rule guarding every url before the fetcher sends a request to it."""

import pytest

from scribejay.core.urls import is_private_host, safe_url


def test_safe_url_allows_http():
    assert safe_url("http://example.com/x") == "http://example.com/x"


def test_safe_url_allows_https():
    assert safe_url("https://example.com/x") == "https://example.com/x"


def test_safe_url_rejects_javascript_scheme():
    assert safe_url("javascript:alert(1)") == ""


def test_safe_url_rejects_data_scheme():
    assert safe_url("data:text/html,<script>alert(1)</script>") == ""


def test_safe_url_rejects_other_schemes():
    assert safe_url("ftp://example.com/x") == ""


def test_safe_url_rejects_relative_and_empty():
    assert safe_url("/relative/path") == ""
    assert safe_url("") == ""


def test_safe_url_tolerates_non_string():
    # urlparse raises AttributeError on None; a feed with a null url should
    # cost its link, not the whole digest.
    assert safe_url(None) == ""


# ---- the Markdown link delimiters --------------------------------------------
#
# A scheme-clean url can still close the `(...)` a caller renders it into, and
# everything after the `)` then renders as Markdown chosen by whoever controls
# the url. This is the destination-side twin of core/text.py:safe_label.

def test_safe_url_cannot_close_a_markdown_link():
    out = safe_url("https://good.example/a) [Free gift](http://evil.example")
    assert ")" not in out
    assert "(" not in out
    assert " " not in out


def test_a_closing_paren_alone_is_encoded():
    # The benign case, and the common one: `)` is a legal unencoded path
    # character, so an ordinary Wikipedia url hits this with no attacker.
    out = safe_url("https://en.wikipedia.org/wiki/Python_(programming_language)")
    assert out == "https://en.wikipedia.org/wiki/Python_%28programming_language%29"


def test_an_encoded_url_is_not_encoded_twice():
    # `%` is deliberately not in the table: encoding it would turn %28 into
    # %2528 and break a url that was already correct.
    already = "https://e.example/a%28b%29c"
    assert safe_url(already) == already


def test_the_scheme_check_still_runs_first():
    # The encoding must never make a rejected url look accepted.
    assert safe_url("javascript:alert(1)") == ""


# ---- is_private_host ----------------------------------------------------------

@pytest.mark.parametrize("host", [
    "", "localhost", "LOCALHOST", "localhost.", "app.localhost", "mymac.local",
    "127.0.0.1", "127.1", "2130706433", "0x7f000001", "0x7f.1", "017700000001",
    "10.0.0.5", "172.16.0.1", "172.31.255.255", "192.168.1.10",
    "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1",
    "::1", "[::1]", "fe80::1", "fd00::1", "::ffff:127.0.0.1",
    "127.0.0.1.nip.io", "localhost:8420",
])
def test_a_private_host_is_private_however_it_is_spelled(host):
    """A redirect picks the spelling, so every one the resolver accepts has to
    be judged as the address it means. `2130706433` IS 127.0.0.1."""
    assert is_private_host(host)


@pytest.mark.parametrize("host", [
    "example.com", "news.ycombinator.com", "8.8.8.8", "172.32.0.1",
    "172.15.255.255", "2606:4700::1", "::ffff:8.8.8.8", "dead.beef",
    "example.com:443",
])
def test_a_public_host_stays_public(host):
    """The other half: a guard that refuses everything is a feature that
    fetches nothing. 172.32 sits just outside the /12 a prefix match would
    get wrong."""
    assert not is_private_host(host)
