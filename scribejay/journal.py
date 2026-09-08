"""Journaling-only helpers — the parts of a daily entry that are ScribeJay's alone.

Everything ScribeJay shares with a synthesis-style task (the prior-day window,
the prompt-bounding compaction, the vault write with its email fallback) lives
in scribejay/activity.py and scribejay/sinks/vault.py instead. What is here is
rendering and quality-checking of a journal entry.
"""

from scribejay.core.text import safe_label
from scribejay.core.urls import safe_url

# A page summary is ScribeJay's own words, but they are written *about* text a
# stranger controls, so it goes through safe_label like everything else here.
# Its own budget, though: safe_label's 120-character default is sized for a
# subject line, and the summarizer is allowed 120 WORDS. Truncating to the
# default would silently amputate the one section that exists to keep a page's
# detail (see pages_read_section).
MAX_NOTE_CHARS = 1000


def videos_section(videos: list) -> str:
    """Deterministic Markdown section listing every video Liked, with a link to
    each. Built in Python (not asked of the model) so the titles and URLs are
    exact and every link is scheme-validated.

    A title and a channel name are chosen by whoever published the video, so
    both go through `safe_label` before they reach the page: a title of
    `Watch me](http://evil.example) [` would otherwise close this line's own
    link early and render a live link to somewhere the user never visited.
    `safe_url` guards the destination, `safe_label` guards the words around it.
    """
    lines = ["### Videos Liked"]
    if not videos:
        lines.append("- **None:** [No videos Liked this day]")
        return "\n".join(lines)
    for v in videos:
        # After safe_label, not before: a title made only of Markdown syntax is
        # emptied by it and still needs the placeholder.
        title = safe_label(v.get("title")) or "Untitled"
        channel = safe_label(v.get("channel"))
        url = safe_url(v.get("url") or "")
        label = f"[{title}]({url})" if url else title
        lines.append(f"- {label}{f' — {channel}' if channel else ''}")
    return "\n".join(lines)


def closed_tasks_section(items: list) -> str:
    """Deterministic Markdown listing what reached Done in ClickUp, one line per
    Task, grouped by Space.

    Written in Python and never asked of the model. The draft prompt beside it is
    written for commits — "several commits are often one piece of work" — which
    says nothing true about a contract being signed or a post being researched.

    The Space leads each line because it is the part git cannot say. A Task in a
    code Space mostly restates a commit two sections above it; a Vibe Foundry one is the
    only record of that day's work anywhere.

    Every field goes through `safe_label`, which folds the newline a pasted Task
    name would otherwise break the list with AND neutralizes the Markdown and
    HTML that makes text *do* something: a Space in a shared workspace can be
    named by somebody else, and AGENTS.md names a ClickUp Task name as untrusted
    text outright."""
    lines = ["### Closed in ClickUp"]
    if not items:
        lines.append("- **None:** [No ClickUp Tasks closed this day]")
        return "\n".join(lines)
    for item in sorted(items, key=lambda i: (i.get("space", ""), i.get("title", ""))):
        title = safe_label(item.get("title")) or "(no title)"
        space = safe_label(item.get("space"))
        status = safe_label(item.get("status"))
        lines.append(f"- **{space}:** {title}" + (f" *({status})*" if status else ""))
    return "\n".join(lines)


def pages_read_section(pages: list) -> str:
    """Deterministic Markdown listing the pages that were fetched and summarised,
    one line each, with the summary kept whole.

    Built in Python for the same reason videos_section is: the URL has to be
    exact and scheme-validated, and the note is already ScribeJay's own words
    about the page. Sending them back through the model to be re-rendered would
    only let them drift from what the summarizer actually said.

    This is the second and last thing the notes do. They also go into the draft
    prompt above, which compresses several pages into one bullet — that is what
    makes the bullets specific, and it is also what loses the detail. This
    section is where the detail survives.

    The title is the untrusted one. It is read out of the remote page's own
    HTML by `sources/web_fetch.py`, so it is chosen by whoever wrote that page:
    a title of `Free money](http://evil.example) [` closes this line's link
    early and leaves a live link to somewhere the user never went, wearing a
    label the page's author picked. `safe_label` breaks that syntax. The domain
    and path fall back into the same slot and get the same treatment, and so
    does the summary — see MAX_NOTE_CHARS for why it keeps its own budget.

    `safe_label` also folds the newline a summary should never contain but that
    would silently break the list into fragments — the same bug
    closed_tasks_section guards against.
    """
    lines = ["### Pages Read"]
    if not pages:
        lines.append("- **None:** [No pages were read this day]")
        return "\n".join(lines)
    for page in pages:
        url = safe_url(page.get("url") or "")
        title = safe_label(page.get("title")) \
            or safe_label(f"{page.get('domain', '')}{page.get('path', '')}")
        note = safe_label(page.get("notes"), limit=MAX_NOTE_CHARS)
        label = f"[{title}]({url})" if url else title
        lines.append(f"- **{label}** — {note}" if note else f"- **{label}**")
    return "\n".join(lines)


def has_substantive_content(text: str) -> bool:
    """True if the draft has at least one real bullet — i.e. a bullet that isn't
    the template's "**None:**" empty-section marker. Lets a task skip writing a
    log whose every section came back empty rather than save an all-"None" file."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and "**None:**" not in stripped:
            return True
    return False


def commit_totals_line(commits: list) -> str:
    """Per-repo commit and line totals as one deterministic line.

    Arithmetic the model is never asked for. It is also the footnote that makes
    the drafted bullets checkable — a draft claiming a big day under a
    two-commit total is visibly wrong."""
    if not commits:
        return "*No commits.*"
    totals: dict = {}
    for c in commits:
        row = totals.setdefault(c["repo"], {"commits": 0, "insertions": 0, "deletions": 0})
        row["commits"] += 1
        row["insertions"] += c["insertions"]
        row["deletions"] += c["deletions"]
    parts = [
        f"{repo} — {row['commits']} commit{'s' if row['commits'] != 1 else ''}, "
        f"+{row['insertions']:,}/-{row['deletions']:,}"
        for repo, row in sorted(totals.items())
    ]
    return "*" + " · ".join(parts) + "*"
