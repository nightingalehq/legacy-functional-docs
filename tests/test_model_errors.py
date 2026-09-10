"""Guards for model_errors.py's shared quota-exhaustion detection (issue
#198) -- the logic every ModelCaller backend (claude-cli, Anthropic,
Vertex) delegates to, tested here in isolation from any particular
caller's transport."""

from __future__ import annotations

from mfdoc.model_errors import (
    QuotaExhaustedError,
    is_quota_exhaustion_error,
    is_quota_exhaustion_text,
)


def test_is_quota_exhaustion_text_matches_usage_limit_reached():
    assert is_quota_exhaustion_text("Claude AI usage limit reached|1234567890")


def test_is_quota_exhaustion_text_matches_case_insensitively():
    assert is_quota_exhaustion_text("USAGE LIMIT REACHED, try again later")


def test_is_quota_exhaustion_text_matches_credit_balance_too_low():
    assert is_quota_exhaustion_text("Error: credit balance too low")
    assert is_quota_exhaustion_text("Error: your credit balance is too low")


def test_is_quota_exhaustion_text_false_for_a_genuine_failure():
    assert not is_quota_exhaustion_text("SyntaxError: unexpected token in generated output")


def test_is_quota_exhaustion_text_skips_none_and_empty_entries():
    """Callers pass optional fields (e.g. `proc.stderr`, which may be
    empty) without checking first -- must not raise on None/empty."""
    assert not is_quota_exhaustion_text(None, "", None)
    assert is_quota_exhaustion_text(None, "usage limit reached", "")


def test_is_quota_exhaustion_error_true_for_billing_error_type():
    class _Exc(Exception):
        pass

    exc = _Exc("payment required")
    exc.type = "billing_error"
    assert is_quota_exhaustion_error(exc)


def test_is_quota_exhaustion_error_true_for_status_429():
    class _Exc(Exception):
        pass

    exc = _Exc("rate limited")
    exc.status_code = 429
    assert is_quota_exhaustion_error(exc)


def test_is_quota_exhaustion_error_false_for_an_unrelated_5xx():
    class _Exc(Exception):
        pass

    exc = _Exc("internal server error")
    exc.status_code = 500
    assert not is_quota_exhaustion_error(exc)


def test_is_quota_exhaustion_error_falls_back_to_text_match():
    """An exception shape with neither `type` nor `status_code` (e.g. a
    plain RuntimeError re-raised from a subprocess) still gets caught via
    its own text."""
    assert is_quota_exhaustion_error(RuntimeError("usage limit reached"))
    assert not is_quota_exhaustion_error(RuntimeError("boom"))


def test_quota_exhausted_error_is_a_runtime_error_and_carries_detail():
    """A RuntimeError subclass -- not an unrelated hierarchy -- so every
    existing `except RuntimeError`/`except Exception` still catches it
    unless a caller specifically wants to distinguish it."""
    exc = QuotaExhaustedError("usage limit exhausted", detail="raw detail text")
    assert isinstance(exc, RuntimeError)
    assert exc.detail == "raw detail text"
    assert str(exc) == "usage limit exhausted"
