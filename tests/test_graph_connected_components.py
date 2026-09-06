"""graph.connected_components() -- weakly-connected components of the call
graph, member ids only. See docs/superpowers/specs/
2026-09-06-call-graph-lr-components-design.md for the design this backs:
structural.call_graph_diagram uses this to split the call graph into one
diagram per independent component, instead of a single node-count
threshold that ignores actual connectivity.
"""

from __future__ import annotations

import sqlite3

from mfdoc import graph
from mfdoc.db import SCHEMA


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _member(conn, name, library="LIB"):
    conn.execute(
        "INSERT INTO member (name, dialect, library) VALUES (?, 'natural', ?)",
        (name, library),
    )
    return conn.execute("SELECT id FROM member WHERE name=? AND library=?", (name, library)).fetchone()["id"]


def _edge(conn, caller_id, callee_name, callee_id=None, resolved=1):
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, callee_id, call_kind, line_no, resolved) "
        "VALUES (?, ?, ?, 'CALLNAT', 1, ?)",
        (caller_id, callee_name, callee_id, resolved),
    )


def test_two_disconnected_subgraphs_yield_two_components():
    conn = _conn()
    a, b = _member(conn, "A"), _member(conn, "B")
    c, d = _member(conn, "C"), _member(conn, "D")
    _edge(conn, a, "B", b)
    _edge(conn, c, "D", d)
    conn.commit()

    components = graph.connected_components(conn)
    assert len(components) == 2
    sets_by_content = sorted(components, key=lambda s: min(s))
    assert sets_by_content[0] == {a, b}
    assert sets_by_content[1] == {c, d}


def test_caller_with_only_unresolved_call_gets_singleton_component():
    conn = _conn()
    solo = _member(conn, "SOLO")
    _edge(conn, solo, "MISSING", callee_id=None, resolved=0)
    conn.commit()

    components = graph.connected_components(conn)
    assert {solo} in components


def test_shared_unresolved_callee_name_does_not_bridge_components():
    """Two callers in otherwise-unrelated parts of the graph that each have
    an unresolved call to the same missing callee *name* must remain in
    separate components -- an unresolved target has no member id, so
    treating a shared missing name as a bridge would let a coincidental (or
    common utility) name silently re-merge components that are otherwise
    completely unrelated."""
    conn = _conn()
    caller1 = _member(conn, "CALLER1", library="LIBA")
    caller2 = _member(conn, "CALLER2", library="LIBB")
    _edge(conn, caller1, "GHOSTUTIL", callee_id=None, resolved=0)
    _edge(conn, caller2, "GHOSTUTIL", callee_id=None, resolved=0)
    conn.commit()

    components = graph.connected_components(conn)
    assert len(components) == 2
    assert {caller1} in components
    assert {caller2} in components


def test_indirect_cycle_collapses_to_one_component():
    conn = _conn()
    a, b, c = _member(conn, "A"), _member(conn, "B"), _member(conn, "C")
    _edge(conn, a, "B", b)
    _edge(conn, b, "C", c)
    _edge(conn, c, "A", a)
    conn.commit()

    components = graph.connected_components(conn)
    assert components == [{a, b, c}]


def test_empty_call_edge_table_yields_no_components():
    conn = _conn()
    conn.commit()
    assert graph.connected_components(conn) == []
