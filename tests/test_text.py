"""Tests for scribejay/core/text.py — the guard on every externally sourced
word before it reaches a Markdown page."""

from scribejay.core.text import MAX_LABEL_CHARS, safe_label


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
