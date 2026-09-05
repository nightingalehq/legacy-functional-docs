# tests/test_structural_complexity.py
from __future__ import annotations

from mfdoc import structural


def test_heatmap_sorted_descending_by_risk_score(indexed_db):
    out = structural.complexity_heatmap(indexed_db)
    assert out.startswith("---\n")
    lines = [l for l in out.splitlines() if l.startswith("| `")]
    scores = [float(l.split("|")[-2].strip()) for l in lines]
    assert scores == sorted(scores, reverse=True)


def test_heatmap_row_count_matches_members_with_rules(indexed_db):
    conn = indexed_db
    expected = conn.execute(
        "SELECT COUNT(DISTINCT member_id) FROM rule_candidate"
    ).fetchone()[0]
    out = structural.complexity_heatmap(conn)
    actual = len([l for l in out.splitlines() if l.startswith("| `")])
    assert actual == expected


def test_unknown_metric_raises():
    import pytest
    from mfdoc.db import connect

    with pytest.raises(ValueError, match="rule_depth"):
        structural.complexity_heatmap(connect(":memory:"), metric="cyclomatic")


def test_complexity_cli(cli_args, derive_result):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, out=None)
    assert cli.cmd_complexity(args) == 0
