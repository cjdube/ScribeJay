"""Tests for scribejay/sources/clickup.py — closed_tasks, the day's record of
work that leaves no commit behind, plus the _ms_to_local_date helper it
depends on.

Mirrors the closed_tasks + _ms_to_local_date slice of LocalLLMAgent's
tests/test_clickup.py. Everything else in that file (the chat tools:
list/read/add/move/comment, the digest, the tag watcher, slug/resolve_space/
resolve_status/_days_since) has no counterpart in scribejay/sources/clickup.py
(see its module docstring) and is dropped. Also dropped the "not offered to
the model" test — ScribeJay has no tool registry at all, so that guarantee
holds trivially and needs no test."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from scribejay.sources import clickup

DAY = date(2026, 8, 26)


# --------------------------------------------------------------------------- #
# _ms_to_local_date — ClickUp stamps are UTC millis; the day reported is
# local. A naive slice of an ISO string would get this wrong across a
# timezone boundary.
# --------------------------------------------------------------------------- #

def test_ms_to_local_date_converts_utc_to_the_local_day(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "America/New_York")
    # 2026-08-27T01:30:00Z is still the 26th in New York.
    ms = 1787794200000
    assert clickup._ms_to_local_date(ms) == "2026-08-26"
    assert clickup._ms_to_local_date(str(ms)) == "2026-08-26"


def test_ms_to_local_date_same_instant_differs_by_zone(monkeypatch):
    ms = 1787794200000
    monkeypatch.setenv("TIMEZONE", "UTC")
    assert clickup._ms_to_local_date(ms) == "2026-08-27"
    monkeypatch.setenv("TIMEZONE", "America/New_York")
    assert clickup._ms_to_local_date(ms) == "2026-08-26"


def test_ms_to_local_date_tolerates_missing_and_junk():
    assert clickup._ms_to_local_date(None) is None
    assert clickup._ms_to_local_date("") is None
    assert clickup._ms_to_local_date("not-a-number") is None


# --------------------------------------------------------------------------- #
# closed_tasks
# --------------------------------------------------------------------------- #

def _closed_task(name="Proposal for Acme", space_id="s1", status="complete",
                 status_type="closed", closed="2026-08-26T14:00:00-04:00",
                 updated=None):
    def _ms(iso):
        return None if iso is None else int(
            datetime.fromisoformat(iso).timestamp() * 1000)
    return {"name": name, "space": {"id": space_id},
            "list": {"name": "Backlog"},
            "status": {"status": status, "type": status_type},
            "date_closed": _ms(closed),
            "date_updated": _ms(updated or closed)}


def _stub_clickup(monkeypatch, tasks, spaces=(("s1", "Vibe Foundry"),)):
    """Stub the two helpers closed_tasks reaches through, and hand back the
    params the task fetch was called with so a test can assert on them."""
    seen = {}
    monkeypatch.setattr(clickup, "_client", lambda k: ("pk_x", None))
    monkeypatch.setattr(clickup, "_team_id", lambda t: "team1")
    monkeypatch.setattr(clickup, "_spaces",
                        lambda t, tid: [{"id": i, "name": n} for i, n in spaces])

    def _fetch(token, team_id, space_ids, include_done, updated_after_ms=None,
               logger=None, **k):
        seen.update(include_done=include_done, updated_after_ms=updated_after_ms,
                    space_ids=space_ids, logger=logger)
        return tasks
    monkeypatch.setattr(clickup, "_fetch_tasks", _fetch)
    return seen


def test_a_task_closed_that_day_is_returned_with_its_space_and_status(monkeypatch):
    _stub_clickup(monkeypatch, [_closed_task()])
    items = clickup.closed_tasks(DAY)["items"]
    assert items == [{"title": "Proposal for Acme", "space": "Vibe Foundry",
                      "status": "complete"}]


def test_a_task_closed_on_another_day_is_left_out(monkeypatch):
    _stub_clickup(monkeypatch, [_closed_task(closed="2026-08-25T14:00:00-04:00")])
    assert clickup.closed_tasks(DAY)["items"] == []


def test_an_old_task_merely_edited_that_day_is_not_reported_as_closed(monkeypatch):
    """The whole reason this filters on date_closed, never date_updated.
    Editing a Task months after shipping it bumps date_updated, which would
    invent work done today that wasn't."""
    _stub_clickup(monkeypatch, [_closed_task(closed="2026-06-01T09:00:00-04:00",
                                             updated="2026-08-26T14:00:00-04:00")])
    assert clickup.closed_tasks(DAY)["items"] == []


