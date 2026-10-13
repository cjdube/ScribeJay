"""Tests for scribejay/daily_youtube_learnings.py — main() drafts a synthesis, appends
the deterministic video list, and persists a Daily-YouTube entry; a day with no
Liked videos writes nothing. Collaborators are monkeypatched; no model, YouTube,
vault, or Gmail access."""

import sys

import pytest

from _helpers import is_run_success
from scribejay import daily_youtube_learnings as yt


@pytest.fixture
def stubbed_run(monkeypatch):
    seen = {"persists": []}
    monkeypatch.setattr(sys, "argv", ["daily_youtube_learnings"])  # argparse must not see pytest's argv
    monkeypatch.setattr(yt, "scribejay_backend", lambda key: None)
    monkeypatch.setattr(yt, "warm_model", lambda **k: True)
    monkeypatch.setattr(yt, "complete_text",
                        lambda **k: "## YouTube Learnings: July 12, 2026\n\n### Themes Explored\n- **Git:** internals")
    monkeypatch.setattr(yt, "persist_or_email",
                        lambda content, prefix, day, subject, task_name, logger:
                        seen["persists"].append((prefix, content)) or {"written": True})
    monkeypatch.setattr(yt, "notify_failure", lambda *a, **k: None)
    return seen


def test_happy_path_persists_synthesis_and_video_list(stubbed_run, monkeypatch):
    monkeypatch.setattr(yt, "fetch_liked_videos", lambda *a, **k: {"videos": [
        {"title": "Git Deep Dive", "channel": "LearnThatStack",
         "url": "https://www.youtube.com/watch?v=abc", "description": "how git works"},
    ]})
    assert yt.main() == 0
    assert len(stubbed_run["persists"]) == 1
    prefix, content = stubbed_run["persists"][0]
    assert prefix == "Daily-YouTube"
    # model synthesis + the deterministic linked list are both present
    assert "### Themes Explored" in content
    assert "### Videos Liked" in content
    assert "https://www.youtube.com/watch?v=abc" in content


def test_unusable_synthesis_degrades_to_list(stubbed_run, monkeypatch):
    # a degenerate/empty model reply must not write a broken file — fall back to a
    # plain header + the deterministic video list.
    monkeypatch.setattr(yt, "complete_text", lambda **k: "   \n\n")
    monkeypatch.setattr(yt, "fetch_liked_videos", lambda *a, **k: {"videos": [
        {"title": "Git Deep Dive", "channel": "LTS", "url": "https://youtu.be/abc"},
    ]})
    assert yt.main() == 0
    _, content = stubbed_run["persists"][0]
    assert content.startswith("## YouTube Learnings:")
    assert "### Themes Explored" not in content  # synthesis dropped
    assert "### Videos Liked" in content         # list still written


def test_no_videos_writes_nothing(stubbed_run, monkeypatch):
    monkeypatch.setattr(yt, "fetch_liked_videos", lambda *a, **k: {"videos": []})
    assert yt.main() == 0
    assert stubbed_run["persists"] == []  # skipped the empty day


def test_no_videos_still_logs_a_run_complete_boundary(stubbed_run, monkeypatch, capsys):
    # A run that logs a start and no completion would read as still "running"
    # forever if anything ever parses this log. The empty-day early return used
    # to do exactly that. Asserted through the same matcher chat/insights.py uses.
    monkeypatch.setattr(yt, "fetch_liked_videos", lambda *a, **k: {"videos": []})
    assert yt.main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(is_run_success(line) for line in lines)


