"""URL guards shared by everything that renders an externally-sourced link.

Mirrors LocalLLMAgent's tasks/_urls.py verbatim. Import `safe_url` from here
rather than copying it.
"""

from urllib.parse import urlparse


def safe_url(url: str) -> str:
    """Return url only if it's an http(s) link, else "". Guards against
    javascript:/data: (or other) schemes in externally-sourced URLs —
    html.escape() alone does not neutralize a dangerous scheme, because the
    danger is in the scheme rather than in any character escaping would touch.

    Returning "" rather than raising is what lets a caller degrade to unlinked
    text: a bad URL costs its link, not the whole page."""
    try:
        return url if urlparse(url).scheme in ("http", "https") else ""
    except (ValueError, AttributeError):
        return ""