def test_an_open_task_updated_that_day_is_left_out(monkeypatch):
    """The fetch is deliberately wide — it asks for everything touched since the
    day began — so the status-group filter is what makes the answer right."""
    _stub_clickup(monkeypatch, [_closed_task(status="building", status_type="custom")])
    assert clickup.closed_tasks(DAY)["items"] == []


def test_the_fetch_includes_done_and_starts_at_the_local_day(monkeypatch):
    """include_done because ClickUp excludes its Closed group by default, which
    is the only group this asks about. And date_updated_gt at the start of the
    LOCAL day, never a slice of a UTC stamp (AGENTS.md's timezone rule)."""
    seen = _stub_clickup(monkeypatch, [])
    clickup.closed_tasks(DAY)
    assert seen["include_done"] is True
    started = datetime.fromtimestamp(seen["updated_after_ms"] / 1000,
                                     ZoneInfo(clickup.local_timezone()))
    assert started.date() == DAY
    assert (started.hour, started.minute) == (0, 0)


def test_a_failure_degrades_to_an_error_dict(monkeypatch):
    """One dead source must never kill the day's entry."""
    _stub_clickup(monkeypatch, [])
    monkeypatch.setattr(clickup, "_team_id",
                        lambda t: (_ for _ in ()).throw(clickup._ClickUpError("no workspace")))
    assert "error" in clickup.closed_tasks(DAY)


def test_no_spaces_is_an_empty_day_not_an_error(monkeypatch):
    _stub_clickup(monkeypatch, [], spaces=())
    assert clickup.closed_tasks(DAY) == {"items": []}


# --------------------------------------------------------------------------- #
# _fetch_tasks — the page cap is a degrade, and a degrade has to be logged.
# A task that produces *less* pushes no alert, while a failing one does, so a
# silent truncation reads exactly like a quiet day (AGENTS.md).
# --------------------------------------------------------------------------- #

class _Recorder:
    """The one logger method this path uses. A real logger would need the
    suite's log guards; this needs nothing and shows what was said."""

    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


def test_fetch_tasks_warns_when_the_page_cap_stops_the_walk(monkeypatch):
    # Every page comes back full and never says last_page, so only the cap can
    # end the loop — the shape of a workspace bigger than the walk allows.
    monkeypatch.setattr(clickup, "_get",
                        lambda path, token, **k: {"tasks": [{"id": "t"}] * clickup._PAGE_SIZE})
    logger = _Recorder()

    out = clickup._fetch_tasks("tok", "team1", ["s1"], include_done=True, logger=logger)

    assert len(out) == clickup._MAX_PAGES * clickup._PAGE_SIZE
    assert len(logger.warnings) == 1
    assert "cap" in logger.warnings[0]


def test_fetch_tasks_says_nothing_when_the_api_ends_the_walk(monkeypatch):
    """The other half: a warning on every ordinary run is a warning nobody
    reads, which would cost the one that matters."""
    monkeypatch.setattr(clickup, "_get",
                        lambda path, token, **k: {"tasks": [{"id": "t"}], "last_page": True})
    logger = _Recorder()

    clickup._fetch_tasks("tok", "team1", ["s1"], include_done=True, logger=logger)

    assert logger.warnings == []


def test_closed_tasks_hands_its_logger_to_the_pager(monkeypatch):
    """The warning above is only reachable if the logger travels the whole way
    down. closed_tasks took none at all before, so the cap could never speak."""
    seen = _stub_clickup(monkeypatch, [])
    logger = _Recorder()

    clickup.closed_tasks(DAY, logger=logger)

    assert seen["logger"] is logger
