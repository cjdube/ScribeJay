"""Tests for scribejay/core/google.py's service construction — transport
wiring only. The OAuth flow and live API calls stay untested, mirroring
LocalLLMAgent's tests/test_google_auth.py precedent; what's asserted here is
the one property a hung googleapis.com connection would otherwise exploit:
every built service rides an httplib2 transport with a timeout.

Dropped the reset_service tests — that function has no counterpart in
scribejay/core/google.py (no reconnect-after-broken-pipe path exists here)."""

import pytest

from scribejay.core import google as ga


@pytest.fixture(autouse=True)
def _fresh_service_cache():
    ga._SERVICES.clear()
    yield
    ga._SERVICES.clear()


def test_build_service_uses_timeout_bearing_transport(monkeypatch):
    monkeypatch.setattr(ga, "get_credentials", lambda: object())
    captured = {}

    def fake_build(api, version, http=None):
        captured["http"] = http
        return f"service-{api}-{version}"

    monkeypatch.setattr(ga, "build", fake_build)

    service = ga.build_service("calendar", "v3")
    assert service == "service-calendar-v3"
    # An AuthorizedHttp wrapping an Http whose timeout is set — not build()'s
    # own default transport, which has none.
    assert captured["http"].http.timeout == ga.GOOGLE_HTTP_TIMEOUT_S


def test_build_service_caches_per_api_and_version(monkeypatch):
    monkeypatch.setattr(ga, "get_credentials", lambda: object())
    builds = []

    def fake_build(api, version, http=None):
        builds.append((api, version))
        return object()

    monkeypatch.setattr(ga, "build", fake_build)

    first = ga.build_service("gmail", "v1")
    assert ga.build_service("gmail", "v1") is first
    assert ga.build_service("tasks", "v1") is not first
    assert builds == [("gmail", "v1"), ("tasks", "v1")]


# ---- scopes -----------------------------------------------------------------

AUTH = "https://www.googleapis.com/auth/"


@pytest.fixture
def only(monkeypatch):
    """Switch exactly the named Google sources on, and no fallback email."""
    def set_on(*names):
        for name in ("google_calendar", "gmail", "youtube"):
            monkeypatch.setenv(f"SCRIBEJAY_FEATURE_{name.upper()}",
                               "1" if name in names else "0")
        monkeypatch.setenv("BRIEF_TO_EMAIL", "")
    return set_on


def test_youtube_alone_asks_for_youtube_alone(only):
    """The beta tester's case: one source on must not cost a mail grant."""
    only("youtube")
    assert ga.wanted_scopes() == [AUTH + "youtube.readonly"]


def test_calendar_asks_for_events_not_the_whole_calendar(only):
    only("google_calendar")
    assert ga.wanted_scopes() == [AUTH + "calendar.events"]


def test_send_is_asked_for_only_when_a_fallback_address_is_set(only, monkeypatch):
    only("youtube")
    assert AUTH + "gmail.send" not in ga.wanted_scopes()
    monkeypatch.setenv("BRIEF_TO_EMAIL", "me@example.com")
    assert AUTH + "gmail.send" in ga.wanted_scopes()


def test_a_token_from_before_the_narrowing_still_covers_calendar(only):
    """Every existing install holds the full calendar scope. It covers the
    narrow one, so nothing is missing and nothing breaks — but it is wider than
    needed, and that is reported."""
    only("google_calendar")
    granted = [AUTH + "calendar"]
    assert ga.missing_scopes(granted) == []
    assert ga.extra_scopes(granted) == [AUTH + "calendar"]


def test_missing_and_extra_name_the_difference(only):
    only("youtube", "gmail")
    granted = [AUTH + "youtube.readonly", AUTH + "calendar"]
    assert ga.missing_scopes(granted) == [AUTH + "gmail.readonly"]
    assert ga.extra_scopes(granted) == [AUTH + "calendar"]


# ---- consent ----------------------------------------------------------------

class _Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


@pytest.fixture
def token(tmp_path, monkeypatch):
    path = tmp_path / "token.json"
    path.write_text('{"refresh_token": "r-old", "token": "a-old", "client_id": "c", '
                    '"client_secret": "s", "scopes": ["x"]}')
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(path))
    client = tmp_path / "credentials.json"
    client.write_text("{}")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(client))
    return path


def _consent(monkeypatch, response, token):
    calls = []

    def fake_post(url, params=None, timeout=None):
        calls.append({"url": url, "params": params, "timeout": timeout,
                      "token_still_on_disk": token.exists()})
        return response

    monkeypatch.setattr(ga.requests, "post", fake_post)
    granted = type("C", (), {"scopes": ["y"]})()
    monkeypatch.setattr(ga, "get_credentials", lambda: granted)
    return calls


def test_consent_revokes_the_old_grant_before_asking_again(monkeypatch, token):
    """Deleting the file alone would leave the old, broader grant live at
    Google with nothing on disk to show for it."""
    calls = _consent(monkeypatch, _Resp(200), token)
    assert ga.consent() == ["y"]
    assert calls == [{"url": ga.REVOKE_URL, "params": {"token": "r-old"},
                      "timeout": ga.GOOGLE_HTTP_TIMEOUT_S,
                      "token_still_on_disk": True}]
    assert not token.exists()


def test_a_refused_revoke_leaves_the_token_in_place(monkeypatch, token):
    _consent(monkeypatch, _Resp(503, "backend error"), token)
    with pytest.raises(RuntimeError, match="unchanged"):
        ga.consent()
    assert token.exists()


def test_an_already_dead_token_does_not_block_consent(monkeypatch, token):
    _consent(monkeypatch, _Resp(400, '{"error": "invalid_token"}'), token)
    assert ga.consent() == ["y"]
    assert not token.exists()


def test_a_missing_client_file_stops_consent_before_the_revoke(monkeypatch, token):
    """Without the client file the browser step cannot run, so revoking first
    would leave no token and no way to get one."""
    calls = _consent(monkeypatch, _Resp(200), token)
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(token.parent / "absent.json"))
    with pytest.raises(FileNotFoundError, match="absent.json"):
        ga.consent()
    assert calls == []
    assert token.exists()
