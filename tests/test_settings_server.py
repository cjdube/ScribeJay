"""Tests for `scribejay settings` — the form and the server in front of it.

The security rules are the reason this file is long. A loopback port is not
automatically safe: any page the user has open can POST to 127.0.0.1, and a
hostile DNS name pointed at 127.0.0.1 makes that page's own origin *be* this
server. So the four guards — bind, token, Host, Origin — are tested as rules,
against `check_request`, rather than through a socket. No test in this file
opens one, and none starts a thread.

The other half is the promise the form makes about credentials: a secret is
write-only. It renders as `set` or `not set`, a blank field means "leave it
alone", and no value ever reaches the page or the server's own output.
"""

import pytest

from scribejay.cli import settings_form, settings_server
from scribejay.core import config, schema, secrets

SENTINEL = "sk-do-not-print-me-0123456789"


@pytest.fixture
def session():
    s = settings_server.Session(idle_timeout=900)
    s.expected_host = "127.0.0.1:54321"
    return s


def headers(**kwargs) -> dict:
    base = {"Host": "127.0.0.1:54321"}
    base.update({k.replace("_", "-").title(): v for k, v in kwargs.items()})
    return base


# ---- the four guards ---------------------------------------------------------

def test_get_without_a_token_is_401(session):
    status, reason = settings_server.check_request("GET", headers(), {}, {}, session)
    assert status == 401
    assert "token" in reason


def test_get_with_the_token_in_the_url_is_allowed(session):
    status, _ = settings_server.check_request(
        "GET", headers(), {"t": session.token}, {}, session)
    assert status == 200


def test_get_with_the_token_in_a_cookie_is_allowed(session):
    status, _ = settings_server.check_request(
        "GET", headers(cookie=f"scribejay={session.token}"), {}, {}, session)
    assert status == 200


def test_a_wrong_token_is_401(session):
    status, _ = settings_server.check_request(
        "GET", headers(), {"t": "not-the-token"}, {}, session)
    assert status == 401


def test_a_foreign_host_header_is_refused_before_the_token_is_read(session):
    """DNS rebinding: a hostile name resolved to 127.0.0.1 arrives with its own
    hostname in Host. It is refused here — with a valid token in hand — because
    the Host check runs first and does not care what else the request carries."""
    status, reason = settings_server.check_request(
        "GET", {"Host": "evil.example.com:54321"}, {"t": session.token}, {}, session)
    assert status == 403
    assert "Host" in reason


def test_a_foreign_host_is_refused_on_post_too(session):
    status, _ = settings_server.check_request(
        "POST", {"Host": "evil.example.com:54321"}, {}, {"csrf": session.token}, session)
    assert status == 403


def test_post_from_another_origin_is_refused(session):
    status, reason = settings_server.check_request(
        "POST", headers(origin="http://evil.example.com"), {},
        {"csrf": session.token}, session)
    assert status == 403
    assert "Origin" in reason


def test_post_without_the_csrf_token_is_refused(session):
    status, reason = settings_server.check_request(
        "POST", headers(origin="http://127.0.0.1:54321"), {}, {}, session)
    assert status == 403
    assert "CSRF" in reason


def test_post_with_the_right_origin_and_token_is_allowed(session):
    status, _ = settings_server.check_request(
        "POST", headers(origin="http://127.0.0.1:54321"), {},
        {"csrf": session.token}, session)
    assert status == 200


def test_a_post_cookie_is_not_enough_on_its_own(session):
    """SameSite=Strict is the browser's promise, not ours. The form token is
    the check that does not depend on the browser keeping it."""
    status, _ = settings_server.check_request(
        "POST", headers(cookie=f"scribejay={session.token}"), {}, {}, session)
    assert status == 403


def test_the_token_is_long_enough_to_be_unguessable(session):
    assert len(session.token) >= 32


def test_the_server_does_not_log_request_lines():
    """The default BaseHTTPRequestHandler access log prints the request line,
    and a GET carries the token in its query string — straight into whatever
    terminal the user launched from."""
    handler = settings_server.make_handler(settings_server.Session(1))
    assert handler.log_message is not settings_server.http.server \
        .BaseHTTPRequestHandler.log_message


# ---- idle timeout -------------------------------------------------------------

