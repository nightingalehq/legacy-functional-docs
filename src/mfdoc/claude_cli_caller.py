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
"""

from __future__ import annotations

import json
import subprocess

from .batch import ModelResponse

DEFAULT_TIMEOUT_S = 600


class ClaudeCLICaller:
    def __init__(self, model: str | None = None, timeout: int = DEFAULT_TIMEOUT_S):
        self.model = model
        self.timeout = timeout

    def __call__(self, prompt: str) -> ModelResponse:
        cmd = ["claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"]
        if self.model:
            cmd += ["--model", self.model]
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
        return ModelResponse(
            text=data.get("result", ""),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
