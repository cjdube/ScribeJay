"""Tests for `scribejay doctor`.

The checks themselves are plain functions over plain data, so each one is
tested against the state it is supposed to catch rather than against a machine
that happens to be healthy. The two that matter most are the ones no other
command can answer: a task whose last run started and never finished, and a
Google token minted before a scope was added.

Nothing here reaches the network, the real Keychain, the real Chrome, or the
real launchd — `collect()` walks every section, so a test that called it
unstubbed would read the developer's own machine and pass or fail with it.
"""

import json
import sqlite3

import pytest

from scribejay.cli import doctor
from scribejay.core import config, features, registry


def _log(tmp_path, key, lines):
    path = tmp_path / f"{key}.log"
    path.write_text("\n".join(lines) + "\n")
    return path


# ---- last run ---------------------------------------------------------------

COMMITS = registry.get("daily_commits")


def test_a_task_with_no_log_has_never_run(tmp_path):
    check = doctor.last_run(COMMITS, tmp_path)
    assert check.status == doctor.WARN
    assert "never run" in check.detail


def test_a_clean_run_ends_on_its_completion_line(tmp_path):
    _log(tmp_path, "daily_commits", [
        "2026-08-30 04:55:01,000 [INFO] Starting daily commits run",
        "2026-08-30 04:55:09,000 [INFO] Daily commits run complete",
    ])
    check = doctor.last_run(COMMITS, tmp_path)
    assert check.status == doctor.OK
    assert "2026-08-30 04:55:09" in check.detail


def test_a_run_that_started_and_never_finished_fails(tmp_path):
    """The failure this command exists for. `schedule status` says the job is
    loaded, `status` says the source is on, and the page is still missing —
    because the last start never reached its boundary line."""
    _log(tmp_path, "daily_commits", [
        "2026-08-29 04:55:01,000 [INFO] Starting daily commits run",
        "2026-08-29 04:55:09,000 [INFO] Daily commits run complete",
        "2026-08-30 04:55:01,000 [INFO] Starting daily commits run",
        "2026-08-30 04:55:03,000 [ERROR] boom",
    ])
    check = doctor.last_run(COMMITS, tmp_path)
    assert check.status == doctor.FAIL
    assert "2026-08-29 04:55:09" in check.detail
    assert "2026-08-30 04:55:03" in check.detail


def test_a_log_with_no_completion_at_all_fails(tmp_path):
    _log(tmp_path, "daily_commits", [
        "2026-08-30 04:55:01,000 [INFO] Starting daily commits run",
    ])
    assert doctor.last_run(COMMITS, tmp_path).status == doctor.FAIL


def test_untimestamped_continuation_lines_are_ignored(tmp_path):
    """A traceback's body carries no stamp. Counting one as the newest line
    would report every task that ever logged an exception as unfinished."""
    _log(tmp_path, "daily_commits", [
        "2026-08-30 04:55:09,000 [INFO] Daily commits run complete",
        "  File \"x.py\", line 1, in <module>",
    ])
    assert doctor.last_run(COMMITS, tmp_path).status == doctor.OK


def test_the_colorizers_counted_completion_line_still_matches(tmp_path):
    """The registry carries "Colorizer run complete: 0 updated, 0 skipped" and
    a real run logs its own counts, so a substring match on the whole line
    would report every non-empty colorizer run as never finishing."""
    task = registry.get("calendar_colorizer")
    _log(tmp_path, "calendar_colorizer", [
        "2026-08-30 17:00:00,000 [INFO] Starting calendar colorizer run",
        "2026-08-30 17:00:04,000 [INFO] Colorizer run complete: 3 updated, 5 skipped",
    ])
    assert doctor.last_run(task, tmp_path).status == doctor.OK


# ---- folders ----------------------------------------------------------------

def test_a_missing_required_folder_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNINGS_DIR", str(tmp_path / "gone"))
    check = doctor._folder("LEARNINGS_DIR", "journal folder", required=True)
    assert check.status == doctor.FAIL
    assert "does not exist" in check.detail


def test_a_missing_optional_folder_only_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("CORRESPONDENCE_DIR", str(tmp_path / "gone"))
    check = doctor._folder("CORRESPONDENCE_DIR", "correspondence", required=False)
    assert check.status == doctor.WARN


