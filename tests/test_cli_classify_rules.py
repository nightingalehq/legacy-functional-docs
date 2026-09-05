"""CLI smoke test for `mfdoc classify-rules` (Task 3)."""

from __future__ import annotations

from types import SimpleNamespace

from mfdoc import cli


def test_classify_rules_fake_echo_end_to_end(cli_args, derive_result):
    args = SimpleNamespace(
        config=cli_args.config, caller="fake-echo", provider="anthropic",
        model=None, gcp_project=None, gcp_region=None, claude_code_timeout=None,
        llm_fallback=True,
    )
    assert cli.cmd_classify_rules(args) == 0
