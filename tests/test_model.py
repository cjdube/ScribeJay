"""Tests for scribejay/core/model.py — the single choke point for ScribeJay's
one model call per task.

Mirrors the _ollama_chat/complete_text/warm_model/think-stripping slice of
LocalLLMAgent's tests/test_loop.py. Dropped everything that belongs to
agent.loop's tool-calling loop with no counterpart here: tool_calls
reassembly, should_cancel/TurnCancelled, advance()/confirm-gates, the
oversized-tool-result cap, the busy-slot probe, and the LaTeX-in-prose
conversion (that lives in chat/server.py's rendering, not the model seam).
backend()/log_backend() replace resolve_backend() with ScribeJay's own
SCRIBEJAY_* env chain (see scribejay/core/model.py's module docstring)."""

import json as _json
import logging

import pytest

from scribejay.core import model


class _FakeResponse:
    """A streaming Ollama response: yields each chunk dict as an NDJSON line,
    and works as a context manager like requests' streamed Response."""
    def __init__(self, chunks):
        self._chunks = chunks

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        for chunk in self._chunks:
            yield _json.dumps(chunk).encode()


def _patch_post(monkeypatch, captured, response):
    """Capture the outgoing payload and return a canned Ollama stream."""
    chunk = {
        "message": response.get("message", {}),
        "done": True,
        "prompt_eval_count": response.get("prompt_eval_count"),
        "eval_count": response.get("eval_count"),
    }
    _patch_post_chunks(monkeypatch, captured, [chunk])


def _patch_post_chunks(monkeypatch, captured, chunks):
    def fake_post(url, json=None, timeout=None, stream=None):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse(chunks)

    monkeypatch.setattr(model.requests, "post", fake_post)


def _llm_returning(content, monkeypatch):
    monkeypatch.setattr(model, "_ollama_chat", lambda messages, **kwargs: {"content": content})


# --------------------------------------------------------------------------- #
# _ollama_chat payload
# --------------------------------------------------------------------------- #

def test_payload_sets_default_num_ctx(monkeypatch):
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    captured = {}
    _patch_post(monkeypatch, captured, {"message": {"content": "hi"}})

    message = model._ollama_chat([{"role": "user", "content": "hey"}])

    assert captured["payload"]["options"]["num_ctx"] == 8192
    assert captured["payload"]["stream"] is True
    assert message["content"] == "hi"


def test_payload_sets_default_keep_alive(monkeypatch):
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)
    captured = {}
    _patch_post(monkeypatch, captured, {"message": {"content": "hi"}})

    model._ollama_chat([{"role": "user", "content": "hey"}])

    assert captured["payload"]["keep_alive"] == "30m"


def test_keep_alive_honors_env_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "-1")
    captured = {}
    _patch_post(monkeypatch, captured, {"message": {"content": "hi"}})

    model._ollama_chat([{"role": "user", "content": "hey"}])

    assert captured["payload"]["keep_alive"] == "-1"


def test_num_ctx_honors_env_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_CTX", "16384")
    captured = {}
    _patch_post(monkeypatch, captured, {"message": {"content": "hi"}})

    model._ollama_chat([{"role": "user", "content": "hey"}])

    assert captured["payload"]["options"]["num_ctx"] == 16384


def test_payload_sets_default_num_predict(monkeypatch):
    monkeypatch.delenv("OLLAMA_NUM_PREDICT", raising=False)
    captured = {}
    _patch_post(monkeypatch, captured, {"message": {"content": "hi"}})

    model._ollama_chat([{"role": "user", "content": "hey"}])

    assert captured["payload"]["options"]["num_predict"] == 3072


def test_num_predict_honors_env_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "1024")
    captured = {}
    _patch_post(monkeypatch, captured, {"message": {"content": "hi"}})

    model._ollama_chat([{"role": "user", "content": "hey"}])

    assert captured["payload"]["options"]["num_predict"] == 1024


