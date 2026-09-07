"""Tests for scribejay/model_bakeoff.py — the temporary Gemini-vs-Gemma harness.

What is actually load-bearing here is fairness, not output: if the arms do not
draft from identical input, every number the report prints is meaningless. So
the gather and the fetch are asserted to happen exactly once per day and to
reach both arms unchanged. The rest guards the two things a harness must never
do — write a vault page, or leave a scheduled run's fingerprints in a task log.

Every model call, fetch and source read is monkeypatched; no model, Chrome,
YouTube, vault or network access.
"""

import json
import logging
import sys
from datetime import date

import pytest

from scribejay import model_bakeoff as mb

DAY = date(2026, 8, 25)


@pytest.fixture
def out(tmp_path, monkeypatch):
    directory = tmp_path / "model-bakeoff"
    monkeypatch.setattr(mb, "out_dir", lambda: directory)
    return directory


@pytest.fixture
def logger():
    return logging.getLogger("test_model_bakeoff")


# ---- fairness: one gather, one fetch, identical input to every arm ----------

def test_one_gather_and_one_fetch_per_day(out, logger, monkeypatch):
    """The whole comparison rests on this. A second gather would hand one arm
    different browsing and the report would be measuring the day, not the
    model."""
    calls = {"gather": 0, "fetch": 0}
    monkeypatch.setattr(mb.chrome, "gather",
                        lambda day, log: calls.__setitem__("gather", calls["gather"] + 1)
                        or [{"domain": f"d{n}.example", "visits": 3, "pages": ["/a"]}
                            for n in range(mb.MIN_SITES)])
    monkeypatch.setattr(mb, "compact_sites", lambda sites: sites)
    monkeypatch.setattr(mb.chrome, "web_fetch_enabled", lambda *a, **k: True)
    monkeypatch.setattr(mb, "candidate_urls", lambda sites, limit: [{"url": "https://x/a"}])
    monkeypatch.setattr(mb.chrome, "max_pages", lambda log: 5)

    from scribejay.sources import web_fetch
    monkeypatch.setattr(web_fetch, "fetch_pages",
                        lambda c, logger_=None: (calls.__setitem__("fetch", calls["fetch"] + 1)
                                                 or ([{"url": "https://x/a", "domain": "x",
                                                       "path": "/a", "text": "t"}],
                                                     {"cached": 0, "failed": 0, "seconds": 0.1})))
    monkeypatch.setattr(mb.chrome, "summarize_pages",
                        lambda pages, log, backend: [{**p, "notes": f"note from {backend}"}
                                                     for p in pages])
    monkeypatch.setattr(mb, "complete_text", lambda **k: "- **Thing:** a claim")
    monkeypatch.setattr(mb, "warm_model", lambda **k: True)

    mb.run_day("daily_chrome_learnings", DAY, logger)
    assert calls == {"gather": 1, "fetch": 1}


def test_both_arms_get_the_identical_page_list(out, logger, monkeypatch):
    seen = []
    monkeypatch.setattr(mb.chrome, "gather",
                        lambda day, log: [{"domain": f"d{n}"} for n in range(mb.MIN_SITES)])
    monkeypatch.setattr(mb, "compact_sites", lambda sites: sites)
    monkeypatch.setattr(mb.chrome, "web_fetch_enabled", lambda *a, **k: True)
    monkeypatch.setattr(mb, "candidate_urls", lambda sites, limit: [{"url": "https://x/a"}])
    monkeypatch.setattr(mb.chrome, "max_pages", lambda log: 5)

    from scribejay.sources import web_fetch
    pages = [{"url": "https://x/a", "domain": "x", "path": "/a", "text": "t"}]
    monkeypatch.setattr(web_fetch, "fetch_pages",
                        lambda c, logger_=None: (pages, {"cached": 1, "failed": 0, "seconds": 0.0}))
    monkeypatch.setattr(mb.chrome, "summarize_pages",
                        lambda p, log, backend: seen.append((backend, [x["url"] for x in p])) or [])
    monkeypatch.setattr(mb, "complete_text", lambda **k: "- **Thing:** a claim")
    monkeypatch.setattr(mb, "warm_model", lambda **k: True)

    mb.run_day("daily_chrome_learnings", DAY, logger)
    assert [arm for arm, _ in seen] == list(mb.ARMS)
    assert seen[0][1] == seen[1][1] == ["https://x/a"]


