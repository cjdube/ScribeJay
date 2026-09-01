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


# ---- candidate_urls ---------------------------------------------------------

def _fetchable_site(domain, pages, title="T", visits=1):
    return {"domain": domain, "title": title, "visits": visits,
            "pages": [{"path": p, "url": f"https://{domain}{p}", "visits": v}
                      for p, v in pages]}


def test_candidate_urls_returns_fetchable_pages():
    out = lc.candidate_urls([_fetchable_site("example.com", [("/blog/post", 1)])], 5)
    assert out == [{"domain": "example.com", "title": "T", "path": "/blog/post",
                    "url": "https://example.com/blog/post"}]


def test_candidate_urls_spreads_across_domains_before_going_deep():
    """Five pages should be five things looked into, not five tabs of one doc
    site. Every domain's best page comes before any domain's second."""
    sites = [
        _fetchable_site("docs.com", [("/docs/one/alpha", 1), ("/docs/one/beta", 1),
                                     ("/docs/one/gamma", 1)]),
        _fetchable_site("news.com", [("/news/one/story", 1)]),
    ]
    domains = [c["domain"] for c in lc.candidate_urls(sites, 3)]
    assert domains[:2] == ["docs.com", "news.com"]  # one each, then back around
    assert domains[2] == "docs.com"


def test_candidate_urls_prefers_a_read_article_over_a_habitual_dashboard():
    """Nine visits to a shallow path is navigation. One visit to a deep path is
    reading. fetch_chrome_history orders by visits; this deliberately does not."""
    sites = [_fetchable_site("example.com", [("/dashboard", 9), ("/blog/deep/article", 1)])]
    assert lc.candidate_urls(sites, 1)[0]["path"] == "/blog/deep/article"


def test_candidate_urls_honours_the_limit():
    sites = [_fetchable_site(f"site{n}.com", [("/blog/post", 1)]) for n in range(10)]
    assert len(lc.candidate_urls(sites, 3)) == 3


@pytest.mark.parametrize("path", [
    "/login", "/account/settings", "/admin/users", "/checkout",
    "/oauth/callback", "/reset-password",
])
def test_candidate_urls_skips_session_and_account_pages(path):
    """A login wall says nothing about the day, and an account page is the
    user's own data rather than something they read."""
    assert lc.candidate_urls([_fetchable_site("example.com", [(path, 1)])], 5) == []


@pytest.mark.parametrize("path", ["/paper.pdf", "/img/logo.png", "/data/export.csv",
                                  "/dl/app.dmg", "/clip.mp4"])
def test_candidate_urls_skips_pages_that_are_not_text(path):
    assert lc.candidate_urls([_fetchable_site("example.com", [(path, 1)])], 5) == []


@pytest.mark.parametrize("host", [
    "localhost", "127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.4.2",
    "172.31.255.1", "mymac.local", "169.254.1.1",
])
def test_candidate_urls_skips_this_machine_and_its_network(host):
    """A dev server's content is the user's own work in progress, not reading —
    and sending it to a third-party fetcher would be worse than pointless."""
    site = {"domain": host, "title": "T", "visits": 1,
            "pages": [{"path": "/a/b", "url": f"http://{host}/a/b", "visits": 1}]}
    assert lc.candidate_urls([site], 5) == []


def test_candidate_urls_keeps_a_public_172_address():
    """172.16-31 is private; 172.32 is not. A prefix match would drop both."""
    site = {"domain": "172.32.0.1", "title": "T", "visits": 1,
            "pages": [{"path": "/blog/post", "url": "http://172.32.0.1/blog/post",
                       "visits": 1}]}
    assert len(lc.candidate_urls([site], 5)) == 1


def test_candidate_urls_refuses_a_dangerous_scheme(monkeypatch):
    site = {"domain": "example.com", "title": "T", "visits": 1,
            "pages": [{"path": "/a", "url": "javascript:alert(1)", "visits": 1}]}
    assert lc.candidate_urls([site], 5) == []


