"""Centralized usage-limit/quota-exhaustion detection shared by every
ModelCaller backend (`claude_cli_caller.ClaudeCLICaller`,
`anthropic_caller.AnthropicCaller`, `vertex_caller.VertexCaller`) -- issue
#198.

Today, a `claude -p` usage-limit exhaustion and a genuine per-chunk
content/tooling failure both surface as the same generic `RuntimeError`
(`` `claude -p` exited 1: ... ``), which forces a human (or an orchestrating
agent) to notice "everything failed at once" by eye before correctly
triaging it as quota rather than blindly retrying a content defect. This
module gives every caller one place to recognize the condition and raise a
distinct, structured `QuotaExhaustedError` instead -- so a wrapping
orchestrator (batch.py's per-chunk/per-member exception handling already
logs `exc.__class__.__name__`, so this alone makes the condition
grep-able/detectable in logs without changing batch.py itself) can act on it
later. Deliberately narrow in scope: *detecting and labelling* the
condition is all this module does -- actually pausing/resuming a run across
a usage-limit reset is a separate, wrapping-orchestrator concern (see issue
#198's "out of scope").
"""

from __future__ import annotations

import re

# Claude Code's own error-classification regex (confirmed by extracting
# strings from the `claude` CLI binary itself, v2.1.268) matches
# "usage limit reached" and "credit balance (is )?too low" case-insensitively
# against subprocess/API output text to recognize exactly this condition.
# Reusing the same substrings here keeps `mfdoc`'s detection aligned with
# what Claude Code itself already treats as authoritative, rather than
# inventing a competing heuristic against text nobody has confirmed is
# stable.
_QUOTA_TEXT_PATTERN = re.compile(
    r"usage limit reached|credit balance (?:is )?too low", re.IGNORECASE,
)


class QuotaExhaustedError(RuntimeError):
    """Raised by a ModelCaller in place of a generic failure when the
    underlying subprocess/API call failed specifically because of
    usage-limit/quota exhaustion (Claude Code's 5-hour/weekly usage limit
    for `--provider claude-code`, or an Anthropic API key's rate limit /
    billing error for `--provider anthropic`/`vertex`) -- not a genuine
    per-chunk content or tooling failure.

    Deliberately a `RuntimeError` subclass rather than an unrelated new
    hierarchy: every existing `except RuntimeError`/`except Exception`
    (batch.py's per-chunk/per-member isolation, the CLI's own error
    reporting, ...) still catches this exactly as it catches today's
    generic failure, unless a caller specifically wants to distinguish it
    (e.g. `except QuotaExhaustedError` first) -- adding this type doesn't
    require touching every existing call site.

    `detail`, if given, is the original failure's raw text (subprocess
    stderr/result, or the API error message) -- preserved so nothing is
    lost relative to the generic RuntimeError this replaces.
    """

    def __init__(self, message: str, *, detail: str | None = None):
        super().__init__(message)
        self.detail = detail


def is_quota_exhaustion_text(*texts: str | None) -> bool:
    """True if any of `texts` (subprocess stderr, a `claude -p` JSON
    `result` string, an API error's message, ...) contains a recognizable
    usage-limit/quota-exhaustion substring. `None`/empty entries are
    skipped, so callers can pass optional fields (e.g. `proc.stderr`, which
    may be empty) without checking first."""
    return any(text and _QUOTA_TEXT_PATTERN.search(text) for text in texts)


def is_quota_exhaustion_error(exc: Exception) -> bool:
    """True if `exc` -- an exception raised by an Anthropic-SDK-backed
    ModelCaller (`AnthropicCaller`/`VertexCaller`), after
    `retry.call_with_retry` has already given up on it -- looks like
    usage-limit/quota exhaustion rather than a generic failure.

    `anthropic.APIStatusError` (and its subclasses, including
    `RateLimitError`) carries a `type` attribute lifted from the response
    body's `error.type` field (confirmed against the installed `anthropic`
    SDK's `_exceptions.py`): `"billing_error"` is Anthropic's own label for
    a credit-balance/quota problem, distinct from `"rate_limit_error"`'s
    generic 429 throttling. But a 429 that survives every one of
    AnthropicCaller's/VertexCaller's configured retries is exactly the same
    "everything failed at once" pattern issue #198 describes -- ordinary
    transient throttling recovers within the retry budget; only a real
    quota/rate ceiling keeps failing past it -- so `status_code == 429`
    counts too, not only `billing_error`.

    Falls back to matching the exception's own text (`is_quota_exhaustion_
    text`) for anything else -- an SDK exception shape that doesn't expose
    `type`/`status_code` (e.g. these tests' fakes, or a future SDK version),
    or a caller (like `ClaudeCLICaller`) whose failures are plain strings
    with no structured attributes at all.
    """
    if getattr(exc, "type", None) == "billing_error":
        return True
    if getattr(exc, "status_code", None) == 429:
        return True
    return is_quota_exhaustion_text(str(exc))