def test_youtube_arms_get_the_identical_video_list(out, logger, monkeypatch):
    prompts = []
    videos = [{"title": "T", "channel": "C", "url": "https://youtu.be/a"}]
    monkeypatch.setattr(mb.youtube, "gather", lambda day, log: videos)
    monkeypatch.setattr(mb, "warm_model", lambda **k: True)
    monkeypatch.setattr(mb, "complete_text",
                        lambda **k: prompts.append(k["user_prompt"]) or "- **Theme:** a claim")

    mb.run_day("daily_youtube_learnings", DAY, logger)
    assert len(prompts) == 2 and prompts[0] == prompts[1]


# ---- blinding ---------------------------------------------------------------

def test_key_file_matches_the_drafts_written(out, logger, monkeypatch):
    monkeypatch.setattr(mb.youtube, "gather", lambda day, log: [{"title": "T", "channel": "C"}])
    monkeypatch.setattr(mb, "warm_model", lambda **k: True)
    monkeypatch.setattr(mb, "complete_text",
                        lambda **k: f"- **{k['backend']}:** a claim")

    mb.run_day("daily_youtube_learnings", DAY, logger)
    key = json.loads((out / f"daily_youtube_learnings-{DAY}-key.json").read_text())
    assert sorted(key) == sorted(mb.BLIND_LETTERS)
    assert sorted(key.values()) == sorted(mb.ARMS)
    for letter, arm in key.items():
        text = (out / f"daily_youtube_learnings-{DAY}-{letter}.md").read_text()
        assert f"**{arm}:**" in text  # the letter really holds that arm's draft


def test_letters_are_shuffled_across_days(out, logger, monkeypatch):
    """A fixed order would let a reader who cracks one day read the rest."""
    monkeypatch.setattr(mb.youtube, "gather", lambda day, log: [{"title": "T", "channel": "C"}])
    monkeypatch.setattr(mb, "warm_model", lambda **k: True)
    monkeypatch.setattr(mb, "complete_text", lambda **k: "- **X:** a claim")
    monkeypatch.setattr(mb.random, "shuffle", lambda seq: seq.reverse())

    mb.run_day("daily_youtube_learnings", DAY, logger)
    key = json.loads((out / f"daily_youtube_learnings-{DAY}-key.json").read_text())
    assert key[mb.BLIND_LETTERS[0]] == mb.ARMS[-1]  # the shuffle really applied


# ---- a harness must not behave like a real run ------------------------------

def test_writes_nothing_outside_its_own_directory(out, logger, monkeypatch, tmp_path):
    monkeypatch.setattr(mb.youtube, "gather", lambda day, log: [{"title": "T", "channel": "C"}])
    monkeypatch.setattr(mb, "warm_model", lambda **k: True)
    monkeypatch.setattr(mb, "complete_text", lambda **k: "- **X:** a claim")

    mb.run_day("daily_youtube_learnings", DAY, logger)
    written = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert written and all(p.parent == out for p in written)


def test_never_reaches_the_vault_sink():
    """persist_or_email is how a page reaches the vault and how a failure
    reaches the user's inbox. The harness must import neither."""
    source = (mb.__file__ and open(mb.__file__).read())
    assert "persist_or_email" not in source
    assert "notify_failure" not in source


def test_uses_its_own_log_so_doctor_is_not_fooled(monkeypatch, out):
    names = []
    monkeypatch.setattr(mb, "setup_logger",
                        lambda name: names.append(name) or logging.getLogger(name))
    monkeypatch.setattr(mb, "run", lambda task, days, log: [])
    monkeypatch.setattr(sys, "argv", ["model_bakeoff", "--task", "daily_youtube_learnings"])
    assert mb.main() == 0
    assert names == ["model_bakeoff"]


# ---- a failing arm is a result, not a crash ---------------------------------

def test_a_failing_arm_is_recorded_and_the_other_still_runs(out, logger, monkeypatch):
    """This is the exact failure that started the bake-off — a 429 on one
    backend. It must land in the row, not end the run."""
    monkeypatch.setattr(mb.youtube, "gather", lambda day, log: [{"title": "T", "channel": "C"}])
    monkeypatch.setattr(mb, "warm_model", lambda **k: True)

    def flaky(**k):
        if k["backend"] == "gemini":
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return "- **Theme:** a claim"

    monkeypatch.setattr(mb, "complete_text", flaky)
    row = mb.run_day("daily_youtube_learnings", DAY, logger)
    assert "429" in row["arms"]["gemini"]["failed"]
    assert row["arms"]["ollama"]["wrote_a_page"]
    assert row["arms"]["ollama"]["bullets"] == 1  # the synthesis bullet, alone