def test_candidate_urls_applies_the_users_exclusions(monkeypatch):
    """The same filter compact_sites uses. A subject the user told ScribeJay to
    leave out must never be handed to a fetcher — least of all a third-party
    one, where excluding it after the fact is too late."""
    monkeypatch.setattr(lc, "_EXCLUDED_DOMAINS", ["secret.org"])
    monkeypatch.setattr(lc, "_EXCLUDED_KEYWORDS", ["divorce"])
    sites = [
        _fetchable_site("secret.org", [("/blog/post", 1)]),
        _fetchable_site("news.com", [("/law/divorce-guide", 1)]),
        _fetchable_site("blog.com", [("/blog/post", 1)], title="Filing for divorce"),
        _fetchable_site("ok.com", [("/blog/post", 1)]),
    ]
    assert [c["domain"] for c in lc.candidate_urls(sites, 5)] == ["ok.com"]


def test_candidate_urls_excludes_a_subdomain_of_an_excluded_domain(monkeypatch):
    monkeypatch.setattr(lc, "_EXCLUDED_DOMAINS", ["sharepoint.com"])
    assert lc.candidate_urls(
        [_fetchable_site("tenant.sharepoint.com", [("/blog/post", 1)])], 5) == []


def test_candidate_urls_ignores_sites_with_no_pages():
    """pages is absent when the caller asked for one page per domain, and the
    path alone is not fetchable anyway."""
    assert lc.candidate_urls([{"domain": "a.com", "title": "T", "visits": 3}], 5) == []


# ---- the published-page gate ------------------------------------------------
#
# Five real days of browsing were measured before this gate existed. The picker
# spent 10 of its 25 picks on sign-in walls and most of the rest on receipts,
# and every one of those urls was handed to a third-party fetcher. The urls
# below are the shapes it actually chose, with the identifiers replaced.

@pytest.mark.parametrize("path", [
    "/MyChart-BILH/app/test-results/details",          # a patient portal
    "/n/list/folders=1&listFilter=PRIORITY/messages/169515",   # a mail message
    "/trips/v1/1694223945557022101/ro/RESERVATION2_CHECKIN/HMB2HP3HQX",
    "/trip_item/show/id/5525628661",                   # a travel itinerary
    "/api/v1/sso/init",                                # an SSO endpoint
    "/782e64d88e6c4f800fe0d3bd249455a7/workers/services/view/app/builds/0548b634",
    "/channel/UCd3jTOPxmukDdHfMMPE9B6w/editing/profile",
    "/en/reserve/view-modify-cancel/rental-search.html",
    "/event/51161505/owner/14227/summary",
    "/transaction/ticketing/mobile/jump.aspx",
])
def test_a_private_or_transactional_page_is_never_a_candidate(path):
    assert lc.candidate_urls([_fetchable_site("portal.example.com", [(path, 1)])], 5) == []


@pytest.mark.parametrize("path", [
    "/2026/08/26/googles-gemini-has-a-branding-problem",   # a headline slug
    "/entertainment/tv/articles/josh-radnor-says-no-longer-195909631.html",
    "/hc/en-us/articles/6310067436695-Set-a-default-view",
    "/blog/structured-outputs",          # two words, but under /blog/
    "/gemini-api/docs/models",           # one word, but under /docs/
])
def test_a_published_page_is_a_candidate(path):
    out = lc.candidate_urls([_fetchable_site("example.com", [(path, 1)])], 5)
    assert [p["path"] for p in out] == [path]


def test_a_docs_marker_does_not_excuse_an_identifier():
    """The marker relaxes the three-word rule, not the identifier rule — or
    "/help/9f3a8c21e4" would read as a help article."""
    assert lc.candidate_urls(
        [_fetchable_site("example.com", [("/help/9f3a8c21e4b7", 1)])], 5) == []


def test_the_query_string_is_dropped_before_the_url_leaves():
    """The query string is where the identifiers live — an order id, a
    reservation code, an SSO `state` token. An article renders the same
    without one, so it is never sent."""
    site = {"domain": "example.com", "title": "T", "visits": 1, "pages": [
        {"path": "/blog/structured-outputs",
         "url": "https://example.com/blog/structured-outputs?utm_source=x&token=SECRET#frag",
         "visits": 1}]}
    assert lc.candidate_urls([site], 5)[0]["url"] == \
        "https://example.com/blog/structured-outputs"