def test_think_key_omitted_unless_a_caller_opts_out(monkeypatch):
    captured = {}
    _patch_post(monkeypatch, captured, {"message": {"content": "hi"}})

    model._ollama_chat([{"role": "user", "content": "hey"}])

    assert "think" not in captured["payload"]


def test_think_false_reaches_the_payload(monkeypatch):
    # A template-filling task turns thinking off: the scratchpad competes with
    # the answer for num_predict, and losing that race returns EMPTY content.
    captured = {}
    _patch_post(monkeypatch, captured, {"message": {"content": "hi"}})

    model._ollama_chat([{"role": "user", "content": "hey"}], think=False)

    assert captured["payload"]["think"] is False


def test_complete_text_passes_think_through_the_seam(monkeypatch):
    seen = {}

    def fake_llm_chat(messages, **kwargs):
        seen.update(kwargs)
        return {"content": "ok"}

    monkeypatch.setattr(model, "_llm_chat", fake_llm_chat)

    assert model.complete_text("sys", "user", think=False) == "ok"
    assert seen["think"] is False


def test_warns_when_generation_reaches_num_predict(monkeypatch, caplog):
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "50")
    _patch_post(
        monkeypatch, {},
        {"message": {"content": "hi"}, "prompt_eval_count": 10, "eval_count": 50},
    )
    logger = logging.getLogger("test_model.predict_warn")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        model._ollama_chat([{"role": "user", "content": "hey"}], logger=logger)

    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "num_predict=50" in caplog.text and "cut off" in caplog.text


def test_logs_prompt_token_usage(monkeypatch, caplog):
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    _patch_post(
        monkeypatch, {},
        {"message": {"content": "hi"}, "prompt_eval_count": 123, "eval_count": 45},
    )
    logger = logging.getLogger("test_model.usage")

    with caplog.at_level(logging.INFO, logger=logger.name):
        model._ollama_chat([{"role": "user", "content": "hey"}], logger=logger)

    assert "prompt_tokens=123" in caplog.text
    assert "num_ctx=8192" in caplog.text


def test_warns_when_prompt_reaches_num_ctx(monkeypatch, caplog):
    monkeypatch.setenv("OLLAMA_NUM_CTX", "100")
    _patch_post(
        monkeypatch, {},
        {"message": {"content": "hi"}, "prompt_eval_count": 100, "eval_count": 5},
    )
    logger = logging.getLogger("test_model.warn")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        model._ollama_chat([{"role": "user", "content": "hey"}], logger=logger)

    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "likely truncated" in caplog.text


def test_no_logging_without_logger(monkeypatch, caplog):
    """A missing logger must not raise and must not emit records."""
    _patch_post(monkeypatch, {}, {"message": {"content": "hi"}, "prompt_eval_count": 10})

    with caplog.at_level(logging.INFO):
        model._ollama_chat([{"role": "user", "content": "hey"}])

    assert caplog.records == []


def test_stream_reassembles_content_across_chunks(monkeypatch):
    chunks = [
        {"message": {"content": "Hel"}},
        {"message": {"content": "lo"}},
        {"message": {}, "done": True, "prompt_eval_count": 5, "eval_count": 3},
    ]
    _patch_post_chunks(monkeypatch, {}, chunks)

    message = model._ollama_chat([{"role": "user", "content": "hey"}])

    assert message["content"] == "Hello"


def _patch_post_raising(monkeypatch, exc):
    def fake_post(url, json=None, timeout=None, stream=None):
        raise exc
    monkeypatch.setattr(model.requests, "post", fake_post)


def _patch_ps(monkeypatch, models=None, fail=False):
    """Stand in for the /api/ps probe _diagnose_stall makes."""
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": m} for m in (models or [])]}

    def fake_get(url, timeout=None):
        assert url.endswith("/api/ps")
        if fail:
            raise model.requests.exceptions.ConnectionError("refused")
        return _Resp()

    monkeypatch.setattr(model.requests, "get", fake_get)


