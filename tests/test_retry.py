"""Guards for the generic transient-error retry/backoff helper (#79)."""

from __future__ import annotations

import pytest

from mfdoc.retry import call_with_retry


class _Transient(Exception):
    pass


class _Permanent(Exception):
    pass


def test_succeeds_immediately_when_the_first_call_succeeds():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    result = call_with_retry(fn, is_retryable=lambda exc: True, sleep=lambda s: None)
    assert result == "ok"
    assert len(calls) == 1


def test_retries_a_transient_error_and_eventually_succeeds():
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _Transient("simulated rate limit")
        return "ok"

    sleeps: list[float] = []
    result = call_with_retry(
        fn, is_retryable=lambda exc: isinstance(exc, _Transient),
        max_retries=5, sleep=sleeps.append,
    )
    assert result == "ok"
    assert attempts["n"] == 3
    assert len(sleeps) == 2  # one sleep between each of the two failed attempts and the next


def test_gives_up_and_raises_after_max_retries_exhausted():
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise _Transient("always fails")

    with pytest.raises(_Transient):
        call_with_retry(
            fn, is_retryable=lambda exc: True, max_retries=2, sleep=lambda s: None,
        )
    assert attempts["n"] == 3  # the initial attempt plus 2 retries, no more


def test_a_non_retryable_error_propagates_immediately_without_retrying():
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise _Permanent("bad request, never retry this")

    with pytest.raises(_Permanent):
        call_with_retry(
            fn, is_retryable=lambda exc: isinstance(exc, _Transient),
            max_retries=5, sleep=lambda s: pytest.fail("must not sleep/retry a non-retryable error"),
        )
    assert attempts["n"] == 1


def test_backoff_delay_grows_exponentially_and_is_capped():
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        raise _Transient("always fails")

    sleeps: list[float] = []
    with pytest.raises(_Transient):
        call_with_retry(
            fn, is_retryable=lambda exc: True, max_retries=4,
            base_delay=1.0, max_delay=3.0, sleep=sleeps.append,
        )
    assert len(sleeps) == 4
    # Uncapped exponential would be 1, 2, 4, 8 -- with 50%-100% jitter and a
    # max_delay=3.0 cap, every actual delay must still respect both bounds.
    expected_uncapped = [1.0, 2.0, 4.0, 8.0]
    for observed, uncapped in zip(sleeps, expected_uncapped):
        ceiling = min(uncapped, 3.0)
        assert ceiling * 0.5 <= observed <= ceiling


def test_on_retry_callback_fires_once_per_retry_with_the_1_based_attempt_number():
    """issue #84's retry-count plumbing: `on_retry(attempt, exc)` is the hook
    a caller (AnthropicCaller/VertexCaller) uses to learn how many retries
    `call_with_retry` actually took, without this function's own return
    value changing. Must fire once per retry actually taken -- not for the
    initial attempt, and not for the final exhausted attempt that re-raises
    instead of retrying."""
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _Transient("simulated rate limit")
        return "ok"

    seen: list[tuple[int, Exception]] = []
    result = call_with_retry(
        fn, is_retryable=lambda exc: True, max_retries=5, sleep=lambda s: None,
        on_retry=lambda attempt, exc: seen.append((attempt, exc)),
    )
    assert result == "ok"
    assert [a for a, _ in seen] == [1, 2]
    assert all(isinstance(exc, _Transient) for _, exc in seen)


def test_on_retry_callback_never_fires_for_the_final_exhausted_attempt():
    def fn():
        raise _Transient("always fails")

    seen: list[int] = []
    with pytest.raises(_Transient):
        call_with_retry(
            fn, is_retryable=lambda exc: True, max_retries=2, sleep=lambda s: None,
            on_retry=lambda attempt, exc: seen.append(attempt),
        )
    # 2 retries permitted -> on_retry fires for attempt 1 and 2, then the
    # 3rd (final) attempt raises without a further retry/on_retry call.
    assert seen == [1, 2]


def test_on_retry_callback_never_fires_when_the_first_call_succeeds():
    def fn():
        return "ok"

    seen: list[int] = []
    result = call_with_retry(
        fn, is_retryable=lambda exc: True, sleep=lambda s: None,
        on_retry=lambda attempt, exc: seen.append(attempt),
    )
    assert result == "ok"
    assert seen == []


def test_negative_max_retries_raises_value_error_before_any_attempt():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    with pytest.raises(ValueError, match="max_retries"):
        call_with_retry(fn, is_retryable=lambda exc: True, max_retries=-1, sleep=lambda s: None)
    assert calls == []


def test_negative_base_delay_raises_value_error_before_any_attempt():
    with pytest.raises(ValueError, match="base_delay"):
        call_with_retry(lambda: "ok", is_retryable=lambda exc: True, base_delay=-1.0, sleep=lambda s: None)


def test_negative_max_delay_raises_value_error_before_any_attempt():
    with pytest.raises(ValueError, match="max_delay"):
        call_with_retry(lambda: "ok", is_retryable=lambda exc: True, max_delay=-1.0, sleep=lambda s: None)