def test_an_unwritable_folder_fails(tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    monkeypatch.setenv("LEARNINGS_DIR", str(locked))
    try:
        assert doctor._folder("LEARNINGS_DIR", "journal", required=True).status \
            == doctor.FAIL
    finally:
        locked.chmod(0o700)


def test_a_writable_folder_leaves_no_probe_file_behind(tmp_path, monkeypatch):
    monkeypatch.setenv("LEARNINGS_DIR", str(tmp_path))
    assert doctor._folder("LEARNINGS_DIR", "journal", required=True).status == doctor.OK
    assert list(tmp_path.iterdir()) == []


# ---- full disk access -------------------------------------------------------

def test_no_chrome_is_not_a_problem(monkeypatch, tmp_path):
    monkeypatch.setattr("scribejay.sources.chrome.HISTORY_PATH", tmp_path / "gone")
    assert doctor.full_disk_access().status == doctor.OFF


def test_an_unreadable_history_database_fails(monkeypatch, tmp_path):
    """What a revoked Full Disk Access looks like from inside the process: the
    file is there and the open fails."""
    fake = tmp_path / "History"
    fake.write_text("not a database")
    monkeypatch.setattr("scribejay.sources.chrome.HISTORY_PATH", fake)
    check = doctor.full_disk_access()
    assert check.status == doctor.FAIL
    assert "Full Disk Access" in check.detail


def test_a_readable_history_database_passes(monkeypatch, tmp_path):
    fake = tmp_path / "History"
    conn = sqlite3.connect(fake)
    conn.execute("CREATE TABLE urls (id INTEGER)")
    conn.commit()
    conn.close()
    monkeypatch.setattr("scribejay.sources.chrome.HISTORY_PATH", fake)
    assert doctor.full_disk_access().status == doctor.OK


# ---- model ------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _ollama(monkeypatch, models):
    import requests

    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: _Resp({"models": [{"name": n} for n in models]}))


def test_an_unreachable_ollama_fails(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", boom)
    check = doctor._ollama_check()
    assert check.status == doctor.FAIL
    assert "not answering" in check.detail


def test_a_reachable_ollama_missing_the_model_says_how_to_pull_it(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "gemma4:26b")
    _ollama(monkeypatch, ["llama3.1:8b"])
    check = doctor._ollama_check()
    assert check.status == doctor.FAIL
    assert "ollama pull gemma4:26b" in check.detail


def test_a_bare_model_name_matches_its_default_tag(monkeypatch):
    """Ollama reports "llama3.1:8b"; a user writes "llama3.1". Reporting that
    as missing would send them to pull a model they already have."""
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")
    _ollama(monkeypatch, ["llama3.1:8b"])
    assert doctor._ollama_check().status == doctor.OK


def test_a_cloud_backend_checks_its_key_instead_of_a_host(monkeypatch):
    monkeypatch.setenv("SCRIBEJAY_LLM_BACKEND", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    statuses = {c.label: c.status for c in doctor.model_checks()}
    assert statuses["model key"] == doctor.FAIL

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    statuses = {c.label: c.status for c in doctor.model_checks()}
    assert statuses["model key"] == doctor.OK


def test_an_unknown_backend_is_named(monkeypatch):
    monkeypatch.setenv("SCRIBEJAY_LLM_BACKEND", "gpt9")
    details = [c.detail for c in doctor.model_checks() if c.status == doctor.FAIL]
    assert any("gpt9" in d for d in details)


# ---- google -----------------------------------------------------------------

def _google_off(monkeypatch):
    for name in ("google_calendar", "gmail", "youtube"):
        monkeypatch.setenv(features.setting_key(name), "0")


def test_google_is_skipped_when_no_google_source_is_on(monkeypatch):
    _google_off(monkeypatch)
    checks = doctor.google_checks()
    assert [c.status for c in checks] == [doctor.OFF]


def test_a_missing_oauth_client_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(tmp_path / "gone.json"))
    checks = doctor.google_checks()
    assert checks[0].status == doctor.FAIL
    assert "setup-google" in checks[0].detail


def test_no_token_yet_warns_and_says_to_run_a_task_by_hand(monkeypatch, tmp_path):
    """A launchd job cannot open a consent window, so an install that has
    never consented is silently broken every morning until someone runs one
    task in a terminal."""
    (tmp_path / "client.json").write_text("{}")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(tmp_path / "client.json"))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "token.json"))
    statuses = {c.label: c for c in doctor.google_checks()}
    assert statuses["google consent"].status == doctor.WARN
    assert "by hand" in statuses["google consent"].detail


def _token(tmp_path, monkeypatch, scopes):
    (tmp_path / "client.json").write_text("{}")
    (tmp_path / "token.json").write_text(json.dumps({"scopes": scopes}))
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(tmp_path / "client.json"))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "token.json"))


def test_a_token_missing_a_scope_fails_and_names_it(monkeypatch, tmp_path):
    """The quiet one. A token minted before youtube.readonly was added keeps
    calendar and mail working, so nothing looks like an auth problem — there
    is just one empty page."""
    from scribejay.core.google import SCOPES

    _token(tmp_path, monkeypatch, [s for s in SCOPES if "youtube" not in s])
    statuses = {c.label: c for c in doctor.google_checks()}
    assert statuses["google scopes"].status == doctor.FAIL
    assert "youtube.readonly" in statuses["google scopes"].detail


def test_a_complete_token_passes(monkeypatch, tmp_path):
    from scribejay.core.google import SCOPES

    _token(tmp_path, monkeypatch, list(SCOPES))
    statuses = {c.label: c.status for c in doctor.google_checks()}
    assert statuses["google scopes"] == doctor.OK