def test_timeout_with_ollama_down_says_down(monkeypatch):
    _patch_post_raising(monkeypatch, model.requests.exceptions.ReadTimeout("timed out"))
    _patch_ps(monkeypatch, fail=True)

    with pytest.raises(model.OllamaUnavailable) as excinfo:
        model._ollama_chat([{"role": "user", "content": "hey"}])

    msg = str(excinfo.value)
    assert "looks down" in msg
    assert "busy" not in msg


def test_timeout_with_ollama_up_says_busy_and_names_model(monkeypatch):
    _patch_post_raising(monkeypatch, model.requests.exceptions.ReadTimeout("timed out"))
    _patch_ps(monkeypatch, models=["gemma4:26b-mlx"])

    with pytest.raises(model.OllamaUnavailable) as excinfo:
        model._ollama_chat([{"role": "user", "content": "hey"}])

    msg = str(excinfo.value)
    assert "is up" in msg
    assert "gemma4:26b-mlx" in msg
    assert "one request at a time" in msg
    assert "without producing any output" in msg


def test_timeout_after_partial_stream_says_mid_reply(monkeypatch):
    class _StallingResponse(_FakeResponse):
        def iter_lines(self):
            yield _json.dumps({"message": {"content": "par"}}).encode()
            raise model.requests.exceptions.ReadTimeout("timed out")

    monkeypatch.setattr(model.requests, "post",
                        lambda url, json=None, timeout=None, stream=None: _StallingResponse([]))
    _patch_ps(monkeypatch, models=["gemma4:26b-mlx"])

    with pytest.raises(model.OllamaUnavailable) as excinfo:
        model._ollama_chat([{"role": "user", "content": "hey"}])

    assert "mid-reply" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# warm_model
# --------------------------------------------------------------------------- #

class _WarmResponse:
    def __init__(self, ok=True):
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise model.requests.exceptions.HTTPError("boom")


def test_warm_model_loads_with_empty_messages(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_CTX", "16384")
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "30m")
    captured = {}

    def fake_post(url, json=None, timeout=None, stream=None):
        captured["url"] = url
        captured["payload"] = json
        captured["timeout"] = timeout
        return _WarmResponse(ok=True)

    monkeypatch.setattr(model.requests, "post", fake_post)

    assert model.warm_model() is True
    assert captured["url"].endswith("/api/chat")
    assert captured["payload"]["messages"] == []
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["options"]["num_ctx"] == 16384
    assert captured["payload"]["keep_alive"] == "30m"


def test_warm_model_uses_warm_timeout_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_WARM_TIMEOUT", "42")
    captured = {}
    monkeypatch.setattr(
        model.requests, "post",
        lambda url, json=None, timeout=None, stream=None: captured.update(timeout=timeout)
        or _WarmResponse(ok=True),
    )

    model.warm_model()

    assert captured["timeout"] == 42.0


def test_warm_model_degrades_on_failure(monkeypatch):
    def boom(*a, **k):
        raise model.requests.exceptions.ConnectionError("no server")

    monkeypatch.setattr(model.requests, "post", boom)

    assert model.warm_model() is False


