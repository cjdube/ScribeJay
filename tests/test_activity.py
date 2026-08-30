"""Tests for scribejay/activity.py — the compaction helpers shared by the
daily activity reviews (daily_chrome_learnings, daily_youtube_learnings,
ai_chat_learnings): the excluded-domain/keyword filters, compact_sites, and
compact_videos.

Mirrors the compaction slice of LocalLLMAgent's tests/test_activity_log.py,
plus the compact_videos tests from tests/test_youtube.py (compact_videos is
a scribejay/activity.py helper, not part of scribejay/sources/youtube.py).
persist_or_email/write_entry moved to scribejay.sinks.vault (a sink, not a
compaction concern) and are covered in tests/test_vault.py instead."""

import pytest

from scribejay import activity as lc


@pytest.fixture
def excluded(monkeypatch):
    """_EXCLUDED_DOMAINS is built from preferences at import, so patch the list
    itself rather than the JSON (same shape as _EXCLUDED_KEYWORDS)."""
    monkeypatch.setattr(lc, "_EXCLUDED_DOMAINS", ["sharepoint.com", "signupgenius.com"])


def _site(domain, visits=1):
    return {"domain": domain, "title": f"{domain} page", "url": f"https://{domain}/x", "visits": visits}


def test_compact_sites_drops_excluded_domain(excluded):
    out = lc.compact_sites([_site("www.signupgenius.com"), _site("ai.google.dev")])
    assert [s["domain"] for s in out] == ["ai.google.dev"]


def test_compact_sites_drops_subdomains_of_excluded(excluded):
    # The whole point of suffix matching: one entry covers every M365 tenant.
    out = lc.compact_sites([_site("acme.sharepoint.com"), _site("ai.google.dev")])
    assert [s["domain"] for s in out] == ["ai.google.dev"]


def test_compact_sites_keeps_lookalike_domain(excluded):
    # Must not match by bare substring: "notsharepoint.com" isn't a subdomain.
    out = lc.compact_sites([_site("notsharepoint.com")])
    assert [s["domain"] for s in out] == ["notsharepoint.com"]


def test_compact_sites_excludes_before_the_cap(excluded, monkeypatch):
    # An excluded site must not consume one of the MAX_CHROME_SITES slots.
    monkeypatch.setattr(lc, "MAX_CHROME_SITES", 2)
    out = lc.compact_sites([
        _site("acme.sharepoint.com", visits=99),
        _site("ai.google.dev", visits=5),
        _site("tailscale.com", visits=3),
    ])
    assert [s["domain"] for s in out] == ["ai.google.dev", "tailscale.com"]


def test_compact_sites_strips_port_before_matching(monkeypatch):
    monkeypatch.setattr(lc, "_EXCLUDED_DOMAINS", ["127.0.0.1"])
    out = lc.compact_sites([_site("127.0.0.1:8420"), _site("ai.google.dev")])
    assert [s["domain"] for s in out] == ["ai.google.dev"]


def test_compact_sites_drops_url_and_sorts_by_visits(excluded):
    out = lc.compact_sites([_site("a.com", visits=1), _site("b.com", visits=9)])
    assert [s["domain"] for s in out] == ["b.com", "a.com"]
    assert "url" not in out[0]


def test_compact_sites_carries_page_paths_through(excluded):
    site = _site("ai.google.dev")
    site["pages"] = [{"path": "/docs/models", "visits": 9}, {"path": "/docs/pricing", "visits": 4}]
    out = lc.compact_sites([site])
    assert out[0]["pages"] == ["/docs/models", "/docs/pricing"]


def test_compact_sites_caps_pages_per_site(excluded, monkeypatch):
    monkeypatch.setattr(lc, "MAX_PAGES_PER_SITE", 2)
    site = _site("ai.google.dev")
    site["pages"] = [{"path": f"/p{i}", "visits": i} for i in range(6)]
    assert len(lc.compact_sites([site])[0]["pages"]) == 2


def test_compact_sites_omits_pages_when_absent(excluded):
    # fetch_chrome_history's default returns no `pages` — degrade to domain+title.
    assert "pages" not in lc.compact_sites([_site("ai.google.dev")])[0]


# --------------------------------------------------------------------------- #
# is_excluded_text — subject-matter exclusions a domain can't express
# --------------------------------------------------------------------------- #

@pytest.fixture
def excluded_kw(monkeypatch):
    monkeypatch.setattr(lc, "_EXCLUDED_KEYWORDS", ["acme"])


def test_is_excluded_text_is_case_insensitive(excluded_kw):
    assert lc.is_excluded_text("Acme Speakers Bureau")
    assert lc.is_excluded_text("volunteering for acme")
    assert not lc.is_excluded_text("Gemini API pricing")
    assert not lc.is_excluded_text("")


def test_compact_sites_drops_site_whose_title_matches(excluded_kw):
    # The domain is fine; the subject isn't.
    site = _site("www.eventbrite.com")
    site["title"] = "Acme Speakers Bureau — Register"
    out = lc.compact_sites([site, _site("ai.google.dev")])
    assert [s["domain"] for s in out] == ["ai.google.dev"]


def test_compact_sites_drops_only_the_matching_path_not_the_site(excluded_kw):
    # One excluded page on an otherwise reviewable site must not drop the site.
    site = _site("www.linkedin.com")
    site["pages"] = [{"path": "/feed/", "visits": 5},
                     {"path": "/company/acme/", "visits": 2}]
    out = lc.compact_sites([site])
    assert out[0]["pages"] == ["/feed/"]


def test_compact_sites_no_keywords_keeps_everything(monkeypatch):
    monkeypatch.setattr(lc, "_EXCLUDED_KEYWORDS", [])
    site = _site("www.eventbrite.com")
    site["title"] = "Acme Speakers Bureau"
    assert len(lc.compact_sites([site])) == 1


def test_compact_sites_no_exclusions_configured_keeps_everything(monkeypatch):
    monkeypatch.setattr(lc, "_EXCLUDED_DOMAINS", [])
    out = lc.compact_sites([_site("www.signupgenius.com")])
    assert [s["domain"] for s in out] == ["www.signupgenius.com"]


# --------------------------------------------------------------------------- #
# compact_videos
# --------------------------------------------------------------------------- #

def test_compact_videos_caps_count():
    videos = [{"title": f"v{i}", "channel": "c", "description": "d", "url": "u"}
             for i in range(lc.MAX_YOUTUBE_VIDEOS + 5)]
    assert len(lc.compact_videos(videos)) == lc.MAX_YOUTUBE_VIDEOS


def test_compact_videos_truncates_description():
    videos = [{"title": "t", "channel": "c",
              "description": "x" * (lc.MAX_YOUTUBE_DESC_CHARS + 100), "url": "u"}]
    assert len(lc.compact_videos(videos)[0]["description"]) == lc.MAX_YOUTUBE_DESC_CHARS


def test_compact_videos_keeps_only_expected_fields():
    videos = [{"title": "t", "channel": "c", "description": "d", "url": "u",
              "video_id": "x", "liked_at": "z"}]
    assert set(lc.compact_videos(videos)[0]) == {"title", "channel", "description", "url"}
