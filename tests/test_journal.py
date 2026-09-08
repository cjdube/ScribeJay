"""Tests for scribejay/journal.py — the journaling-only rendering helpers: the
deterministic Liked-videos list (including its URL scheme guard), the
per-repo commit totals line, and the empty-draft check."""

from scribejay import journal as lc


# --------------------------------------------------------------------------- #
# safe_url / videos_section
# --------------------------------------------------------------------------- #

def test_videos_section_renders_linked_list():
    section = lc.videos_section([
        {"title": "Git Deep Dive", "channel": "LearnThatStack",
         "url": "https://www.youtube.com/watch?v=abc"},
        {"title": "No Channel Vid", "channel": "", "url": "https://youtu.be/xyz"},
    ])
    lines = section.splitlines()
    assert lines[0] == "### Videos Liked"
    assert lines[1] == "- [Git Deep Dive](https://www.youtube.com/watch?v=abc) — LearnThatStack"
    assert lines[2] == "- [No Channel Vid](https://youtu.be/xyz)"


def test_videos_section_empty_states_none():
    assert "None" in lc.videos_section([])


def test_videos_section_drops_bad_scheme_url_but_keeps_title():
    section = lc.videos_section([
        {"title": "Sketchy", "channel": "X", "url": "javascript:alert(1)"},
    ])
    assert "- Sketchy — X" in section
    assert "javascript:" not in section


# --------------------------------------------------------------------------- #
# has_substantive_content
# --------------------------------------------------------------------------- #

def test_has_substantive_content_true_with_a_real_bullet():
    assert lc.has_substantive_content("### X\n- **GitHub:** reviewed a PR")


def test_has_substantive_content_false_when_only_none_markers():
    text = ("## Daily Log\n\n### What I Worked On\n- **None:** [No qualifying items]\n\n"
            "### Tools & Tech Encountered\n- **None:** [No qualifying items]")
    assert not lc.has_substantive_content(text)


def test_has_substantive_content_false_when_no_bullets():
    assert not lc.has_substantive_content("## Daily Log: July 12, 2026\n\nheader only")


# --------------------------------------------------------------------------- #
# commit_totals_line
# --------------------------------------------------------------------------- #

def _c(repo, insertions, deletions):
    return {"repo": repo, "insertions": insertions, "deletions": deletions}


def test_commit_totals_line_sums_per_repo():
    line = lc.commit_totals_line([
        _c("LocalLLMAgent", 2111, 127), _c("LocalLLMAgent", 10, 0), _c("ObsidianWikiAgent", 40, 26)])
    assert line == "*LocalLLMAgent — 2 commits, +2,121/-127 · ObsidianWikiAgent — 1 commit, +40/-26*"


def test_commit_totals_line_singular_for_one_commit():
    assert "1 commit," in lc.commit_totals_line([_c("r", 1, 0)])


def test_commit_totals_line_with_no_commits():
    # The task skips the write on an empty day, so this is a guard rather than a
    # path anyone renders — it must not raise.
    assert lc.commit_totals_line([]) == "*No commits.*"


# ---- closed_tasks_section: the half git cannot witness ----


def _closed(title, space, status="complete"):
    return {"title": title, "space": space, "status": status}


def test_a_closed_task_renders_with_its_space_and_status():
    out = lc.closed_tasks_section([_closed("Proposal for Acme", "Vibe Foundry")])
    assert out.splitlines()[0] == "### Closed in ClickUp"
    assert "- **Vibe Foundry:** Proposal for Acme *(complete)*" in out


def test_the_space_leads_the_line():
    """It is the part git cannot say. A Wren Task restates a commit two sections
    up; a Vibe Foundry one is the only record of that day's work anywhere."""
    line = lc.closed_tasks_section([_closed("X", "Blog")]).splitlines()[1]
    assert line.startswith("- **Blog:**")


def test_tasks_are_grouped_by_space():
    out = lc.closed_tasks_section([
        _closed("Zebra", "Wren"), _closed("Apple", "Blog"), _closed("Beta", "Wren")])
    spaces = [ln.split("**")[1] for ln in out.splitlines()[1:]]
    assert spaces == ["Blog:", "Wren:", "Wren:"]


def test_an_empty_day_renders_the_none_marker():
    """has_substantive_content reads "**None:**" as an empty section, which is how
    a day with commits but no closures still reads as a real entry."""
    out = lc.closed_tasks_section([])
    assert "**None:**" in out
    assert not lc.has_substantive_content(out)


def test_a_rendered_task_list_counts_as_substantive_content():
    """The other half of the promise above — a day of pure non-code work must not
    look empty to the same check."""
    assert lc.has_substantive_content(
        lc.closed_tasks_section([_closed("Proposal for Acme", "Vibe Foundry")]))


def test_a_title_with_a_newline_cannot_break_the_list():
    """A Task name should not contain one, but a pasted title would silently split
    one bullet into fragments — the same bug a multi-paragraph description caused
    in daily_synthesis."""
    out = lc.closed_tasks_section([_closed("Call notes\n\nsecond para", "Blog")])
    assert len(out.splitlines()) == 2
    assert "Call notes second para" in out


def test_a_task_with_no_status_still_renders():
    out = lc.closed_tasks_section([{"title": "X", "space": "Blog", "status": ""}])
    assert "- **Blog:** X" in out
    assert "*()*" not in out


