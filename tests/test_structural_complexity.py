# tests/test_structural_complexity.py
from __future__ import annotations

import sqlite3

from mfdoc import structural
from mfdoc.db import SCHEMA


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


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


def test_heatmap_handles_name_collision_across_libraries():
    """Two distinct member.id rows can share a bare name (unique only
    together with library+dialect -- see db.py). The heatmap must not
    silently merge their rule_candidate rows into one collapsed/undercounted
    row: either both members show up as distinct risk-scored rows, or the
    colliding name is rendered as one explicit ambiguous row -- never a
    merged single row."""
    conn = _conn()
    conn.execute(
        "INSERT INTO member (name, dialect, library) VALUES ('DUPPROG', 'natural', 'LIBA')"
    )
    conn.execute(
        "INSERT INTO member (name, dialect, library) VALUES ('DUPPROG', 'natural', 'LIBB')"
    )
    mid_a = conn.execute(
        "SELECT id FROM member WHERE library='LIBA'"
    ).fetchone()["id"]
    mid_b = conn.execute(
        "SELECT id FROM member WHERE library='LIBB'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, depth, construct, raw) "
        "VALUES (?, 10, 1, 'IF', 'IF X')", (mid_a,)
    )
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, depth, construct, raw) "
        "VALUES (?, 20, 3, 'IF', 'IF Y')", (mid_b,)
    )
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, depth, construct, raw) "
        "VALUES (?, 21, 3, 'IF', 'IF Z')", (mid_b,)
    )
    conn.commit()

    out = structural.complexity_heatmap(conn)
    rows = [l for l in out.splitlines() if l.startswith("| `DUPPROG`")]

    # Never collapse both members' facts into a single row that reports
    # only one member's rule_count/max_depth (e.g. losing LIBB's 2 rules
    # or LIBA's depth-1 rule entirely).
    if len(rows) == 1:
        assert "ambiguous" in rows[0].lower()
        assert "LIBA" in rows[0] and "LIBB" in rows[0]
    else:
        assert len(rows) == 2
        assert not any("ambiguous" in r.lower() for r in rows)


def test_complexity_cli(cli_args, derive_result):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, out=None)
    assert cli.cmd_complexity(args) == 0
