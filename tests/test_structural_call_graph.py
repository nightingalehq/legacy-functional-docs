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
    caller_id = indexed_db.execute(
        "SELECT id FROM member WHERE name='MMB0100'"
    ).fetchone()["id"]
    assert caller_id in graph_data
    assert graph_data[caller_id]["name"] == "MMB0100"
    callees = {c["callee_name"] for c in graph_data[caller_id]["calls"]}
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
    calls = graph_data[member_id]["calls"]
    assert any(
        c["callee_name"] == "NOSUCHPROG" and c["callee_id"] is None and c["resolved"] is False
        for c in calls
    )


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
    assert "GHOSTPROG" in out["inline"], "the missing callee's name must be visible, not collapsed into an anonymous sink"


def test_repeated_unresolved_calls_to_same_callee_produce_one_edge():
    conn = _conn()
    conn.execute(
        "INSERT INTO member (name, dialect) VALUES ('CGTEST3', 'natural')"
    )
    member_id = conn.execute("SELECT id FROM member WHERE name='CGTEST3'").fetchone()["id"]
    for line in (5, 9, 14):
        conn.execute(
            "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
            "VALUES (?, 'GHOSTPROG', NULL, 'CALLNAT', ?, 0)", (member_id, line)
        )
    conn.commit()
    out = structural.call_graph_diagram(conn, cluster_by="module", max_nodes_inline=10_000)
    inline = out["inline"]
    assert inline.count("-.->|unresolved|") == 1, "three calls to the same missing callee must collapse to one edge"
    assert inline.count("(unresolved)") == 1, "the GHOSTPROG node must be declared once, not once per call site"


def test_repeated_resolved_calls_to_same_callee_produce_one_edge():
    conn = _conn()
    conn.execute("INSERT INTO member (name, dialect) VALUES ('CGTEST4', 'natural')")
    conn.execute("INSERT INTO member (name, dialect) VALUES ('CGTARGET', 'natural')")
    caller_id = conn.execute("SELECT id FROM member WHERE name='CGTEST4'").fetchone()["id"]
    callee_id = conn.execute("SELECT id FROM member WHERE name='CGTARGET'").fetchone()["id"]
    for line in (5, 9):
        conn.execute(
            "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
            "VALUES (?, 'CGTARGET', ?, 'CALLNAT', ?, 1)", (caller_id, callee_id, line)
        )
    conn.commit()
    out = structural.call_graph_diagram(conn, cluster_by="module", max_nodes_inline=10_000)
    edge_lines = [l for l in out["inline"].splitlines() if "-->" in l]
    assert len(edge_lines) == 1, "two calls to the same resolved callee must collapse to one edge"


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


def test_unsupported_cluster_by_raises():
    """A config typo (e.g. "subsytem") must not silently fall back to
    library clustering with no warning -- matching complexity_heatmap's
    posture of raising ValueError for its own unsupported `metric`
    value, rather than guessing at what the caller meant."""
    import pytest

    conn = _conn()
    with pytest.raises(ValueError, match="subsytem"):
        structural.build_call_graph(conn, cluster_by="subsytem")
    with pytest.raises(ValueError):
        structural.call_graph_diagram(conn, cluster_by="subsytem")


