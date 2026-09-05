from __future__ import annotations

from mfdoc import structural


def test_gap_summary_counts_match_gap_table(indexed_db):
    conn = indexed_db
    expected = dict(
        conn.execute(
            "SELECT gap_kind || '|' || severity, COUNT(*) FROM gap GROUP BY 1"
        ).fetchall()
    )
    out = structural.gap_summary(conn)
    assert out.startswith("---\n")
    assert "doc_type: register" in out
    for key, count in expected.items():
        kind, severity = key.split("|")
        assert f"| `{kind}` | {severity} | {count} |" in out


def test_gap_summary_empty_when_no_gaps(tmp_path):
    from mfdoc.db import connect

    conn = connect(tmp_path / "index.db")
    out = structural.gap_summary(conn)
    assert "No gaps recorded" in out


def test_gap_summary_cli(cli_args, derive_result):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, out=None)
    assert cli.cmd_gap_summary(args) == 0