def test_an_unreadable_token_fails(monkeypatch, tmp_path):
    (tmp_path / "client.json").write_text("{}")
    (tmp_path / "token.json").write_text("{ truncated")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(tmp_path / "client.json"))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "token.json"))
    statuses = {c.label: c.status for c in doctor.google_checks()}
    assert statuses["google consent"] == doctor.FAIL


# ---- sources ----------------------------------------------------------------

def test_a_declined_source_is_off_not_failed(monkeypatch):
    monkeypatch.setenv(features.setting_key("strava"), "0")
    strava = next(c for c in doctor.source_checks() if c.label == "strava")
    assert strava.status == doctor.OFF


def test_probing_never_sends_a_push(monkeypatch):
    """Testing `notify` means pushing to the user's phone. A diagnostic that
    buzzes you is one you stop running."""
    from scribejay.cli import settings_form

    probed = []
    monkeypatch.setattr(settings_form, "test_feature",
                        lambda name: probed.append(name) or "0 row(s) for yesterday")
    doctor.source_checks(probe=True)
    assert "notify" not in probed
    assert "chrome" in probed


def test_a_source_that_errors_fails(monkeypatch):
    from scribejay.cli import settings_form

    monkeypatch.setattr(settings_form, "test_feature", lambda name: "error: HTTP 401")
    assert all(c.status == doctor.FAIL for c in doctor.source_checks(probe=True)
               if c.label != "notify")


def test_a_source_that_raises_becomes_one_failed_line(monkeypatch):
    """A doctor that crashes on the fifth of nine sources is worse than
    useless: the answer is usually in the four it never reached."""
    from scribejay.cli import settings_form

    def boom(name):
        raise ValueError("unexpected")

    monkeypatch.setattr(settings_form, "test_feature", boom)
    checks = doctor.source_checks(probe=True)
    assert all(c.status in (doctor.FAIL, doctor.OK) for c in checks)
    assert any("ValueError" in c.detail for c in checks)


# ---- report -----------------------------------------------------------------

def _stub_everything(monkeypatch, tmp_path):
    """Every section that would touch a real machine, replaced."""
    from scribejay.cli import schedule, settings_form
    from scribejay.core.google import SCOPES

    fake = tmp_path / "History"
    conn = sqlite3.connect(fake)
    conn.execute("CREATE TABLE urls (id INTEGER)")
    conn.commit()
    conn.close()
    monkeypatch.setattr("scribejay.sources.chrome.HISTORY_PATH", fake)
    _ollama(monkeypatch, [config.getenv("OLLAMA_MODEL")])
    _token(tmp_path, monkeypatch, list(SCOPES))
    monkeypatch.setattr(settings_form, "test_feature", lambda name: "1 row(s) for yesterday")
    monkeypatch.setattr(schedule, "is_loaded", lambda label: True)


def test_collect_walks_every_section(monkeypatch, tmp_path):
    _stub_everything(monkeypatch, tmp_path)
    names = [name for name, _ in doctor.collect()]
    assert names == [name for name, _ in doctor.SECTIONS]


def test_a_section_that_raises_does_not_stop_the_rest(monkeypatch, tmp_path):
    _stub_everything(monkeypatch, tmp_path)

    def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(doctor, "SECTIONS",
                        (("BROKEN", lambda probe: boom()),
                         ("FOLDERS", lambda probe: doctor.folder_checks())))
    sections = dict(doctor.collect())
    assert sections["BROKEN"][0].status == doctor.FAIL
    assert "RuntimeError" in sections["BROKEN"][0].detail
    assert sections["FOLDERS"]


def test_main_exits_zero_when_nothing_failed(monkeypatch, tmp_path, capsys):
    _stub_everything(monkeypatch, tmp_path)
    assert doctor.main([]) == 0
    out = capsys.readouterr().out
    assert "0 failed" in out
    assert "FOLDERS" in out


def test_main_exits_one_on_a_failure(monkeypatch, tmp_path, capsys):
    _stub_everything(monkeypatch, tmp_path)
    monkeypatch.setenv("LEARNINGS_DIR", str(tmp_path / "gone"))
    assert doctor.main([]) == 1
    assert "1 failed" in capsys.readouterr().out


def test_a_warning_alone_does_not_fail_the_command(monkeypatch, tmp_path, capsys):
    """Phase 3's rule, applied to the exit code: a user who declined a source,
    or has simply not installed the schedule yet, must not get a non-zero exit
    for it. Nothing is installed under the tmp LaunchAgents here, so every job
    warns and the command still succeeds."""
    _stub_everything(monkeypatch, tmp_path)
    assert doctor.main([]) == 0
    out = capsys.readouterr().out
    assert "0 failed" in out
    assert "schedule install" in out


# ---- wiring -----------------------------------------------------------------

def test_the_cli_dispatches_to_doctor_and_forwards_probe(monkeypatch):
    from scribejay import cli

    seen = []
    monkeypatch.setattr(doctor, "main", lambda argv: (seen.append(argv), 0)[1])
    assert cli.main(["doctor"]) == 0
    assert cli.main(["doctor", "--probe"]) == 0
    assert seen == [[], ["--probe"]]