def test_fetch_failure_is_a_failed_run(stubbed_run, monkeypatch):
    calls = []
    monkeypatch.setattr(yt, "fetch_liked_videos",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api boom")))
    monkeypatch.setattr(yt, "notify_failure", lambda name, detail, logger=None: calls.append(str(detail)))
    assert yt.main() == 1
    assert any("api boom" in c for c in calls)


def _window_seen(monkeypatch, argv):
    """Run main() with `argv` and return the (start, end) fetch_liked_videos got."""
    seen = {}
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(yt, "fetch_liked_videos",
                        lambda start, end: seen.update(start=start, end=end) or {"videos": []})
    assert yt.main() == 0
    return seen


def test_date_fetches_that_day_not_yesterday(stubbed_run, monkeypatch):
    """Credits lapsing loses a day's Likes, and the Likes playlist keeps them —
    so the day has to be nameable. Without this the only reachable day is
    whatever yesterday happens to be when you run it."""
    seen = _window_seen(monkeypatch, ["daily_youtube_learnings", "--date", "2026-09-03"])
    assert (seen["start"], seen["end"]) == ("2026-09-03", "2026-09-03")


def test_no_date_still_covers_yesterday(stubbed_run, monkeypatch):
    """The launchd job passes no flags. --date must not move the default day."""
    from scribejay.core.dates import prior_day

    _, _, yesterday = prior_day()
    seen = _window_seen(monkeypatch, ["daily_youtube_learnings"])
    assert seen["start"] == yesterday.strftime("%Y-%m-%d")


# ---- the seams the bake-off harness drives ----------------------------------

def test_build_prompt_sends_titles_and_channels_only():
    """The Liked-video descriptions derail the small model into repetition
    loops, so they must never reach the prompt. The url must not either — it is
    rendered deterministically by journal.videos_section, and a model asked to
    copy one will eventually mistype it."""
    from datetime import date

    prompt = yt.build_prompt(date(2026, 7, 12), [
        {"title": "Git Deep Dive", "channel": "LearnThatStack",
         "url": "https://www.youtube.com/watch?v=abc",
         "description": "SUBSCRIBE!! links below http://spam.example"},
    ])
    assert "Git Deep Dive" in prompt
    assert "LearnThatStack" in prompt
    assert "July 12, 2026" in prompt
    assert "spam.example" not in prompt
    assert "watch?v=abc" not in prompt


def test_gather_returns_the_days_videos(monkeypatch):
    """The harness calls gather() once and hands the same list to both arms, so
    it has to be reachable without running the whole task."""
    from datetime import date

    seen = {}
    monkeypatch.setattr(yt, "fetch_liked_videos",
                        lambda start, end: seen.update(start=start, end=end)
                        or {"videos": [{"title": "A", "channel": "B"}]})
    videos = yt.gather(date(2026, 9, 3), __import__("logging").getLogger("t"))
    assert [v["title"] for v in videos] == ["A"]
    assert (seen["start"], seen["end"]) == ("2026-09-03", "2026-09-03")


def test_gather_degrades_to_empty_on_a_fetch_error(monkeypatch):
    """A degraded source returns {"error": ...} and must read as empty, not
    raise — AGENTS.md's degrade-don't-crash rule."""
    from datetime import date

    monkeypatch.setattr(yt, "fetch_liked_videos", lambda *a, **k: {"error": "quota"})
    assert yt.gather(date(2026, 9, 3), __import__("logging").getLogger("t")) == []


# ---- run modes --------------------------------------------------------------

def test_dry_run_writes_nothing(stubbed_run, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["daily_youtube_learnings", "--dry-run"])
    monkeypatch.setattr(yt, "fetch_liked_videos", lambda *a, **k: {"videos": [
        {"title": "Git Deep Dive", "channel": "LTS", "url": "https://youtu.be/abc"},
    ]})
    assert yt.main() == 0
    assert stubbed_run["persists"] == []
    assert "YouTube Learnings" in capsys.readouterr().out


def test_dry_run_uses_its_own_log_so_doctor_is_not_fooled(stubbed_run, monkeypatch):
    """cli/doctor.py:last_run reads logs/daily_youtube_learnings.log for the
    boundary lines, and a sibling repo reads the same folder. A bake-off run
    must not leave a scheduled run's fingerprints there."""
    names = []
    monkeypatch.setattr(yt, "setup_logger",
                        lambda name: names.append(name) or __import__("logging").getLogger(name))
    monkeypatch.setattr(sys, "argv", ["daily_youtube_learnings", "--dry-run"])
    monkeypatch.setattr(yt, "fetch_liked_videos", lambda *a, **k: {"videos": []})
    yt.main()
    assert names == ["daily_youtube_learnings_dryrun"]


def test_a_real_run_still_uses_the_task_log(stubbed_run, monkeypatch):
    names = []
    monkeypatch.setattr(yt, "setup_logger",
                        lambda name: names.append(name) or __import__("logging").getLogger(name))
    monkeypatch.setattr(yt, "fetch_liked_videos", lambda *a, **k: {"videos": []})
    yt.main()
    assert names == ["daily_youtube_learnings"]


# ---- catch-up ---------------------------------------------------------------

YESTERDAY = __import__("datetime").date(2026, 10, 11)


@pytest.fixture
def catchup(stubbed_run, monkeypatch):
    """Yesterday pinned to YESTERDAY; fetch_liked_videos records each day it is
    asked for and answers from `replies` (default: one video)."""
    import json

    monkeypatch.setattr(yt, "prior_day", lambda: (None, None, YESTERDAY))
    state = {"fetched": [], "replies": {}, "warms": 0}

    def fetch(start, end):
        state["fetched"].append(start)
        return state["replies"].get(start, {"videos": [{"title": "T", "channel": "C", "url": "https://youtu.be/x"}]})

    def warm(**k):
        state["warms"] += 1
        return True

    monkeypatch.setattr(yt, "fetch_liked_videos", fetch)
    monkeypatch.setattr(yt, "warm_model", warm)
    state["persists"] = stubbed_run["persists"]
    state["seed"] = lambda day: yt.state_path().write_text(json.dumps({"last_day": day.isoformat()}))
    state["stored"] = lambda: json.loads(yt.state_path().read_text()).get("last_day") if yt.state_path().exists() else None
    return state


def _days_back(n):
    from datetime import timedelta
    return YESTERDAY - timedelta(days=n)


def test_first_run_covers_only_yesterday(catchup):
    """No marker yet: writing two weeks of pages on the first run would be a surprise."""
    assert yt.main() == 0
    assert catchup["fetched"] == ["2026-10-11"]
    assert catchup["stored"]() == "2026-10-11"


def test_missed_days_are_covered_oldest_first(catchup):
    """A Mac that was off for days must still get those pages, in order."""
    catchup["seed"](_days_back(3))
    assert yt.main() == 0
    assert catchup["fetched"] == ["2026-10-09", "2026-10-10", "2026-10-11"]
    assert catchup["stored"]() == "2026-10-11"


def test_catchup_is_capped_at_fourteen_days(catchup):
    """A month's gap must not become a month of model calls; older days are
    the --date flag's job."""
    catchup["seed"](_days_back(30))
    assert yt.main() == 0
    assert len(catchup["fetched"]) == 14
    assert catchup["fetched"][0] == "2026-09-28"
    assert catchup["fetched"][-1] == "2026-10-11"


def test_failed_fetch_stops_the_catchup_and_keeps_earlier_days(catchup):
    """A day whose fetch failed must be retried, not skipped: the marker stops
    at the last day that finished, and later days wait behind it."""
    catchup["seed"](_days_back(3))
    catchup["replies"]["2026-10-10"] = {"videos": [], "error": "boom"}
    assert yt.main() == 0
    assert catchup["fetched"] == ["2026-10-09", "2026-10-10"]
    assert catchup["stored"]() == "2026-10-09"


def test_already_covered_does_nothing_but_still_completes(catchup, capsys):
    """The run-complete line is what the health check reads; a no-op run that
    omits it would look like a run that never finished."""
    catchup["seed"](YESTERDAY)
    assert yt.main() == 0
    assert catchup["fetched"] == []
    assert any(is_run_success(line) for line in capsys.readouterr().out.splitlines())


def test_date_flag_never_moves_the_marker(catchup, monkeypatch):
    """--date is for repairing one day by hand; it must not tell the scheduled
    job that the days in between are done."""
    from datetime import date

    catchup["seed"](date(2026, 8, 1))
    monkeypatch.setattr(sys, "argv", ["daily_youtube_learnings", "--date", "2026-09-03"])
    assert yt.main() == 0
    assert catchup["fetched"] == ["2026-09-03"]
    assert catchup["stored"]() == "2026-08-01"


def test_dry_run_never_moves_the_marker(catchup, monkeypatch):
    """A preview that advanced the marker would make the real run skip the days
    it previewed."""
    catchup["seed"](_days_back(2))
    monkeypatch.setattr(sys, "argv", ["daily_youtube_learnings", "--dry-run"])
    assert yt.main() == 0
    assert len(catchup["fetched"]) == 2
    assert catchup["stored"]() == "2026-10-09"


def test_a_day_with_no_likes_still_advances_the_marker(catchup):
    """A quiet day is finished, not missed; otherwise it is refetched forever."""
    catchup["replies"]["2026-10-11"] = {"videos": []}
    assert yt.main() == 0
    assert catchup["persists"] == []
    assert catchup["stored"]() == "2026-10-11"


def test_model_is_warmed_once_across_a_catchup(catchup):
    """Loading the model is the slow part; a seven-day catch-up must pay it once."""
    catchup["seed"](_days_back(3))
    assert yt.main() == 0
    assert len(catchup["persists"]) == 3
    assert catchup["warms"] == 1


def test_a_vault_write_failure_stops_the_catchup(catchup, monkeypatch):
    """An unplugged vault drive must cost one emailed draft, not one per missed
    day: the marker stays put and the next run retries from that day."""
    catchup["seed"](_days_back(3))
    monkeypatch.setattr(yt, "persist_or_email",
                        lambda content, prefix, day, subject, task_name, logger:
                        catchup["persists"].append((prefix, content)) or {"error": "missing"})
    assert yt.main() == 0
    assert len(catchup["persists"]) == 1
    assert catchup["stored"]() == "2026-10-08"