def test_a_day_with_no_data_is_skipped_not_counted(out, logger, monkeypatch):
    monkeypatch.setattr(mb.youtube, "gather", lambda day, log: [])
    assert mb.run_day("daily_youtube_learnings", DAY, logger) == {}
    assert not out.exists() or not list(out.glob("*.md"))


def test_a_day_too_quiet_to_compare_is_skipped(out, logger, monkeypatch):
    """Any model writes one bullet from two visits. Counting such a day as a
    comparison is how a bake-off reports a tie it never tested — the first run
    of this harness spent four of its seven chrome days that way."""
    monkeypatch.setattr(mb.chrome, "gather",
                        lambda day, log: [{"domain": "a"}, {"domain": "b"}])
    monkeypatch.setattr(mb, "compact_sites", lambda sites: sites)
    assert mb.gather_chrome(DAY, logger) is None
    assert mb.gather_chrome(DAY, logger, min_sites=2) is not None


def test_run_keeps_walking_back_until_it_has_enough_days(out, logger, monkeypatch):
    """Likes land on about half of all days. A run that stopped at the first
    empty day would compare two days and call it seven."""
    days = []
    monkeypatch.setattr(mb.youtube, "gather",
                        lambda day, log: days.append(day) or
                        ([{"title": "T", "channel": "C"}] if day.day % 2 == 0 else []))
    monkeypatch.setattr(mb, "warm_model", lambda **k: True)
    monkeypatch.setattr(mb, "complete_text", lambda **k: "- **X:** a claim")

    rows = mb.run("daily_youtube_learnings", 3, logger)
    assert len(rows) == 3
    assert len(days) > 3  # it really had to skip some


# ---- scoring ----------------------------------------------------------------

def test_repeat_ratio_catches_a_loop_and_ignores_a_clean_draft():
    clean = "- **Agents:** The report describes tool use, planning and recovery in detail."
    looped = " ".join(["the model repeats this exact phrase over and over"] * 6)
    assert mb._repeat_ratio(clean) == 0.0
    assert mb._repeat_ratio(looped) > 50


def test_template_check_flags_a_left_behind_placeholder():
    text = ("## Daily Log: August 25, 2026\n\n### Tools & Tech Encountered\n"
            "- **[Tool/Technology]:** [How it was used]\n- **B:** two\n"
            "### Product & Strategy\n- **C:** three\n- **D:** four\n")
    checks = mb._template_ok(text, "daily_chrome_learnings")
    assert checks["headings_present"]
    assert checks["unfilled_placeholders"]
    assert not mb._template_clean(checks)


def test_template_check_passes_a_good_draft():
    text = ("## Daily Log: August 25, 2026\n\n### Tools & Tech Encountered\n"
            "- **A:** one\n- **B:** two\n\n### Product & Strategy\n"
            "- **C:** three\n- **D:** four\n")
    assert mb._template_clean(mb._template_ok(text, "daily_chrome_learnings"))


def test_template_check_ignores_the_sections_python_appends():
    """Pages Read and Videos Liked are written deterministically after the
    model is done. Counting them as the model going off-template would fail
    every good draft."""
    text = ("## Daily Log: August 25, 2026\n\n### Tools & Tech Encountered\n"
            "- **A:** one\n- **B:** two\n\n### Product & Strategy\n"
            "- **C:** three\n- **D:** four\n\n### Pages Read\n")
    assert not mb._template_ok(text, "daily_chrome_learnings")["extra_sections"]


def test_unknown_cost_totals_as_none_not_zero():
    """A row the price table does not cover must not make a paid arm look
    free."""
    rows = [{"ok": True, "cost_usd": 0.001}, {"ok": True, "cost_usd": None}]
    assert mb._ledger_totals(rows)["cost_usd"] is None
    assert mb._ledger_totals(rows[:1])["cost_usd"] == 0.001