# ---------------------------------------------------------------------------
# pages_read_section

def _page(**kw):
    base = {"url": "https://e.com/blog/post", "domain": "e.com", "path": "/blog/post",
            "title": "A Real Title", "notes": "It says a specific thing."}
    return {**base, **kw}


def test_pages_read_section_links_every_page_and_keeps_the_whole_note():
    section = lc.pages_read_section([_page(), _page(url="https://f.com/docs/x",
                                                    title="Another", notes="And this.")])
    assert section.startswith("### Pages Read")
    assert "- **[A Real Title](https://e.com/blog/post)** — It says a specific thing." in section
    assert "- **[Another](https://f.com/docs/x)** — And this." in section


def test_pages_read_section_empty_states_none():
    assert "None" in lc.pages_read_section([])


def test_pages_read_section_drops_a_bad_scheme_url_but_keeps_the_note():
    """Same rule videos_section follows: an unsafe URL is never rendered, and
    the line degrades to unlinked text rather than vanishing."""
    section = lc.pages_read_section([_page(url="javascript:alert(1)")])
    assert "javascript:" not in section
    assert "It says a specific thing." in section


def test_pages_read_section_falls_back_to_the_path_when_there_is_no_title():
    assert "e.com/blog/post]" in lc.pages_read_section([_page(title="")])


def test_a_newline_in_a_note_cannot_break_the_list():
    """A summary should never contain a newline. One that did would split the
    bullet into fragments, which is the bug closed_tasks_section already guards
    against — so this guards it the same way."""
    section = lc.pages_read_section([_page(notes="one\ntwo\nthree")])
    assert len([ln for ln in section.splitlines() if ln.startswith("- ")]) == 1
    assert "one two three" in section


# --------------------------------------------------------------------------- #
# safe_label — every stranger-chosen word that reaches the page
#
# AGENTS.md: "Anything a stranger chose goes through safe_label before it
# reaches a file." Each of these three sections renders text somebody outside
# this machine picked — a channel's video title, a shared ClickUp Space, and a
# remote page's own <title> read straight out of its HTML. Without the guard a
# title of "x](http://evil.example) [" closes the line's own link early and
# leaves a live link to somewhere the user never visited, wearing a label its
# author chose.
#
# What these assert is that no LINK is forged, not that the attacker's host
# disappears. safe_label breaks the syntax and leaves the words: the host is
# meant to survive as plain, unlinked text, which is the whole point of
# neutralizing rather than escaping (see core/text.py). So the test is the
# count of "](" — one when the section built a link of its own, none when it
# did not.
# --------------------------------------------------------------------------- #

_SPOOF = "Free money](http://evil.example) ["


def test_a_video_title_cannot_forge_a_second_link():
    section = lc.videos_section([
        {"title": _SPOOF, "channel": "X", "url": "https://youtu.be/real"},
    ])
    # One link, and it is the one this function built.
    assert section.count("](") == 1
    assert "](https://youtu.be/real)" in section


def test_a_video_channel_cannot_inject_markdown():
    section = lc.videos_section([
        {"title": "Fine", "channel": "<img src=x onerror=alert(1)>",
         "url": "https://youtu.be/real"},
    ])
    assert "<" not in section and ">" not in section


def test_a_video_title_made_only_of_syntax_still_gets_a_placeholder():
    """safe_label empties it, and a bare "[](url)" would render as a live link
    with no visible words at all."""
    section = lc.videos_section([{"title": "[]`|", "channel": "",
                                  "url": "https://youtu.be/real"}])
    assert "[Untitled](https://youtu.be/real)" in section


def test_a_page_title_cannot_forge_a_second_link():
    section = lc.pages_read_section([_page(title=_SPOOF)])
    assert section.count("](") == 1
    assert "](https://e.com/blog/post)" in section


def test_a_page_summary_cannot_inject_markdown():
    section = lc.pages_read_section([_page(notes="see `rm -rf /` and <b>this</b>")])
    assert "`" not in section
    assert "<" not in section and ">" not in section


def test_a_long_page_summary_is_kept_whole():
    """The note gets its own budget. safe_label's 120-character default is sized
    for a subject line; the summarizer is allowed 120 WORDS, and this section is
    the only place that detail survives."""
    note = "word " * 150
    section = lc.pages_read_section([_page(notes=note)])
    assert "…" not in section
    assert len(section) > 700


def test_a_page_falling_back_to_its_path_is_still_neutralized():
    section = lc.pages_read_section([
        _page(title="", domain="e.com", path="/a](http://evil.example)"),
    ])
    assert section.count("](") == 1
    assert "](https://e.com/blog/post)" in section


def test_a_clickup_task_name_cannot_inject_html():
    section = lc.closed_tasks_section([
        {"space": "S", "title": "<img src=x onerror=alert(1)>", "status": "done"},
    ])
    assert "<" not in section and ">" not in section


def test_a_clickup_space_cannot_inject_markdown():
    """A Space in a shared workspace can be named by somebody else."""
    section = lc.closed_tasks_section([
        {"space": "[x](http://evil.example)", "title": "T", "status": ""},
    ])
    # This section renders no links at all, so any "](" would be forged.
    assert "](" not in section
