"""Tests for scribejay/core/backends/openrouter.py — the second cloud backend
behind core.model's _llm_chat seam.

Shorter than the Gemini tests because the module is: no SDK, no streaming, no
message translation. What is worth pinning is the payload it builds (model,
output cap, the think=False mapping), the parse of an OpenAI-shaped reply, and
the three ways a paid API says no — an error status, a 200 carrying an error
object, and a missing key.
"""

import logging

import pytest

from scribejay.core.backends import openrouter

# Captured at import, before the autouse egress guard in conftest.py replaces
# it — the two tests below exercise _post itself, so they have to put the real
# one back (test_secrets.py restores its Keychain runner the same way).
_REAL_POST = openrouter._post


def _reply(content="ok", finish_reason="stop", model="anthropic/claude-sonnet-5"):
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content},
                     "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4},
    }


def _patch_post(monkeypatch, response, captured=None):
    """Stub the one network call, recording the payload it was handed."""
    captured = captured if captured is not None else {}

    def _fake_post(payload, timeout):
        captured.setdefault("calls", []).append({"payload": payload, "timeout": timeout})
        return response

    monkeypatch.setattr(openrouter, "_post", _fake_post)
    return captured


def test_returns_the_canonical_message_shape(monkeypatch):
    _patch_post(monkeypatch, _reply(content="Hello"))

    message = openrouter._openrouter_chat([{"role": "user", "content": "hey"}])

    assert message == {"role": "assistant", "content": "Hello"}


def test_messages_pass_through_unchanged(monkeypatch):
    # ScribeJay's canonical shape is already the OpenAI shape, so there is no
    # translation layer to get wrong — including the system turn, which stays
    # a message here rather than being hoisted the way Gemini's is.
    captured = _patch_post(monkeypatch, _reply())
    messages = [
        {"role": "system", "content": "You are ScribeJay."},
        {"role": "user", "content": "Summarize the day."},
    ]

    openrouter._openrouter_chat(messages)

    assert captured["calls"][0]["payload"]["messages"] == messages


def test_model_defaults_and_is_settings_overridable(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    captured = _patch_post(monkeypatch, _reply())
    openrouter._openrouter_chat([{"role": "user", "content": "hey"}])
    assert captured["calls"][0]["payload"]["model"] == openrouter.OPENROUTER_DEFAULT_MODEL

    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-5")
    captured2 = _patch_post(monkeypatch, _reply())
    openrouter._openrouter_chat([{"role": "user", "content": "hey"}])
    assert captured2["calls"][0]["payload"]["model"] == "openai/gpt-5"


def test_output_cap_defaults_and_is_settings_overridable(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MAX_OUTPUT_TOKENS", raising=False)
    captured = _patch_post(monkeypatch, _reply())
    openrouter._openrouter_chat([{"role": "user", "content": "hey"}])
    assert captured["calls"][0]["payload"]["max_tokens"] == 8192

    monkeypatch.setenv("OPENROUTER_MAX_OUTPUT_TOKENS", "2048")
    captured2 = _patch_post(monkeypatch, _reply())
    openrouter._openrouter_chat([{"role": "user", "content": "hey"}])
    assert captured2["calls"][0]["payload"]["max_tokens"] == 2048


def test_think_false_turns_reasoning_off_and_nothing_else_touches_it(monkeypatch):
    # think=False is how every template-filling call in the repo asks for no
    # reasoning; think=None means "leave the model's own default alone".
    captured = _patch_post(monkeypatch, _reply())

    openrouter._openrouter_chat([{"role": "user", "content": "hey"}], think=False)
    assert captured["calls"][0]["payload"]["reasoning"] == {"enabled": False}

    openrouter._openrouter_chat([{"role": "user", "content": "hey"}], think=None)
    assert "reasoning" not in captured["calls"][1]["payload"]

    openrouter._openrouter_chat([{"role": "user", "content": "hey"}], think=True)
    assert "reasoning" not in captured["calls"][2]["payload"]


def test_the_call_always_carries_a_timeout(monkeypatch):
    captured = _patch_post(monkeypatch, _reply())

    openrouter._openrouter_chat([{"role": "user", "content": "hey"}])
    assert captured["calls"][0]["timeout"] == openrouter.OPENROUTER_TIMEOUT_S

    openrouter._openrouter_chat([{"role": "user", "content": "hey"}], timeout=12.0)
    assert captured["calls"][1]["timeout"] == 12.0


# ---- the ways a paid API says no ---------------------------------------------

def test_an_error_status_carries_the_body_into_the_message(monkeypatch):
    # raise_for_status() would report "402 Client Error" and drop the reason.
    class _Resp:
        status_code = 402
        text = '{"error":{"message":"Insufficient credits"}}'

    monkeypatch.setattr(openrouter, "_post", _REAL_POST)
    monkeypatch.setattr(openrouter.requests, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr(openrouter, "resolve_key", lambda name: "sk-test")

    with pytest.raises(openrouter.OpenRouterError, match="Insufficient credits"):
        openrouter._post({}, 1.0)


def test_a_200_carrying_an_error_object_still_raises(monkeypatch):
    _patch_post(monkeypatch, {"error": {"message": "no endpoints found"}})

    with pytest.raises(openrouter.OpenRouterError, match="no endpoints found"):
        openrouter._openrouter_chat([{"role": "user", "content": "hey"}])


def test_a_missing_key_raises_before_the_network(monkeypatch):
    monkeypatch.setattr(openrouter, "_post", _REAL_POST)
    monkeypatch.setattr(openrouter, "resolve_key", lambda name: None)

    def _no_network(*a, **k):
        raise AssertionError("must not reach the network without a key")

    monkeypatch.setattr(openrouter.requests, "post", _no_network)

    with pytest.raises(openrouter.OpenRouterError, match="OPENROUTER_API_KEY"):
        openrouter._post({}, 1.0)


# ---- the logging that makes a silently different model visible ---------------

def test_the_log_names_the_model_asked_for_and_the_one_served(monkeypatch, caplog):
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-5")
    _patch_post(monkeypatch, _reply(model="anthropic/claude-haiku-4.5"))
    logger = logging.getLogger("test_openrouter.served")

    with caplog.at_level(logging.INFO, logger=logger.name):
        openrouter._openrouter_chat([{"role": "user", "content": "hey"}], logger=logger)

    assert "model=anthropic/claude-sonnet-5" in caplog.text
    assert "served=anthropic/claude-haiku-4.5" in caplog.text


def test_a_truncated_reply_warns(monkeypatch, caplog):
    _patch_post(monkeypatch, _reply(content="half a dr", finish_reason="length"))
    logger = logging.getLogger("test_openrouter.length")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        openrouter._openrouter_chat([{"role": "user", "content": "hey"}], logger=logger)

    assert "OPENROUTER_MAX_OUTPUT_TOKENS" in caplog.text


def test_an_empty_reply_warns_rather_than_degrading_silently(monkeypatch, caplog):
    # A reasoning model spending the whole budget returns EMPTY content, not a
    # truncated answer — the failure docs/model-constraints.md exists for.
    _patch_post(monkeypatch, _reply(content="", finish_reason="stop"))
    logger = logging.getLogger("test_openrouter.empty")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        message = openrouter._openrouter_chat(
            [{"role": "user", "content": "hey"}], logger=logger)

    assert message["content"] == ""
    assert "EMPTY content" in caplog.text
