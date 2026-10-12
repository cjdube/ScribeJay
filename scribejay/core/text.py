"""Text guards shared by everything that renders externally-sourced words.

The sibling of `scribejay/core/urls.py`: `safe_url` guards a link's scheme,
`safe_label` guards the words around it. Import from here rather than copying.

Why this exists. A subject line or a sender's display name is chosen by whoever
sent the mail, and it lands verbatim in a Markdown file the user trusts. A
subject of `[Unpaid invoice](http://evil.example)` renders as a live link
inside the vault, with the real destination hidden behind friendly words. That
is deception, not a broken character — so the fix is to break the *syntax* that
makes it a link, which leaves the real URL visible as plain text.

Not an escape function. Backslash-escaping would keep the page faithful to the
original bytes, but a record of who wrote to you is a diary, not a transcript:
`\\[Unpaid invoice\\]` is noise to read every morning, and the exact
punctuation of a stranger's subject line is worth nothing.
"""

import re

# Characters that make Markdown or HTML *do* something rather than say
# something: link and image syntax, code spans, tags, and table cells. Each
# becomes a space rather than being deleted, so two words never merge into one
# — "a|b" reads as "a b", not "ab".
ACTIVE_CHARS = r"[\[\]`<>|]"

# Long enough for any real subject line, short enough that a payload has no
# room to hide past it. Nobody writes a 120-character subject on purpose.
MAX_LABEL_CHARS = 120

_ACTIVE = re.compile(ACTIVE_CHARS)
_WHITESPACE = re.compile(r"\s+")


def safe_label(text, limit: int = MAX_LABEL_CHARS) -> str:
    """Return text safe to write into a Markdown page as plain words.

    Neutralizes Markdown/HTML syntax, folds every run of whitespace (newlines
    included — a header may legally be folded across lines) into one space, and
    truncates to `limit`.

    Returns "" for empty or non-string input, so the caller can fall back to its
    own placeholder: a nameless sender costs its label, not the whole page."""
    if not isinstance(text, str):
        return ""
    cleaned = _WHITESPACE.sub(" ", _ACTIVE.sub(" ", text)).strip()
    if len(cleaned) <= limit:
        return cleaned
    # The ellipsis is inside the budget, so the result is never longer than
    # the caller asked for.
    return cleaned[:limit - 1].rstrip() + "…"


# Credentials with a fixed, recognisable shape. A chat transcript holds what the
# user pasted and what the assistant quoted back, and either can be a live key;
# the transcript goes to the model, and the summary to a page other agents read.
# Shape-matching only: a password or a key with no prefix gets through, so this
# narrows the leak rather than closing it. A private key is cut to its END line,
# or to the next blank line when the paste was truncated (base64 has none).
_SECRET_PATTERNS = [
    r"sk-(?:ant|proj|or)-[A-Za-z0-9_\-]{16,}",   # Anthropic, OpenAI, OpenRouter
    r"sk_live_[A-Za-z0-9]{16,}",                  # Stripe
    r"gh[pousr]_[A-Za-z0-9]{30,}",                # GitHub tokens
    r"github_pat_[A-Za-z0-9_]{30,}",
    r"AKIA[0-9A-Z]{16}",                          # AWS access key id
    r"AIza[0-9A-Za-z_\-]{35}",                    # Google API key
    r"xox[abprs]-[A-Za-z0-9\-]{10,}",             # Slack
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*?(?=\n\s*\n)|.*\Z)",
]
_SECRETS = re.compile("|".join(_SECRET_PATTERNS), re.DOTALL)
REDACTED = "[redacted]"


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace every recognisable credential with REDACTED. Returns the text and
    how many were replaced, so the caller can log the count — never the values."""
    return _SECRETS.subn(REDACTED, text)
