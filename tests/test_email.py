"""Tests for scribejay/sinks/email.py — send_email: builds the MIME message,
pins the recipient, maps errors. The Gmail client is stubbed.

Mirrors the send_email slice of LocalLLMAgent's tests/test_email.py. Dropped
the tool-wrapper test (ScribeJay has no tool registry to dispatch through)
and every reply_to_thread/reply_plan test (that function has no counterpart
here — see the module docstring: ScribeJay never grows a `to` beyond
BRIEF_TO_EMAIL and has no reply-to-thread need)."""

import base64
from email import message_from_bytes

from scribejay.sinks import email as email_mod
from scribejay.sinks.email import send_email


def _patch_gmail(monkeypatch, box):
    """Stub build_service with a fluent fake that records the sent raw message."""
    class _Chain:
        def users(self):
            return self

        def messages(self):
            return self

        def send(self, userId=None, body=None):
            box["userId"] = userId
            box["raw"] = body["raw"]
            return self

        def execute(self):
            return {"id": "msg-1"}

    def fake_build_service(api, version):
        box["api"] = (api, version)
        return _Chain()

    monkeypatch.setattr(email_mod, "build_service", fake_build_service)


def _decode(raw):
    return message_from_bytes(base64.urlsafe_b64decode(raw))


def test_send_email_builds_plaintext_to_configured_recipient(monkeypatch):
    monkeypatch.setenv("BRIEF_TO_EMAIL", "owner@example.com")
    box = {}
    _patch_gmail(monkeypatch, box)
    result = send_email("Subject line", "Hello there")
    assert result == {"message_id": "msg-1"}
    assert box["userId"] == "me" and box["api"] == ("gmail", "v1")
    msg = _decode(box["raw"])
    assert msg["to"] == "owner@example.com"
    assert msg["subject"] == "Subject line"
    assert msg.get_content_type() == "text/plain"
    assert "Hello there" in msg.get_payload(decode=True).decode()


def test_send_email_html_flag_sets_html_content_type(monkeypatch):
    monkeypatch.setenv("BRIEF_TO_EMAIL", "owner@example.com")
    box = {}
    _patch_gmail(monkeypatch, box)
    send_email("S", "<b>hi</b>", html=True)
    assert _decode(box["raw"]).get_content_type() == "text/html"


def test_send_email_explicit_to_overrides_default(monkeypatch):
    monkeypatch.setenv("BRIEF_TO_EMAIL", "owner@example.com")
    box = {}
    _patch_gmail(monkeypatch, box)
    send_email("S", "b", to="ops@example.com")
    assert _decode(box["raw"])["to"] == "ops@example.com"


def test_send_email_errors_when_no_recipient(monkeypatch):
    monkeypatch.delenv("BRIEF_TO_EMAIL", raising=False)
    # The recipient check must short-circuit before any Gmail client is built.
    monkeypatch.setattr(email_mod, "build_service",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("build_service reached")))
    out = send_email("S", "b")
    assert "BRIEF_TO_EMAIL" in out["error"]


def test_send_email_maps_api_exception_to_error(monkeypatch):
    monkeypatch.setenv("BRIEF_TO_EMAIL", "owner@example.com")

    def boom(*a, **k):
        raise RuntimeError("gmail 500")
    monkeypatch.setattr(email_mod, "build_service", boom)
    assert send_email("S", "b")["error"] == "gmail 500"
