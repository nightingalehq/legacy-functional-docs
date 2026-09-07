"""Guards for AnthropicCaller's error-path contract, its transient-error
retry/backoff (#79), and its configurable request timeout (#80) -- no real
network call or API key is exercised here, same isolation approach as
test_vertex_caller.py: fake the `anthropic` module entirely via sys.modules
so nothing needs the real package installed to run these."""

from __future__ import annotations

import sys
import types

import pytest

from mfdoc.anthropic_caller import DEFAULT_TIMEOUT_S, AnthropicCaller


class _FakeRateLimitError(Exception):
    pass


class _FakeAPIConnectionError(Exception):
    pass


class _FakeInternalServerError(Exception):
    pass


class _FakeBadRequestError(Exception):
    """Stand-in for a real, non-retryable 4xx error."""


def _fake_anthropic_module(create_fn):
    messages = types.SimpleNamespace(create=create_fn)
    client = types.SimpleNamespace(messages=messages)
    module = types.SimpleNamespace(
        Anthropic=lambda api_key=None, timeout=None: client,
        RateLimitError=_FakeRateLimitError,
        APIConnectionError=_FakeAPIConnectionError,
        InternalServerError=_FakeInternalServerError,
    )
    return module, client


def _fake_message(text="a response"):
    block = types.SimpleNamespace(type="text", text=text)
    usage = types.SimpleNamespace(input_tokens=10, output_tokens=20)
    return types.SimpleNamespace(content=[block], usage=usage)


def test_missing_anthropic_package_raises_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(RuntimeError, match=r"pip install 'mfdoc\[batch\]'"):
        AnthropicCaller()


def test_succeeds_without_retrying_when_the_call_succeeds_first_try(monkeypatch):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        return _fake_message()

    module, _ = _fake_anthropic_module(create)
    monkeypatch.setitem(sys.modules, "anthropic", module)

    caller = AnthropicCaller()
    result = caller("some prompt")
    assert result.text == "a response"
    assert calls["n"] == 1


def test_retries_a_transient_error_and_eventually_succeeds(monkeypatch):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeRateLimitError("simulated 429")
        return _fake_message()

    module, _ = _fake_anthropic_module(create)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setattr("mfdoc.retry.time.sleep", lambda s: None)

    caller = AnthropicCaller(max_retries=5)
    result = caller("some prompt")
    assert result.text == "a response"
    assert calls["n"] == 3


def test_gives_up_after_max_retries_on_a_persistent_transient_error(monkeypatch):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        raise _FakeInternalServerError("simulated persistent 500")

    module, _ = _fake_anthropic_module(create)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setattr("mfdoc.retry.time.sleep", lambda s: None)

    caller = AnthropicCaller(max_retries=2)
    with pytest.raises(_FakeInternalServerError):
        caller("some prompt")
    assert calls["n"] == 3  # initial attempt + 2 retries


def test_a_non_retryable_error_propagates_without_retrying(monkeypatch):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        raise _FakeBadRequestError("simulated 400, never retry this")

    module, _ = _fake_anthropic_module(create)
    monkeypatch.setitem(sys.modules, "anthropic", module)

    caller = AnthropicCaller(max_retries=5)
    with pytest.raises(_FakeBadRequestError):
        caller("some prompt")
    assert calls["n"] == 1


def test_default_timeout_is_passed_to_the_anthropic_client(monkeypatch):
    constructed = {}

    def fake_anthropic_ctor(**kwargs):
        constructed.update(kwargs)
        return types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: None))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(
        Anthropic=fake_anthropic_ctor,
        RateLimitError=_FakeRateLimitError,
        APIConnectionError=_FakeAPIConnectionError,
        InternalServerError=_FakeInternalServerError,
    ))

    caller = AnthropicCaller()
    assert caller.timeout == DEFAULT_TIMEOUT_S == 600
    assert constructed["timeout"] == DEFAULT_TIMEOUT_S


def test_explicit_timeout_overrides_the_default(monkeypatch):
    constructed = {}

    def fake_anthropic_ctor(**kwargs):
        constructed.update(kwargs)
        return types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: None))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(
        Anthropic=fake_anthropic_ctor,
        RateLimitError=_FakeRateLimitError,
        APIConnectionError=_FakeAPIConnectionError,
        InternalServerError=_FakeInternalServerError,
    ))

    caller = AnthropicCaller(timeout=30)
    assert caller.timeout == 30
    assert constructed["timeout"] == 30


def test_explicit_timeout_is_passed_alongside_an_api_key(monkeypatch):
    constructed = {}

    def fake_anthropic_ctor(**kwargs):
        constructed.update(kwargs)
        return types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: None))

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(
        Anthropic=fake_anthropic_ctor,
        RateLimitError=_FakeRateLimitError,
        APIConnectionError=_FakeAPIConnectionError,
        InternalServerError=_FakeInternalServerError,
    ))

    AnthropicCaller(api_key="sk-test", timeout=45)
    assert constructed == {"api_key": "sk-test", "timeout": 45}