def test_ledger_rows_are_attributed_to_the_arm_that_made_them(out, logger, monkeypatch,
                                                              tmp_path):
    """Tokens and cost per arm come from the ledger slice each arm appended."""
    from scribejay.core import usage_ledger

    ledger = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", ledger)
    monkeypatch.setattr(mb.youtube, "gather", lambda day, log: [{"title": "T", "channel": "C"}])
    monkeypatch.setattr(mb, "warm_model", lambda **k: True)

    def recording(**k):
        with open(ledger, "a") as f:
            f.write(json.dumps({"ok": True, "backend": k["backend"], "prompt_tokens": 100,
                                "output_tokens": 10, "duration_ms": 5,
                                "cost_usd": 0.5 if k["backend"] == "gemini" else 0.0}) + "\n")
        return "- **X:** a claim"

    monkeypatch.setattr(mb, "complete_text", recording)
    row = mb.run_day("daily_youtube_learnings", DAY, logger)
    assert row["arms"]["gemini"]["usage"]["cost_usd"] == 0.5
    assert row["arms"]["ollama"]["usage"]["cost_usd"] == 0.0
    assert row["arms"]["gemini"]["usage"]["prompt_tokens"] == 100


def test_a_pruned_ledger_reports_missing_not_zero(out, logger, monkeypatch, tmp_path):
    """The ledger rewrites itself past a size cap. Reading a stale byte offset
    would report a real spend as zero."""
    from scribejay.core import usage_ledger

    ledger = tmp_path / "usage.jsonl"
    ledger.write_text("x" * 100)
    monkeypatch.setattr(usage_ledger, "LEDGER_PATH", ledger)
    assert mb._ledger_since(500, logger) == []


def test_score_ignores_the_list_python_appends(out, logger, monkeypatch):
    """journal.videos_section and journal.pages_read_section are written by
    Python from the source data. Scoring the assembled page instead of the
    model's own draft would count Python's bullets as the model's, and would
    score its own inputs as 100% "grounded" — so a broken model and a good one
    would produce the same numbers."""
    videos = [{"title": "T1", "channel": "C", "url": "https://youtu.be/a"},
              {"title": "T2", "channel": "C", "url": "https://youtu.be/b"}]
    monkeypatch.setattr(mb.youtube, "gather", lambda day, log: videos)
    monkeypatch.setattr(mb, "warm_model", lambda **k: True)
    monkeypatch.setattr(mb, "complete_text", lambda **k: "- **Theme:** one bullet")

    row = mb.run_day("daily_youtube_learnings", DAY, logger)
    assert row["arms"]["ollama"]["bullets"] == 1  # not 3
    # and the reader still gets the full page
    page = (out / f"daily_youtube_learnings-{DAY}-V.md").read_text()
    assert "https://youtu.be/a" in page


def test_a_quiet_day_is_not_a_template_failure():
    """The chrome prompt asks for "**None:** [No qualifying items for this
    section]" verbatim, brackets and all, when a section has nothing in it.
    Marking that as an unfilled placeholder or a short section would fail both
    models on every quiet day — the one day they are supposed to agree."""
    text = ("## Daily Log: September 6, 2026\n\n### Tools & Tech Encountered\n"
            "- **None:** [No qualifying items for this section]\n\n"
            "### Product & Strategy\n"
            "- **None:** [No qualifying items for this section]\n")
    assert mb._template_clean(mb._template_ok(text, "daily_chrome_learnings"))


def test_bullets_are_counted_per_section_not_in_total():
    """Four bullets is a pass as two-and-two and a fail as four-and-none. A
    flat total cannot tell those apart, and the second is the shape a small
    model actually produces."""
    lopsided = ("## Daily Log: x\n### Tools & Tech Encountered\n"
                "- **A:** 1\n- **B:** 2\n- **C:** 3\n- **D:** 4\n"
                "### Product & Strategy\n")
    assert not mb._template_ok(lopsided, "daily_chrome_learnings")["bullets_in_range"]


def test_one_bullet_where_two_were_asked_for_is_still_flagged():
    """The sanctioned exemption is the "None" bullet only. A single real
    bullet where the prompt asked for 2-4 stays a miss."""
    text = "## YouTube Learnings: x\n### Themes Explored\n- **Theme:** only one\n"
    assert not mb._template_ok(text, "daily_youtube_learnings")["bullets_in_range"]