def test_warm_model_noop_for_cloud_backend(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Ollama must not be contacted for a cloud backend")
    monkeypatch.setattr(model.requests, "post", boom)

    assert model.warm_model(backend="gemini") is True


# --------------------------------------------------------------------------- #
# think markup stripping
# --------------------------------------------------------------------------- #

def test_orphan_closing_think_tag_is_stripped_so_strict_parsers_survive(monkeypatch):
    monkeypatch.setattr(model, "_ollama_chat",
                        lambda messages, **kwargs: {"content": '{"1": "7"}\n</think>'})

    out = model.complete_text("sys", "user")

    assert out == '{"1": "7"}'
    assert _json.loads(out) == {"1": "7"}


def test_a_matched_think_block_is_dropped_and_the_answer_kept(monkeypatch):
    _llm_returning("<think>weighing it up\nstill weighing</think>\nthe answer", monkeypatch)

    assert model.complete_text("sys", "user") == "the answer"


def test_a_reply_that_is_only_scratchpad_reads_as_empty(monkeypatch):
    # Not a regression: stripping must not manufacture an answer out of
    # reasoning. Empty is what the num_predict warning is there to explain.
    _llm_returning("<think>never got to the point</think>", monkeypatch)

    assert model.complete_text("sys", "user") == ""


def test_content_without_think_markup_keeps_its_exact_whitespace(monkeypatch):
    monkeypatch.setattr(model, "_ollama_chat",
                        lambda messages, **kwargs: {"content": "  line\n\n  next  "})

    message = model._llm_chat([{"role": "user", "content": "hi"}])

    assert message["content"] == "  line\n\n  next  "


def test_missing_content_does_not_crash_the_seam(monkeypatch):
    monkeypatch.setattr(model, "_ollama_chat",
                        lambda messages, **kwargs: {"role": "assistant"})

    assert model._llm_chat([{"role": "user", "content": "hi"}])["content"] == ""


# --------------------------------------------------------------------------- #
# backend() / log_backend() — ScribeJay's own SCRIBEJAY_* precedence chain
# --------------------------------------------------------------------------- #

def test_backend_precedence(monkeypatch):
    monkeypatch.delenv("SCRIBEJAY_LLM_BACKEND", raising=False)
    monkeypatch.delenv("SCRIBEJAY_DAILY_CHROME_LEARNINGS_BACKEND", raising=False)
    assert model.backend("daily_chrome_learnings") is None

    monkeypatch.setenv("SCRIBEJAY_LLM_BACKEND", "ollama")
    assert model.backend("daily_chrome_learnings") == "ollama"

    # per-task var wins over the global default
    monkeypatch.setenv("SCRIBEJAY_DAILY_CHROME_LEARNINGS_BACKEND", "gemini")
    assert model.backend("daily_chrome_learnings") == "gemini"


def test_backend_never_falls_back_to_a_wren_variable(monkeypatch):
    # A silent WREN_* fallback would hide a missed .env setup — see the
    # module docstring. WREN_LLM_BACKEND must be invisible here.
    monkeypatch.delenv("SCRIBEJAY_LLM_BACKEND", raising=False)
    monkeypatch.setenv("WREN_LLM_BACKEND", "gemini")
    assert model.backend("daily_commits") is None


def test_log_backend_names_the_source(caplog):
    logger = logging.getLogger("test_model.log_backend")
    with caplog.at_level(logging.INFO, logger=logger.name):
        model.log_backend(logger, "daily_commits", None)
    assert "backend: ollama (default)" in caplog.text
    assert "from unset" in caplog.text


def test_log_backend_names_the_per_task_var(monkeypatch, caplog):
    monkeypatch.setenv("SCRIBEJAY_DAILY_COMMITS_BACKEND", "gemini")
    logger = logging.getLogger("test_model.log_backend2")
    with caplog.at_level(logging.INFO, logger=logger.name):
        model.log_backend(logger, "daily_commits", "gemini")
    assert "backend: gemini" in caplog.text
    assert "SCRIBEJAY_DAILY_COMMITS_BACKEND" in caplog.text


# --------------------------------------------------------------------------- #
# _llm_chat dispatch
# --------------------------------------------------------------------------- #

def test_llm_chat_dispatch_and_unknown_backend(monkeypatch):
    monkeypatch.delenv("SCRIBEJAY_LLM_BACKEND", raising=False)
    monkeypatch.setattr(model, "_gemini_chat", lambda messages, **kwargs: {"content": "cloud"})
    monkeypatch.setattr(model, "_openrouter_chat",
                        lambda messages, **kwargs: {"content": "router"})

    assert model._llm_chat([{"role": "user", "content": "hi"}],
                           backend="gemini")["content"] == "cloud"
    assert model._llm_chat([{"role": "user", "content": "hi"}],
                           backend="openrouter")["content"] == "router"

    monkeypatch.setenv("SCRIBEJAY_LLM_BACKEND", "nonsense")
    with pytest.raises(ValueError):
        model._llm_chat([{"role": "user", "content": "hi"}])


def test_openrouter_is_reached_through_the_settings_chain(monkeypatch):
    # The per-task and global variables are what a settings screen writes, so
    # pin that they actually route here — not just the explicit argument.
    monkeypatch.setattr(model, "_openrouter_chat",
                        lambda messages, **kwargs: {"content": "router"})
    monkeypatch.setenv("SCRIBEJAY_LLM_BACKEND", "openrouter")

    assert model.backend("daily_commits") == "openrouter"
    assert model._llm_chat([{"role": "user", "content": "hi"}])["content"] == "router"


# --------------------------------------------------------------------------- #
# with_identity — ScribeJay's own persona, deliberately not Wren's
# --------------------------------------------------------------------------- #

def test_with_identity_joins_persona_and_system_prompt(monkeypatch):
    monkeypatch.setattr(model, "SCRIBEJAY_PERSONA", "You are ScribeJay.")
    assert model.with_identity("Write a summary.") == "You are ScribeJay.\n\n---\n\nWrite a summary."


def test_with_identity_degrades_when_persona_is_empty(monkeypatch):
    monkeypatch.setattr(model, "SCRIBEJAY_PERSONA", "")
    assert model.with_identity("Write a summary.") == "Write a summary."


# --------------------------------------------------------------------------- #
# LaTeX in prose. gemma4:26b-mlx writes `$\rightarrow$` where it means →, and a
# Markdown vault page does not render math — the raw markup lands in the written
# record. The regression that matters is money: two prices in one sentence must
# not read as a math span.
# --------------------------------------------------------------------------- #

def test_dollar_wrapped_latex_becomes_the_character(monkeypatch):
    _llm_returning(r"Initial Contact $\rightarrow$ Discovery Call", monkeypatch)

    assert model.complete_text("sys", "user") == "Initial Contact → Discovery Call"


def test_money_on_both_sides_of_prose_is_left_alone(monkeypatch):
    # The whole reason the rule demands a backslash: without one, "$5 to $10"
    # looks exactly like a math span and the words between the prices vanish.
    _llm_returning("They raised $5 to $10 million last year.", monkeypatch)

    assert model.complete_text("sys", "user") == "They raised $5 to $10 million last year."


def test_a_bare_command_converts_too(monkeypatch):
    _llm_returning(r"scored 8 \times faster, \geq the target", monkeypatch)

    assert model.complete_text("sys", "user") == "scored 8 × faster, ≥ the target"


def test_a_longer_command_is_not_matched_as_a_shorter_one(monkeypatch):
    _llm_returning(r"$\leftrightarrow$ and $\leq$", monkeypatch)

    assert model.complete_text("sys", "user") == "↔ and ≤"


def test_a_word_that_starts_with_a_command_name_survives(monkeypatch):
    _llm_returning(r"the \total came to \gets", monkeypatch)

    assert model.complete_text("sys", "user") == r"the \total came to ←"


def test_math_without_a_backslash_is_left_alone(monkeypatch):
    # Deliberately out of scope: no backslash means no safe way to tell this
    # from a pair of dollar amounts.
    _llm_returning("with $n=2$ samples", monkeypatch)

    assert model.complete_text("sys", "user") == "with $n=2$ samples"


def test_an_unlisted_command_is_left_alone(monkeypatch):
    _llm_returning(r"C:\Programs and $\alpha$", monkeypatch)

    assert model.complete_text("sys", "user") == r"C:\Programs and $\alpha$"


def test_think_markup_and_latex_are_both_applied(monkeypatch):
    # Ordering guard: _strip_think_markup runs first, _latex_to_unicode second,
    # and a reply carrying both must come out clean on both counts.
    _llm_returning("<think>scratch</think>Lead $\\rightarrow$ Proposal.", monkeypatch)

    assert model.complete_text("sys", "user") == "Lead → Proposal."
