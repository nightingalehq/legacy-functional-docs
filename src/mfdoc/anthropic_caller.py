"""Anthropic-backed ModelCaller for `mfdoc batch`.

Isolated in its own module so `anthropic` stays an optional dependency --
importing mfdoc.batch, or running everything except `mfdoc batch`, never
requires it installed.
"""

from __future__ import annotations

from .batch import ModelResponse, model_response_from_message

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 8192


class AnthropicCaller:
    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS,
                 api_key: str | None = None):
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "`mfdoc batch` needs the `anthropic` package: pip install 'mfdoc[batch]'"
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def __call__(self, prompt: str) -> ModelResponse:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return model_response_from_message(message)
