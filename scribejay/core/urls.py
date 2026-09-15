"""URL guards shared by everything that renders an externally-sourced link.

Started as a verbatim copy of LocalLLMAgent's tasks/_urls.py. It is no longer
one: the scheme check there is the whole function, and the delimiter encoding
below was added here after a review found that a scheme-clean url can still
close its own Markdown link and forge a second one.

**That gap does not reach LocalLLMAgent, checked 2026-09-15.** Its four
`safe_url` callers all render HTML — `morning_brief._game_html` and
`_starred_repos_html`, `opportunity_digest._item_html` and `_triage_footer` —
and each one writes the url as `html.escape(url)` inside a quoted `href`, which
closes the attribute-breakout the same way this table closes the Markdown one.
It builds no Markdown links at all; its only `](` is a regex in
`agent/tools/evaluate_app.py` that strips them out of fetched text. So the
scheme check really is the whole job there, and copying this table across would
be a fix for a renderer that repo does not have. What would change that is a
plain-text or Markdown emitter — a text alternative beside the digest's
`MIMEText(body, "html")` — because `safe_url` alone would not cover it.

The sibling of `scribejay/core/text.py`: `safe_url` guards the destination,
`safe_label` guards the words around it. Import from here rather than copying.
"""

from urllib.parse import urlparse

# The characters that end a Markdown link target. `[Title](url)` puts the url
# inside parentheses, so a `)` in the url closes the link early and everything
# after it is rendered as fresh Markdown chosen by whoever controls the url:
#
#     https://good.example/a) [Free gift](http://evil.example
#
# renders as the expected link followed by a SECOND live link, wearing a label
# the user never saw. `)` is a legal unencoded path character — Wikipedia uses
# it — so this is reachable from an ordinary browsing history, not only from a
# crafted one.
#
# Percent-encoded rather than neutralized the way text.py:safe_label
# neutralizes its own set. A label only has to read; a url still has to
# resolve, and %28/%29/%20 are equivalent to the raw characters for every http
# server. The result is a link that still works and can no longer escape its
# own parentheses.
# `[` and `]` are in the table for a different reason than the rest. They do
# not end a link target in CommonMark, so a renderer that follows the spec is
# already safe from them — but the vault is read in Obsidian, and betting the
# guard on one renderer's bracket handling buys nothing when encoding them
# costs nothing. It also means no bracket a remote page chose survives into the
# page at all, which is a property worth being able to state plainly.
_LINK_DELIMITERS = {
    "(": "%28",
    ")": "%29",
    " ": "%20",
    "<": "%3C",
    ">": "%3E",
    "[": "%5B",
    "]": "%5D",
    '"': "%22",
    "\\": "%5C",
}


def safe_url(url: str) -> str:
    """Return url only if it's an http(s) link, else "". Guards against
    javascript:/data: (or other) schemes in externally-sourced URLs —
    html.escape() alone does not neutralize a dangerous scheme, because the
    danger is in the scheme rather than in any character escaping would touch.

    The returned url also has its Markdown link delimiters percent-encoded, so
    it cannot break out of the `(...)` a caller renders it into. See
    `_LINK_DELIMITERS`.

    Returning "" rather than raising is what lets a caller degrade to unlinked
    text: a bad URL costs its link, not the whole page."""
    try:
        if urlparse(url).scheme not in ("http", "https"):
            return ""
    except (ValueError, AttributeError):
        return ""
    for char, encoded in _LINK_DELIMITERS.items():
        url = url.replace(char, encoded)
    return url