def test_the_session_expires_and_a_touch_resets_it(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(settings_server.time, "monotonic", lambda: clock["now"])

    s = settings_server.Session(idle_timeout=60)
    clock["now"] = 1059.0
    assert not s.expired

    s.touch()
    clock["now"] = 1100.0
    assert not s.expired

    clock["now"] = 1200.0
    assert s.expired


# ---- secrets are write-only ----------------------------------------------------

def test_no_secret_value_reaches_the_rendered_page(monkeypatch):
    monkeypatch.setattr(secrets, "get", lambda name: SENTINEL)
    page = settings_form.render("token-123")

    assert SENTINEL not in page
    # And it does say the credential exists, so a user can tell.
    assert "set" in page


def test_a_secret_renders_as_a_state_not_a_value(monkeypatch):
    monkeypatch.setattr(secrets, "get", lambda name: SENTINEL)
    field = settings_form._field(schema.get("STRAVA_CLIENT_SECRET"))

    assert SENTINEL not in field
    assert 'type="password"' in field
    assert "leave blank to keep it" in field


def test_a_blank_secret_field_leaves_the_stored_one_alone(monkeypatch):
    written = []
    monkeypatch.setattr(secrets, "set", lambda k, v: written.append(k) or True)

    settings_form.apply({"STRAVA_CLIENT_SECRET": "", "TIMEZONE": "America/New_York"})

    assert written == []


def test_a_filled_secret_field_goes_to_the_keychain(monkeypatch):
    written = {}
    monkeypatch.setattr(secrets, "set", lambda k, v: written.setdefault(k, v) or True)

    saved, errors = settings_form.apply({"STRAVA_CLIENT_SECRET": SENTINEL})

    assert written == {"STRAVA_CLIENT_SECRET": SENTINEL}
    assert not errors
    assert not any(SENTINEL in line for line in saved)


def test_a_secret_never_reaches_the_settings_file(monkeypatch):
    monkeypatch.setattr(secrets, "set", lambda k, v: True)
    settings_form.apply({"STRAVA_CLIENT_SECRET": SENTINEL})

    assert SENTINEL not in config.config_path().read_text()


def test_a_keychain_refusal_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(secrets, "set", lambda k, v: False)
    _, errors = settings_form.apply({"STRAVA_CLIENT_SECRET": SENTINEL})
    assert errors and "Keychain" in errors[0]


# ---- validation ----------------------------------------------------------------

def test_a_bad_number_is_refused(tmp_path):
    _, errors = settings_form.apply({"OLLAMA_NUM_CTX": "eight thousand"})
    assert errors and "not a number" in errors[0]


def test_a_bad_choice_is_refused():
    _, errors = settings_form.apply({"SCRIBEJAY_LLM_BACKEND": "gpt5"})
    assert errors and "not one of" in errors[0]


def test_a_bad_timezone_is_refused():
    _, errors = settings_form.apply({"TIMEZONE": "Mars/Olympus_Mons"})
    assert errors and "IANA" in errors[0]


def test_a_folder_that_does_not_exist_is_refused(tmp_path):
    _, errors = settings_form.apply({"LEARNINGS_DIR": str(tmp_path / "nope")})
    assert errors and "not a folder that exists" in errors[0]


def test_a_folder_that_exists_is_accepted(tmp_path):
    target = tmp_path / "journal"
    target.mkdir()
    saved, errors = settings_form.apply({"LEARNINGS_DIR": str(target)})
    assert not errors
    # Read the document, not config.getenv: the suite pins LEARNINGS_DIR in the
    # environment (tests/conftest.py keeps every task off the real vault), and
    # the environment is the top resolution layer.
    assert config.CONFIG["output"]["learnings_dir"] == str(target)


def test_a_symlink_is_stored_resolved(tmp_path):
    """A path setting decides where an unattended job writes. An indirection
    the user cannot see in the form has no business surviving into the file."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    settings_form.apply({"LEARNINGS_DIR": str(link)})

    assert config.CONFIG["output"]["learnings_dir"] == str(real.resolve())


def test_a_symlink_into_a_forbidden_directory_is_refused(tmp_path, monkeypatch):
    forbidden = tmp_path / "LaunchAgents"
    forbidden.mkdir()
    monkeypatch.setattr(settings_form, "FORBIDDEN_ROOTS", (forbidden,))

    link = tmp_path / "journal"
    link.symlink_to(forbidden)

    _, errors = settings_form.apply({"LEARNINGS_DIR": str(link)})
    assert errors and "will not write to" in errors[0]


def test_an_empty_value_clears_back_to_the_default():
    settings_form.apply({"OLLAMA_MODEL": "llama9"})
    assert config.getenv("OLLAMA_MODEL") == "llama9"

    settings_form.apply({"OLLAMA_MODEL": ""})
    assert config.getenv("OLLAMA_MODEL") == schema.default_for("OLLAMA_MODEL")


def test_nothing_is_written_when_any_field_is_invalid():
    """Half-applying is the worst outcome available: the user reads an error,
    assumes nothing happened, and a 4:30 job runs against a setting they
    thought they had abandoned."""
    settings_form.apply({"OLLAMA_MODEL": "keepme"})

    saved, errors = settings_form.apply(
        {"OLLAMA_MODEL": "changed", "OLLAMA_NUM_CTX": "not a number"})

    assert errors and not saved
    assert config.getenv("OLLAMA_MODEL") == "keepme"


# ---- the form is built from the tables ------------------------------------------

def test_every_non_secret_setting_gets_a_field():
    page = settings_form.render("t")
    for setting in schema.SETTINGS:
        assert f'name="{setting.key}"' in page, f"{setting.key} has no form field"


def test_groups_cover_every_schema_feature():
    covered = {g.name for g in settings_form.groups()}
    assert covered == set(schema.FEATURES)


def test_the_google_group_carries_all_three_features_it_governs():
    """One OAuth client, three things a user may accept or decline separately —
    somebody may want the YouTube journal without ScribeJay recolouring their
    work calendar."""
    google = next(g for g in settings_form.groups() if g.name == "google")
    assert {f.name for f in google.features} == {"google_calendar", "gmail", "youtube"}


def test_a_feature_test_button_exists_for_every_feature():
    page = settings_form.render("t")
    from scribejay.core import features
    for name in features.NAMES:
        assert f'data-feature="{name}"' in page


def test_test_feature_reports_a_source_error_rather_than_raising(monkeypatch):
    from scribejay.sources import strava
    monkeypatch.setattr(strava, "fetch_strava",
                        lambda *a, **k: {"error": "no refresh token"})
    assert "no refresh token" in settings_form.test_feature("strava")


def test_test_feature_counts_rows(monkeypatch):
    from scribejay.sources import strava
    monkeypatch.setattr(strava, "fetch_strava",
                        lambda *a, **k: {"activities": [1, 2, 3]})
    assert "3 row(s)" in settings_form.test_feature("strava")
