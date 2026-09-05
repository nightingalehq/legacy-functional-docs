"""Call-graph DAG builder/renderer tests.

The two unresolved-call cases build an isolated in-memory connection
(same pattern as test_unused_entity_fields.py) instead of mutating the
session-scoped indexed_db fixture, which is shared across the whole
test suite -- inserting synthetic rows into it would leak into every
test that runs afterward in the same session.
"""

from __future__ import annotations

import sqlite3

from mfdoc import structural
from mfdoc.db import SCHEMA


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def test_build_call_graph_includes_known_edge(indexed_db):
    graph_data = structural.build_call_graph(indexed_db)
    assert "MMB0100" in graph_data
    callees = {c["callee"] for c in graph_data["MMB0100"]["calls"]}
    assert "MMP0100" in callees


def test_unresolved_call_marked_not_resolved():
    conn = _conn()
    conn.execute(
        "INSERT INTO member (name, dialect) VALUES ('CGTEST', 'natural')"
    )
    member_id = conn.execute("SELECT id FROM member WHERE name='CGTEST'").fetchone()["id"]
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, 'NOSUCHPROG', NULL, 'CALLNAT', 5, 0)", (member_id,)
    )
    conn.commit()
    graph_data = structural.build_call_graph(conn)
    calls = graph_data["CGTEST"]["calls"]
    assert any(c["callee"] == "NOSUCHPROG" and c["resolved"] is False for c in calls)


def test_inline_diagram_below_threshold(indexed_db):
    out = structural.call_graph_diagram(indexed_db, cluster_by="module", max_nodes_inline=10_000)
    assert set(out.keys()) == {"inline"}
    assert "```mermaid" in out["inline"] and "graph TD" in out["inline"]


def test_standalone_files_above_threshold(indexed_db):
    out = structural.call_graph_diagram(indexed_db, cluster_by="module", max_nodes_inline=0)
    assert "inline" in out
    assert len(out) > 1, "expected per-cluster standalone files when over threshold"


def test_unresolved_edge_renders_dashed():
    conn = _conn()
    conn.execute(
        "INSERT INTO member (name, dialect) VALUES ('CGTEST2', 'natural')"
    )
    member_id = conn.execute("SELECT id FROM member WHERE name='CGTEST2'").fetchone()["id"]
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, 'GHOSTPROG', NULL, 'CALLNAT', 5, 0)", (member_id,)
    )
    conn.commit()
    out = structural.call_graph_diagram(conn, cluster_by="module", max_nodes_inline=10_000)
    assert "-.->|unresolved|" in out["inline"]


def test_cluster_by_subsystem_changes_clustering():
    """cluster_by="subsystem" must actually drive clustering off
    member.system, not silently fall back to library grouping -- two
    synthetic members share one library but have different system
    values, so the standalone-file keys differ between the two
    cluster_by settings."""
    conn = _conn()
    conn.execute(
        "INSERT INTO member (name, dialect, library, system) VALUES "
        "('CGA', 'natural', 'SHAREDLIB', 'SYSALPHA')"
    )
    conn.execute(
        "INSERT INTO member (name, dialect, library, system) VALUES "
        "('CGB', 'natural', 'SHAREDLIB', 'SYSBETA')"
    )
    a_id = conn.execute("SELECT id FROM member WHERE name='CGA'").fetchone()["id"]
    b_id = conn.execute("SELECT id FROM member WHERE name='CGB'").fetchone()["id"]
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, 'TARGETA', NULL, 'CALLNAT', 1, 0)", (a_id,)
    )
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, 'TARGETB', NULL, 'CALLNAT', 1, 0)", (b_id,)
    )
    conn.commit()

    by_module = structural.call_graph_diagram(conn, cluster_by="module", max_nodes_inline=0)
    by_subsystem = structural.call_graph_diagram(conn, cluster_by="subsystem", max_nodes_inline=0)

    assert set(by_module.keys()) - {"inline"} == {"SHAREDLIB"}
    assert set(by_subsystem.keys()) - {"inline"} == {"SYSALPHA", "SYSBETA"}


def test_call_graph_cli_stdout(cli_args, derive_result, capsys):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, out=None)
    assert cli.cmd_call_graph(args) == 0
    assert "mermaid" in capsys.readouterr().out
