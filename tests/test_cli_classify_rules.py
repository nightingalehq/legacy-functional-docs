"""CLI smoke test for `mfdoc classify-rules` (Task 3)."""

from __future__ import annotations

import re
from types import SimpleNamespace

from mfdoc import cli


def test_classify_rules_fake_echo_end_to_end(cli_args, derive_result):
    args = SimpleNamespace(
        config=cli_args.config, caller="fake-echo", provider="anthropic",
        model=None, gcp_project=None, gcp_region=None, claude_code_timeout=None,
        llm_fallback=True,
    )
    assert cli.cmd_classify_rules(args) == 0


def test_classify_rules_limit_flag_caps_rows_sent(cli_args, indexed_db, monkeypatch):
    """Finding 7: --limit must be wired through cmd_classify_rules into
    classify_rules_llm's `limit` parameter, capping how many rows are
    actually sent to the model even when more are eligible."""
    from mfdoc import classify as classify_mod

    # indexed_db is a session-scoped connection shared across test modules;
    # a prior test in this session may already have reclassified every
    # structural row to 'llm'. Reset to a known "nothing classified yet"
    # state (same approach as test_classify.py's _reset_rule_theme) so this
    # test can rely on there being more than 2 eligible rows.
    indexed_db.execute("DELETE FROM rule_theme")
    indexed_db.commit()
    classify_mod.classify_rules_deterministic(indexed_db, taxonomy={})
    structural_count = indexed_db.execute(
        "SELECT COUNT(*) FROM rule_theme WHERE source='structural'"
    ).fetchone()[0]
    assert structural_count > 2, "fixture needs >2 structural rows to exercise a limit of 2"

    # Rows are now sent to the model in batches (issue #167), so a single
    # call can legitimately carry more than one row -- what --limit must
    # still guarantee is that no more than `limit` *rows* are ever put in
    # front of the model across all calls, not that there's exactly one
    # call per row. Capture every prompt this run issues and count the row
    # ids actually referenced in them instead of counting calls.
    prompts: list[str] = []
    real_classify_rules_llm = classify_mod.classify_rules_llm

    def recording_classify_rules_llm(conn, caller, **kwargs):
        def recording_caller(prompt: str):
            prompts.append(prompt)
            return caller(prompt)
        return real_classify_rules_llm(conn, recording_caller, **kwargs)

    monkeypatch.setattr(cli.classify, "classify_rules_llm", recording_classify_rules_llm)

    args = SimpleNamespace(
        config=cli_args.config, caller="fake-echo", provider="anthropic",
        model=None, gcp_project=None, gcp_region=None, claude_code_timeout=None,
        llm_fallback=True, limit=2,
    )
    assert cli.cmd_classify_rules(args) == 0
    rows_sent = {
        rc_id for prompt in prompts for rc_id in re.findall(r"^(\d+):", prompt, re.MULTILINE)
    }
    assert len(rows_sent) == 2
