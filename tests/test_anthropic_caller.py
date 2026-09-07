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
    # issue #84: a first-try success reports zero transient retries.
    assert result.retries == 0


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
    # issue #84: 2 failed attempts before the 3rd (successful) one -> 2 retries.
    assert result.retries == 2


def test_retry_count_stays_correct_when_two_calls_actually_run_concurrently(monkeypatch):
    """`ModelResponse.retries` must reflect only the one `__call__` it came
    from -- not a shared instance attribute that a second, concurrent call
    from another thread could stomp on before the first call reads it back
    (issue #84's design note on why `retries` is tracked via a local
    closure variable inside `__call__`, not `self.something`).

    Genuinely runs both calls concurrently (via a real ThreadPoolExecutor),
    with events forcing call A's retry to be "in flight" (its `on_retry` has
    already fired, as it would with a shared `self.retries` attribute)
    at the exact moment call B executes and returns -- rather than two
    calls made sequentially, which would pass even against a *broken*,
    shared-attribute implementation and so wouldn't actually catch the
    regression this test exists to guard against."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    a_failed_once = threading.Event()
    b_done = threading.Event()
    calls = {"A": 0, "B": 0}

    def create(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        if prompt == "prompt A":
            calls["A"] += 1
            if calls["A"] == 1:
                a_failed_once.set()
                assert b_done.wait(timeout=5), "call B never completed"
                raise _FakeRateLimitError("simulated 429 on A's first attempt")
            return _fake_message()
        assert a_failed_once.wait(timeout=5), "call A never reached its first (failing) attempt"
        calls["B"] += 1
        response = _fake_message()
        b_done.set()
        return response

    module, _ = _fake_anthropic_module(create)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setattr("mfdoc.retry.time.sleep", lambda s: None)

    caller = AnthropicCaller(max_retries=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(caller, "prompt A")
        fut_b = pool.submit(caller, "prompt B")
        first = fut_a.result(timeout=5)
        second = fut_b.result(timeout=5)

    assert first.retries == 1  # needed one retry
    assert second.retries == 0  # first-try success, unaffected by A's in-flight retry


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
