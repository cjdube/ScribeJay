"""Tests for scribejay/core/backends/gemini.py — the cloud backend behind
core.model's _llm_chat seam.

Mirrors the no-tools slice of LocalLLMAgent's gemini tests in tests/test_loop.py.
Dropped every tool-calling/thought-signature test: this module never sends
tools and has no should_cancel/TurnCancelled path (see its module docstring —
ScribeJay always sends one system+user turn)."""

from scribejay.core.backends import gemini as gemini_backend


class _FakePart:
    def __init__(self, text=None):
        self.text = text


class _FakeUsage:
    def __init__(self, prompt, out):
        self.prompt_token_count = prompt
        self.candidates_token_count = out


class _FakeChunk:
    def __init__(self, parts=None, usage=None):
        cand = type("C", (), {"content": type("Ct", (), {"parts": parts or []})(),
                              "finish_reason": None})()
        self.candidates = [cand]
        self.usage_metadata = usage


class _FakeGeminiClient:
    """Returns one canned chunk-stream and records the (contents, config) it
    was handed for assertions."""
    def __init__(self, response, captured):
        self._response = response
        self._captured = captured
        outer = self

        class _Models:
            def generate_content_stream(self, model=None, contents=None, config=None):
                outer._captured.setdefault("calls", []).append(
                    {"model": model, "contents": contents, "config": config})
                return iter(outer._response)

        self.models = _Models()


def _patch_gemini(monkeypatch, response, captured=None):
    captured = captured if captured is not None else {}
    client = _FakeGeminiClient(response, captured)
    monkeypatch.setattr(gemini_backend, "_gemini_client", lambda timeout=None: client)
    return captured


def test_gemini_backend_reassembles_text_across_chunks(monkeypatch):
    stream = [
        _FakeChunk(parts=[_FakePart(text="Hel")]),
        _FakeChunk(parts=[_FakePart(text="lo")], usage=_FakeUsage(5, 3)),
    ]
    _patch_gemini(monkeypatch, stream)

    message = gemini_backend._gemini_chat([{"role": "user", "content": "hey"}])

    assert message["role"] == "assistant"
    assert message["content"] == "Hello"


def test_gemini_disables_thinking_and_sets_output_cap_by_default(monkeypatch):
    # Thinking is a *thinking* model's default, and thinking tokens count
    # against the output cap — leaving it on can starve a draft entirely.
    # Default to budget 0 (off) with generous output headroom.
    monkeypatch.delenv("SCRIBEJAY_GEMINI_THINKING_BUDGET", raising=False)
    monkeypatch.delenv("SCRIBEJAY_GEMINI_MAX_OUTPUT_TOKENS", raising=False)
    captured = _patch_gemini(monkeypatch, [_FakeChunk(parts=[_FakePart(text="ok")])])

    gemini_backend._gemini_chat([{"role": "user", "content": "hey"}])

    config = captured["calls"][0]["config"]
    assert config.thinking_config.thinking_budget == 0
    assert config.max_output_tokens == 8192


def test_gemini_thinking_budget_and_cap_are_env_overridable(monkeypatch):
    monkeypatch.setenv("SCRIBEJAY_GEMINI_THINKING_BUDGET", "1024")
    monkeypatch.setenv("SCRIBEJAY_GEMINI_MAX_OUTPUT_TOKENS", "2048")
    captured = _patch_gemini(monkeypatch, [_FakeChunk(parts=[_FakePart(text="ok")])])

    gemini_backend._gemini_chat([{"role": "user", "content": "hey"}])

    config = captured["calls"][0]["config"]
    assert config.thinking_config.thinking_budget == 1024
    assert config.max_output_tokens == 2048


def test_gemini_hoists_the_system_prompt_out_of_contents(monkeypatch):
    captured = _patch_gemini(monkeypatch, [_FakeChunk(parts=[_FakePart(text="ok")])])
    messages = [
        {"role": "system", "content": "You are ScribeJay."},
        {"role": "user", "content": "Summarize the day."},
    ]

    gemini_backend._gemini_chat(messages)

    config = captured["calls"][0]["config"]
    assert config.system_instruction == "You are ScribeJay."
    contents = captured["calls"][0]["contents"]
    assert len(contents) == 1
    assert contents[0].role == "user"


def test_gemini_model_defaults_and_is_env_overridable(monkeypatch):
    monkeypatch.delenv("SCRIBEJAY_GEMINI_MODEL", raising=False)
    captured = _patch_gemini(monkeypatch, [_FakeChunk(parts=[_FakePart(text="ok")])])
    gemini_backend._gemini_chat([{"role": "user", "content": "hey"}])
    assert captured["calls"][0]["model"] == gemini_backend.GEMINI_DEFAULT_MODEL

    monkeypatch.setenv("SCRIBEJAY_GEMINI_MODEL", "gemini-3.6-flash")
    captured2 = _patch_gemini(monkeypatch, [_FakeChunk(parts=[_FakePart(text="ok")])])
    gemini_backend._gemini_chat([{"role": "user", "content": "hey"}])
    assert captured2["calls"][0]["model"] == "gemini-3.6-flash"


def test_gemini_attaches_usage_for_the_ledger(monkeypatch):
    """The private key core/model.py:_llm_chat pops off and records."""
    usage = _FakeUsage(5, 3)
    usage.thoughts_token_count = 11
    chunk = _FakeChunk(parts=[_FakePart(text="Hi")], usage=usage)
    chunk.candidates[0].finish_reason = "MAX_TOKENS"
    _patch_gemini(monkeypatch, [chunk])

    message = gemini_backend._gemini_chat([{"role": "user", "content": "hey"}])

    assert message["_usage"] == {
        "model": "gemini-2.5-flash",
        "prompt_tokens": 5,
        "output_tokens": 3,
        "thinking_tokens": 11,
        "finish_reason": "MAX_TOKENS",
    }
