"""Tests for scribejay/core/text.py — the guard on every externally sourced
word before it reaches a Markdown page."""

from scribejay.core.text import MAX_LABEL_CHARS, redact_secrets, safe_label


def test_safe_label_passes_ordinary_text_through():
    assert safe_label("Chat about local-first agents?") == "Chat about local-first agents?"


def test_safe_label_defuses_a_disguised_link():
    # The attack this exists for: friendly words hiding a hostile destination.
    # The brackets die, so the real URL is visible as plain text.
    out = safe_label("[Unpaid invoice](http://evil.example)")
    assert "[" not in out and "]" not in out
    assert out == "Unpaid invoice (http://evil.example)"


def test_safe_label_defuses_an_image_embed():
    # ![alt](url) renders a remote image, which is a read receipt.
    out = safe_label("![x](http://evil.example/track.png)")
    assert "[" not in out and "]" not in out


def test_safe_label_defuses_html_tags():
    out = safe_label("<script>alert(1)</script>")
    assert "<" not in out and ">" not in out


def test_safe_label_defuses_code_spans_and_table_cells():
    assert "`" not in safe_label("run `rm -rf /` now")
    assert "|" not in safe_label("col a|col b")


def test_safe_label_separates_rather_than_merges():
    # A deleted character would make "a|b" read as the single word "ab".
    assert safe_label("a|b") == "a b"


def test_safe_label_folds_newlines():
    # A folded header arrives with real newlines; unfolded, it would break the
    # bullet it is written into.
    assert safe_label("first\n  second\tthird") == "first second third"


def test_safe_label_truncates_within_the_budget():
    out = safe_label("x" * 500)
    assert len(out) == MAX_LABEL_CHARS
    assert out.endswith("…")


def test_safe_label_respects_a_caller_supplied_limit():
    assert len(safe_label("y" * 50, limit=10)) == 10


def test_safe_label_leaves_short_text_unmarked():
    # An exactly-at-limit label must not gain an ellipsis it did not earn.
    exact = "z" * MAX_LABEL_CHARS
    assert safe_label(exact) == exact


def test_safe_label_tolerates_empty_and_non_string():
    # A message with no Subject header costs its label, not the whole page.
    assert safe_label("") == ""
    assert safe_label(None) == ""
    assert safe_label(12345) == ""


# Built at runtime, never written out whole: a realistic-looking key in a
# committed file trips GitHub's push protection.
FAKE_SECRETS = {
    "anthropic": "sk-ant-" + "api03-" + "a1B2" * 6,
    "openai": "sk-proj-" + "Zx9" * 8,
    "stripe": "sk_live_" + "4eC3" * 6,
    "github": "ghp_" + "Ab1" * 12,
    "github fine-grained": "github_pat_" + "11AB" * 9,
    "aws": "AKIA" + "QX7Z" * 4,
    "google": "AIza" + "Sy" + "Dq3_" * 8 + "x",
    "slack": "xoxb-" + "1234-5678-" + "aBcD" * 4,
}


def test_redact_secrets_catches_each_known_shape():
    for kind, secret in FAKE_SECRETS.items():
        out, n = redact_secrets(f"User: my key is {secret} please use it")
        assert secret not in out, kind
        assert n == 1, kind
        assert out == "User: my key is [redacted] please use it", kind


def test_redact_secrets_cuts_a_whole_private_key():
    body = "MIIEvQ" + "A" * 60
    pem = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n{body}\n-----END RSA PRIVATE KEY-----"
    out, n = redact_secrets(f"Assistant: here it is\n{pem}\nand that is all")
    assert body not in out and "PRIVATE KEY" not in out
    assert out == "Assistant: here it is\n[redacted]\nand that is all"
    assert n == 1


def test_a_truncated_private_key_stops_at_the_next_blank_line():
    # No END line: the paste was cut off. The key body has no blank lines, so
    # the next one is where the key certainly stopped.
    body = "MIIEvQ" + "A" * 60
    out, n = redact_secrets(f"-----BEGIN PRIVATE KEY-----\n{body}\n\nUser: next question")
    assert body not in out
    assert out.endswith("User: next question")
    assert n == 1


def test_redact_secrets_leaves_ordinary_talk_about_keys_alone():
    text = ("User: where does the sk-ant- prefix come from? AKIA keys are AWS. "
            "A public cert starts -----BEGIN CERTIFICATE----- and that is fine.")
    assert redact_secrets(text) == (text, 0)
