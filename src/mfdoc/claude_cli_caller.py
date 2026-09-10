"""Claude-Code-CLI-backed ModelCaller for `mfdoc batch`/`mfdoc test-batch`.

For anyone with the `claude` CLI installed and authenticated (interactive
login, no `ANTHROPIC_API_KEY` needed) who wants to run the narrative pass
without provisioning a separate API key. Isolated in its own module, same
as `anthropic_caller.py`/`vertex_caller.py`, so nothing else needs the
`claude` binary on PATH to import or run.

Each call is a single, tool-free, non-persisted one-shot completion:
`--tools ""` disables all tool access (the fact brief + writing rules +
template are already fully embedded in the prompt string by
batch.py/testbatch.py, so the call never needs filesystem/tool access to
do its job -- and disabling it keeps this a pure text-in/text-out
completion, not an agentic session that could wander off and edit files),
`--no-session-persistence` avoids leaving a throwaway conversation behind
per call, `--output-format json` gives a single structured result with
token usage instead of having to scrape human-readable text output.

Issue #150: `claude -p` has no `max_tokens` equivalent -- nothing bounds how
much a single call can generate the way `AnthropicCaller.DEFAULT_MAX_TOKENS`
does, which was implicated in real 600s subprocess timeouts. `claude -p
--help` exposes no output-token cap either, but `--max-budget-usd` ("only
works with --print", i.e. exactly this caller's usage) is the closest
available lever -- capping dollar spend also caps how long a runaway
generation can keep going. Opt-in only (`max_budget_usd=None` by default):
this repo doesn't guess at a business-tuned dollar figure nobody asked for,
so callers that want the safety net set one explicitly (via
--claude-code-max-budget-usd).
"""

from __future__ import annotations

import json
import subprocess

from .batch import ModelResponse

DEFAULT_TIMEOUT_S = 600


class ClaudeCLICaller:
    def __init__(self, model: str | None = None, timeout: int | None = None,
                 max_budget_usd: float | None = None):
        self.model = model
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_S
        if max_budget_usd is not None and max_budget_usd <= 0:
            raise ValueError(
                f"max_budget_usd must be a positive dollar amount, got {max_budget_usd!r} -- "
                "omit it (or pass None) for no cap"
            )
        self.max_budget_usd = max_budget_usd

    def __call__(self, prompt: str) -> ModelResponse:
        cmd = ["claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"]
        if self.model:
            cmd += ["--model", self.model]
        if self.max_budget_usd is not None:
            cmd += ["--max-budget-usd", str(self.max_budget_usd)]
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "--provider claude-code needs the `claude` CLI on PATH -- install Claude Code "
                "and authenticate it, or use --provider anthropic/vertex instead"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"`claude -p` timed out after {self.timeout}s") from exc

        if proc.returncode != 0:
            raise RuntimeError(f"`claude -p` exited {proc.returncode}: {proc.stderr.strip()}")

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"`claude -p --output-format json` produced unparseable output: {proc.stdout[:500]!r}") from exc

        if data.get("is_error"):
            raise RuntimeError(f"`claude -p` reported an error: {data.get('result')!r}")

        usage = data.get("usage") or {}
        # Issue #169: `claude -p --output-format json`'s `usage` object is the
        # same shape as the Anthropic Messages API's own usage object (the
        # `claude` CLI's runtime is itself an Anthropic SDK client) -- under
        # that shape, `input_tokens` alone reports only a turn's uncached
        # portion once prompt caching is involved; the cache-write and
        # cache-hit token counts are separate fields. `.get(..., 0)`
        # everywhere so an older `claude` CLI that doesn't emit these fields,
        # or a call that genuinely didn't cache anything, still defaults
        # sensibly to 0 rather than raising or silently misreporting.
        return ModelResponse(
            text=data.get("result", ""),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        )
