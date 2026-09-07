"""Anthropic-backed ModelCaller for `mfdoc batch`.

Isolated in its own module so `anthropic` stays an optional dependency --
importing mfdoc.batch, or running everything except `mfdoc batch`, never
requires it installed.
"""

from __future__ import annotations

from .batch import ModelResponse, model_response_from_message
from .retry import DEFAULT_MAX_RETRIES, call_with_retry

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 8192
# Matches ClaudeCLICaller's DEFAULT_TIMEOUT_S (claude_cli_caller.py) -- a
# hung request should surface as a clear timeout, not block a worker
# thread indefinitely with no visibility into why a run has stalled.
DEFAULT_TIMEOUT_S = 600


class AnthropicCaller:
    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS,
                 api_key: str | None = None, timeout: float | None = None,
                 max_retries: int = DEFAULT_MAX_RETRIES):
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "`mfdoc batch` needs the `anthropic` package: pip install 'mfdoc[batch]'"
            ) from exc
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_S
        self._client = (
            anthropic.Anthropic(api_key=api_key, timeout=self.timeout) if api_key
            else anthropic.Anthropic(timeout=self.timeout)
        )
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        # Rate limits, transient connection failures, and 5xx responses are
        # exactly the failure modes issue #79 exists to make survivable
        # without propagating into run_batch's per-member isolation (#78)
        # as a hard, whole-member failure -- everything else (bad request,
        # auth, a genuinely malformed prompt) is a real failure and must
        # not be retried into a longer, equally-doomed run.
        self._retryable_errors = (
            anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError,
        )

    def __call__(self, prompt: str) -> ModelResponse:
        def do_call() -> ModelResponse:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return model_response_from_message(message)

        # `retries` is local to this one `__call__` -- never a shared
        # instance attribute -- so it stays correct under run_batch's
        # concurrent ThreadPoolExecutor callers (issue #84): each thread's
        # own call gets its own counter via `on_retry`'s closure, with no
        # cross-thread state to race.
        retries = 0

        def on_retry(attempt: int, exc: Exception) -> None:
            nonlocal retries
            retries = attempt

        response = call_with_retry(
            do_call, lambda exc: isinstance(exc, self._retryable_errors),
            max_retries=self.max_retries, on_retry=on_retry,
        )
        response.retries = retries
        return response
