"""Tests for scribejay/core/features.py — what the user switched on.

The behaviour worth pinning hardest is the fallback: an unanswered toggle asks
the machine. That single rule is what lets a fresh install be quiet and a
running install be unchanged, and both halves of it are easy to break without
any test noticing, because in the suite everything is answered explicitly.
"""

import pytest

from scribejay.core import features


def _unanswer(monkeypatch, name):
    """Undo conftest's blanket 'everything on' for one feature."""
    monkeypatch.delenv(features.setting_key(name), raising=False)


# ---- the fallback -----------------------------------------------------------

def test_an_unanswered_toggle_asks_the_machine(monkeypatch):
    _unanswer(monkeypatch, "strava")
    monkeypatch.setattr(features, "configured", lambda n: True)
    assert features.enabled("strava") is True

    monkeypatch.setattr(features, "configured", lambda n: False)
    assert features.enabled("strava") is False


def test_an_unconfigured_feature_explains_itself(monkeypatch):
    # The reason lands in the skip line of a task that did nothing, so it has
    # to be readable by someone asking why their morning was empty.
    _unanswer(monkeypatch, "strava")
    monkeypatch.setattr(features, "configured", lambda n: False)
    ok, reason = features.state("strava")
    assert ok is False
    assert "Strava" in reason and "settings" in reason


def test_an_explicit_no_beats_being_configured(monkeypatch):
    monkeypatch.setenv(features.setting_key("strava"), "0")
    monkeypatch.setattr(features, "configured", lambda n: True)
    ok, reason = features.state("strava")
    assert ok is False
    assert "turned off in settings" in reason


def test_an_explicit_yes_beats_the_probe(monkeypatch):
    # The user may know something the probe does not. Overruling them would
    # turn a real error into a tidy skip and hide the actual cause.
    monkeypatch.setenv(features.setting_key("strava"), "1")
    monkeypatch.setattr(features, "configured", lambda n: False)
    assert features.enabled("strava") is True


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " On "])
def test_truthy_spellings(monkeypatch, raw):
    monkeypatch.setenv(features.setting_key("strava"), raw)
    monkeypatch.setattr(features, "configured", lambda n: False)
    assert features.enabled("strava") is True


@pytest.mark.parametrize("raw", ["0", "false", "No", "off"])
def test_falsey_spellings(monkeypatch, raw):
    monkeypatch.setenv(features.setting_key("strava"), raw)
    monkeypatch.setattr(features, "configured", lambda n: True)
    assert features.enabled("strava") is False


def test_an_unrecognised_answer_falls_back_rather_than_meaning_no(monkeypatch):
    # "maybe" is a typo, not a decision. Reading it as False would silently
    # switch off a source the user believed they had turned on.
    monkeypatch.setenv(features.setting_key("strava"), "maybe")
    monkeypatch.setattr(features, "configured", lambda n: True)
    assert features.enabled("strava") is True


# ---- the probes -------------------------------------------------------------

def test_git_follows_the_projects_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECTS_DIR", str(tmp_path / "nope"))
    assert features.configured("git") is False
    monkeypatch.setenv("PROJECTS_DIR", str(tmp_path))
    assert features.configured("git") is True


def test_google_follows_the_oauth_client_not_the_token(monkeypatch, tmp_path):
    # Gating on the token would be a deadlock: the token only exists after the
    # first browser consent, and the consent only happens inside a run.
    client = tmp_path / "client.json"
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(client))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "token.json"))
    assert features.configured("google_calendar") is False
    client.write_text("{}")
    assert features.configured("google_calendar") is True


def test_the_three_google_features_share_one_probe(monkeypatch, tmp_path):
    client = tmp_path / "client.json"
    client.write_text("{}")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(client))
    assert all(features.configured(n)
               for n in ("google_calendar", "gmail", "youtube"))


def test_but_they_are_declined_separately(monkeypatch, tmp_path):
    # One OAuth client, three answers: wanting the YouTube journal is not
    # consenting to ScribeJay recolouring a work calendar.
    monkeypatch.setenv(features.setting_key("google_calendar"), "0")
    assert features.enabled("google_calendar") is False
    assert features.enabled("youtube") is True


def test_strava_needs_all_three_credentials(monkeypatch):
    have = {"STRAVA_CLIENT_ID": "x", "STRAVA_CLIENT_SECRET": "y"}
    monkeypatch.setattr(features, "_credentials_present",
                        lambda *keys: all(have.get(k) for k in keys))
    assert features.configured("strava") is False
    have["STRAVA_REFRESH_TOKEN"] = "z"
    assert features.configured("strava") is True


def test_notify_follows_the_topic_url(monkeypatch):
    monkeypatch.delenv("NTFY_URL", raising=False)
    assert features.configured("notify") is False
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example/topic")
    assert features.configured("notify") is True


def test_the_probe_is_read_fresh_every_call(monkeypatch, tmp_path):
    # A user who pastes a token into the settings screen expects the next run
    # to see it. A cached "no" would keep the task silent until a restart.
    monkeypatch.setenv("PROJECTS_DIR", str(tmp_path / "later"))
    assert features.configured("git") is False
    (tmp_path / "later").mkdir()
    assert features.configured("git") is True


# ---- the table ---------------------------------------------------------------

def test_every_feature_has_a_working_probe():
    # A feature added to the table without a probe branch raises KeyError at
    # 4:30 AM inside a task's guard, which is the worst place to find out.
    for name in features.NAMES:
        assert isinstance(features.configured(name), bool)


def test_every_feature_has_a_schema_row():
    from scribejay.core import schema

    for name in features.NAMES:
        assert schema.get(features.setting_key(name)) is not None, name


def test_a_toggle_row_carries_no_default():
    # A default would collapse "the user has not said" into "no", which is
    # exactly the distinction the fallback depends on.
    from scribejay.core import schema

    for name in features.NAMES:
        assert schema.default_for(features.setting_key(name)) is None, name


def test_every_settings_group_a_feature_names_exists():
    from scribejay.core import schema

    for f in features.FEATURES:
        assert f.settings_group in schema.FEATURES, f.name


def test_an_unknown_feature_raises():
    with pytest.raises(KeyError):
        features.state("myspace")
    with pytest.raises(KeyError):
        features.configured("myspace")


def test_switching_notify_off_actually_stops_a_push(monkeypatch):
    """The toggle has to reach the wire, not just the table.

    core/notify.py already declined to push with no NTFY_URL, so a toggle that
    only recorded an answer would look correct in every table test while a
    configured user who switched alerts off kept getting them.
    """
    from scribejay.core import notify as notify_mod

    def _blew_it(*a, **k):
        raise AssertionError("pushed while notify was switched off")

    monkeypatch.setattr(notify_mod.requests, "post", _blew_it)
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example/topic")
    monkeypatch.setenv(features.setting_key("notify"), "0")
    assert "switched off" in notify_mod.notify("probe")["error"]
