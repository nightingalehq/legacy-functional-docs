"""Small, dependency-free bounded exponential backoff for transient
ModelCaller errors (rate limits, connection resets, 5xx) -- issue #79.

`ClaudeCLICaller` (claude_cli_caller.py) already turns a hung/failed `claude
-p` subprocess into a clear `RuntimeError`, but neither `AnthropicCaller` nor
`VertexCaller` retried on a transient API error before this -- every one
became a hard failure that propagated straight up into `run_batch`'s
per-member isolation (see issue #78), forcing a whole member to be marked
failed and re-run from scratch for something a short wait would have
resolved. This is deliberately generic (no dependency on `anthropic`'s own
exception types) so both callers -- and anything else that ever needs the
same shape of retry -- can reuse it, each supplying its own `is_retryable`
predicate for what "transient" means to it.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY_S = 1.0
DEFAULT_MAX_DELAY_S = 20.0

# One logger shared by every ModelCaller that delegates to this helper
# (AnthropicCaller, VertexCaller) -- retries here are exactly the kind of
# long-running-batch diagnostic noise issue #83 exists to move off stdout:
# real signal (a rate limit or transient network error being backed off,
# not silently swallowed) that a caller watching a multi-hour `mfdoc batch`
# run needs visibility into, without it competing with the batch harness's
# own progress reporting.
logger = logging.getLogger("mfdoc.retry")


def call_with_retry(
    fn: Callable[[], T],
    is_retryable: Callable[[Exception], bool],
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY_S,
    max_delay: float = DEFAULT_MAX_DELAY_S,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Call `fn()`, retrying up to `max_retries` times (so at most
    `max_retries + 1` attempts total) whenever the raised exception is one
    `is_retryable` accepts as transient. Delay before the Nth retry grows
    exponentially (`base_delay * 2**(N-1)`, capped at `max_delay`), then has
    50%-100% jitter applied (never lengthened beyond the capped value, only
    shortened), so many concurrent workers hitting the same rate limit don't
    all retry in lockstep. A non-retryable exception, or the last permitted
    attempt's exception, propagates immediately and normally -- this never
    swallows a genuine failure, only defers it past a bounded number of
    transient-looking ones.

    `sleep` is injectable (defaults to `time.sleep`, looked up at call time
    rather than bound as a literal default value, so a test can monkeypatch
    `mfdoc.retry.time.sleep` and have it actually take effect) purely so
    tests can assert on backoff timing/attempt counts without a real test
    suite run taking `max_retries` seconds per case.

    Raises `ValueError` immediately, before any attempt, for a negative
    `max_retries`, `base_delay`, or `max_delay` -- a negative `max_retries`
    would silently disable retries with no signal to the caller, and a
    negative delay only surfaces later as a confusing `time.sleep`
    `ValueError` deep inside the first retry.
    """
    if max_retries < 0:
        raise ValueError(f"max_retries must be >= 0, got {max_retries!r}")
    if base_delay < 0:
        raise ValueError(f"base_delay must be >= 0, got {base_delay!r}")
    if max_delay < 0:
        raise ValueError(f"max_delay must be >= 0, got {max_delay!r}")
    do_sleep = sleep if sleep is not None else time.sleep
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            attempt += 1
            if attempt > max_retries or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay *= 0.5 + random.random() / 2  # jitter: 50%-100% of the computed delay
            logger.warning(
                "transient error on attempt %d/%d, retrying in %.1fs: %s: %s",
                attempt, max_retries + 1, delay, exc.__class__.__name__, exc,
            )
            do_sleep(delay)
