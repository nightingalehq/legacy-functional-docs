"""Claude-via-Vertex-AI ModelCaller for `mfdoc batch`.

Isolated the same way anthropic_caller.py is: the `anthropic` package's
Vertex extra (which pulls in `google-auth`) stays optional, so nothing
outside `mfdoc batch --provider vertex` ever needs it installed.

This backs Claude models running on Vertex AI (`anthropic.AnthropicVertex`),
not Google's own Gemini models -- same model family our writing-rules
prompting and citation-discipline behaviour are built against, just a
different egress path (Google Cloud, not Anthropic direct). See issue #12
and docs/guides/security-and-compliance.md's data-flow section for why that
distinction matters to a client's compliance posture. Routing Gemini or any
other non-Claude model through this caller is out of scope until the Phase
5.1 eval prompts have been run against it specifically -- see issue #12.
"""

from __future__ import annotations

import os
import threading

from .batch import ModelResponse, model_response_from_message
from .retry import DEFAULT_MAX_RETRIES, call_with_retry

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_REGION = "us-east5"
# Matches ClaudeCLICaller's DEFAULT_TIMEOUT_S (claude_cli_caller.py) -- a
# hung request should surface as a clear timeout, not block a worker
# thread indefinitely with no visibility into why a run has stalled.
DEFAULT_TIMEOUT_S = 600


class VertexCaller:
    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS,
                 project: str | None = None, region: str | None = None,
                 timeout: float | None = None, max_retries: int = DEFAULT_MAX_RETRIES):
        try:
            import anthropic
            from anthropic import AnthropicVertex
        except ImportError as exc:
            raise RuntimeError(
                "`mfdoc batch --provider vertex` needs the `anthropic` package's Vertex "
                "extra: pip install 'mfdoc[vertex]'"
            ) from exc

        # `ANTHROPIC_VERTEX_PROJECT_ID`/`CLOUD_ML_REGION` are the anthropic
        # SDK's own env vars for AnthropicVertex (confirmed against the
        # package's actual source) -- checked first so anyone who's already
        # set those up (e.g. by following Anthropic's own Vertex docs) gets
        # picked up automatically. GOOGLE_CLOUD_PROJECT/GCLOUD_PROJECT are
        # kept as a secondary fallback since they're common ambient
        # GCP-project env vars, just not ones this SDK itself reads.
        # Only an explicit `None` (argument not passed) falls back to env
        # vars -- an explicitly-passed empty string is left as-is and hits
        # the "no GCP project configured" check below, rather than silently
        # being replaced by whatever happens to be in the ambient
        # environment.
        if project is None:
            project = (
                os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
                or os.environ.get("GOOGLE_CLOUD_PROJECT")
                or os.environ.get("GCLOUD_PROJECT")
            )
        if region is None:
            region = os.environ.get("CLOUD_ML_REGION") or os.environ.get("ANTHROPIC_VERTEX_REGION")
        region = region or DEFAULT_REGION
        if not project:
            raise RuntimeError(
                "no GCP project configured -- pass --gcp-project, or set "
                "ANTHROPIC_VERTEX_PROJECT_ID (the anthropic SDK's own Vertex env var) "
                "or GOOGLE_CLOUD_PROJECT"
            )

        # The `anthropic` package's Vertex extra (`google-auth`) is only
        # imported lazily, inside the SDK, the first time a real request is
        # made -- so `from anthropic import AnthropicVertex` above succeeds
        # even when google-auth isn't installed, and a missing extra would
        # otherwise only surface mid-batch as an
        # `anthropic.lib._extras._common.MissingDependencyError` (not an
        # ImportError, so the except above never catches it) referencing
        # `pip install anthropic[vertex]` instead of this project's own
        # `mfdoc[vertex]` extra. Check for it now, at construction, so the
        # same clear install hint this class exists to provide actually
        # fires before any work is done.
        try:
            import google.auth  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "`mfdoc batch --provider vertex` needs the `anthropic` package's Vertex "
                "extra: pip install 'mfdoc[vertex]'"
            ) from exc

        # Credentials come from the ambient environment (ADC: `gcloud auth
        # application-default login`, a service account, or workload identity)
        # the same way any other Vertex client picks them up -- this caller
        # never handles a key file itself.
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_S
        self._client = AnthropicVertex(project_id=project, region=region, timeout=self.timeout)
        self.model = model
        self.max_tokens = max_tokens
        # `run_batch` invokes callers concurrently from a ThreadPoolExecutor.
        # `Anthropic`'s API-key auth is a stateless read of an immutable
        # string, but AnthropicVertex's ADC credentials are refreshed
        # in-place (mutating shared `google.auth.credentials.Credentials`
        # state) with no locking of its own -- serialize requests through
        # this one client so concurrent workers can't race a token refresh.
        self._lock = threading.Lock()
        self.max_retries = max_retries
        # Same transient-error set as AnthropicCaller (issue #79) -- both
        # callers are built on the same `anthropic` SDK response/exception
        # shape, just a different transport.
        self._retryable_errors = (
            anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError,
        )

    def __call__(self, prompt: str) -> ModelResponse:
        def do_call() -> ModelResponse:
            # Lock held only around the actual call, not around backoff's
            # sleep between retries -- a retry waiting out a rate limit has
            # no reason to also block every other worker's credential-safe
            # access to this client in the meantime.
            with self._lock:
                message = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
            return model_response_from_message(message)

        # Local to this one `__call__`, not a shared instance attribute --
        # see AnthropicCaller's identical comment (issue #84): each
        # concurrent caller gets its own counter via `on_retry`'s closure.
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