def test_ambiguous_member_name_renders_as_two_distinct_nodes():
    """Finding 1: build_call_graph() must key on member.id, not the bare
    member.name -- member.name is only unique together with
    (library, dialect) (see the UNIQUE(name, library, dialect) constraint
    in db.py), so two distinct members sharing a name in different
    libraries must never be silently conflated into one call-graph node.

    Two members named DUPPROG live in LIBA/LIBB. Each is exercised as
    both a caller (it calls a distinct downstream target) and a callee
    (a distinct upstream caller resolves to it via callee_id -- the real
    foreign key graph.resolve() sets, not a name match) so the fix must
    hold in both directions, not just for outgoing calls."""
    conn = _conn()
    conn.execute("INSERT INTO member (name, dialect, library) VALUES ('DUPPROG', 'natural', 'LIBA')")
    conn.execute("INSERT INTO member (name, dialect, library) VALUES ('DUPPROG', 'natural', 'LIBB')")
    conn.execute("INSERT INTO member (name, dialect, library) VALUES ('UPSTREAMA', 'natural', 'LIBA')")
    conn.execute("INSERT INTO member (name, dialect, library) VALUES ('UPSTREAMB', 'natural', 'LIBB')")
    conn.execute("INSERT INTO member (name, dialect, library) VALUES ('DOWNSTREAMA', 'natural', 'LIBA')")
    conn.execute("INSERT INTO member (name, dialect, library) VALUES ('DOWNSTREAMB', 'natural', 'LIBB')")

    def mid(name, library):
        return conn.execute(
            "SELECT id FROM member WHERE name=? AND library=?", (name, library)
        ).fetchone()["id"]

    dup_a, dup_b = mid("DUPPROG", "LIBA"), mid("DUPPROG", "LIBB")
    upstream_a, upstream_b = mid("UPSTREAMA", "LIBA"), mid("UPSTREAMB", "LIBB")
    downstream_a, downstream_b = mid("DOWNSTREAMA", "LIBA"), mid("DOWNSTREAMB", "LIBB")

    # DUPPROG(A) and DUPPROG(B) each called from a distinct upstream member,
    # resolved via the real callee_id foreign key (not just a name match).
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, 'DUPPROG', ?, 'CALLNAT', 1, 1)", (upstream_a, dup_a),
    )
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, 'DUPPROG', ?, 'CALLNAT', 1, 1)", (upstream_b, dup_b),
    )
    # DUPPROG(A) and DUPPROG(B) each also call a distinct downstream member.
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, 'DOWNSTREAMA', ?, 'CALLNAT', 1, 1)", (dup_a, downstream_a),
    )
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, 'DOWNSTREAMB', ?, 'CALLNAT', 1, 1)", (dup_b, downstream_b),
    )
    conn.commit()

    graph_data = structural.build_call_graph(conn)
    assert dup_a in graph_data and dup_b in graph_data
    assert graph_data[dup_a]["library"] == "LIBA"
    assert graph_data[dup_b]["library"] == "LIBB"
    assert {c["callee_name"] for c in graph_data[dup_a]["calls"]} == {"DOWNSTREAMA"}
    assert {c["callee_id"] for c in graph_data[dup_a]["calls"]} == {downstream_a}
    assert {c["callee_name"] for c in graph_data[dup_b]["calls"]} == {"DOWNSTREAMB"}
    assert {c["callee_id"] for c in graph_data[dup_b]["calls"]} == {downstream_b}

    out = structural.call_graph_diagram(conn, cluster_by="module", max_nodes_inline=10_000)
    inline = out["inline"]
    # Both colliding members must render as their own, distinctly-labeled
    # node -- never merged into a single "DUPPROG" node.
    assert 'DUPPROG (LIBA)' in inline
    assert 'DUPPROG (LIBB)' in inline
    assert inline.count('"DUPPROG"') == 0, "the bare ambiguous name must not appear unqualified"

    # Each upstream caller's edge must land on its own DUPPROG node, not a
    # shared merged one -- i.e. two distinct mermaid node ids for DUPPROG,
    # derived from the (library, name, dialect) triple, not the member.id
    # rowid (see test_node_ids_stable_across_member_id_renumbering below
    # for why rowid-derived ids are the wrong choice).
    assert structural._mermaid_id("LIBA|DUPPROG|natural") != structural._mermaid_id(
        "LIBB|DUPPROG|natural"
    )


def test_node_ids_stable_across_member_id_renumbering():
    """Regression for the call-graph node-id-churn follow-up finding:
    mermaid node ids must be derived from the content-stable
    (library, name, dialect) triple, not member.id (a SQLite rowid).
    Keying by rowid meant inserting one unrelated source file earlier in
    ingest order (so it grabs a lower id) renumbered every downstream
    member id, which churned every node id in call-graph.md even though
    none of the actual members changed. Here the same two members
    (STABLEPROG calling TARGETPROG) get different ids across two runs
    because an unrelated member is inserted first in the second run --
    the rendered node id for STABLEPROG must be identical in both."""
    conn1 = _conn()
    conn1.execute("INSERT INTO member (name, dialect, library) VALUES ('STABLEPROG', 'natural', 'LIBX')")
    conn1.execute("INSERT INTO member (name, dialect, library) VALUES ('TARGETPROG', 'natural', 'LIBX')")

    def mid1(name):
        return conn1.execute("SELECT id FROM member WHERE name=?", (name,)).fetchone()["id"]

    conn1.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, 'TARGETPROG', ?, 'CALLNAT', 1, 1)",
        (mid1("STABLEPROG"), mid1("TARGETPROG")),
    )
    conn1.commit()
    out1 = structural.call_graph_diagram(conn1, cluster_by="module", max_nodes_inline=10_000)

    conn2 = _conn()
    # An unrelated member inserted first, with an explicit low id but a
    # different name -- simulates an earlier source file in ingest order.
    conn2.execute("INSERT INTO member (id, name, dialect, library) VALUES (1, 'UNRELATED', 'natural', 'LIBY')")
    conn2.execute("INSERT INTO member (name, dialect, library) VALUES ('STABLEPROG', 'natural', 'LIBX')")
    conn2.execute("INSERT INTO member (name, dialect, library) VALUES ('TARGETPROG', 'natural', 'LIBX')")

    def mid2(name):
        return conn2.execute("SELECT id FROM member WHERE name=?", (name,)).fetchone()["id"]

    conn2.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, 'TARGETPROG', ?, 'CALLNAT', 1, 1)",
        (mid2("STABLEPROG"), mid2("TARGETPROG")),
    )
    conn2.commit()
    out2 = structural.call_graph_diagram(conn2, cluster_by="module", max_nodes_inline=10_000)

    # member ids for STABLEPROG differ between the two runs (renumbered by
    # the unrelated insert), but the rendered node id must not.
    assert mid1("STABLEPROG") != mid2("STABLEPROG")
    stable_node_id = structural._mermaid_id("LIBX|STABLEPROG|natural")
    assert stable_node_id in out1["inline"]
    assert stable_node_id in out2["inline"]


def test_call_graph_cli_stdout(cli_args, derive_result, capsys):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, out=None)
    assert cli.cmd_call_graph(args) == 0
    assert "mermaid" in capsys.readouterr().out
