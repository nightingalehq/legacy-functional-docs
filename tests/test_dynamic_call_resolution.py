"""Guards for graph.resolve_interface_literal_calls -- reclassifying a CALL
on a Mantis INTERFACE handle bound to a literal, instead of leaving it as a
misleadingly "can't know until runtime" dynamic_target gap. See issue #104."""

from __future__ import annotations

import sqlite3

from mfdoc import graph
from mfdoc.db import SCHEMA
from mfdoc.dialects import mantis


def _extract(src: str, member_name: str = "TESTMOD"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, ?, 'mantis')", (member_name,))
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    mantis.extract(conn, 1, lines, member_name)
    return conn


def test_call_on_literal_bound_interface_handle_is_not_dynamic_target():
    """`INTERFACE MYHANDLE("REALPROG",PASSWORD)` binds MYHANDLE to a known
    literal target. A later bare `CALL MYHANDLE` is therefore determinable
    from source -- graph.resolve() must rewrite the edge to the real target
    and drop the dynamic_target gap RE_CALL raised at extraction time
    (which, in isolation, can't see the earlier binding)."""
    conn = _extract(
        'PROGRAM "TESTMOD"\n'
        "ENTRY MAIN\n"
        '  INTERFACE MYHANDLE("REALPROG",PASSWORD)\n'
        "  CALL MYHANDLE\n"
        "EXIT\n"
    )
    # Sanity check on raw extraction: the call site was flagged dynamic
    # before any resolution ran.
    before = conn.execute(
        "SELECT dynamic FROM call_edge WHERE callee_name='MYHANDLE'"
    ).fetchone()
    assert before is not None and before["dynamic"] == 1
    gap_before = conn.execute(
        "SELECT id FROM gap WHERE gap_kind='dynamic_target' AND raw LIKE '%MYHANDLE%'"
    ).fetchone()
    assert gap_before is not None

    graph.resolve(conn)

    edge = conn.execute(
        "SELECT callee_name, dynamic FROM call_edge WHERE call_kind='CALL' AND line_no=4"
    ).fetchone()
    assert edge is not None
    assert edge["callee_name"] == "REALPROG", "must resolve to the bound literal, not the handle name"
    assert edge["dynamic"] == 0

    gap_after = conn.execute(
        "SELECT id FROM gap WHERE gap_kind='dynamic_target' AND raw LIKE '%MYHANDLE%'"
    ).fetchone()
    assert gap_after is None, "resolved call must not still carry a dynamic_target gap"


def test_call_on_handle_never_bound_to_a_literal_stays_dynamic_target():
    """A CALL target with no matching INTERFACE binding in this member is
    genuinely indeterminate from source and must keep raising
    dynamic_target -- this fix must not blanket-suppress the gap kind."""
    conn = _extract(
        'PROGRAM "TESTMOD"\n'
        "ENTRY MAIN\n"
        "  CALL SOME_VAR\n"
        "EXIT\n"
    )
    graph.resolve(conn)
    edge = conn.execute(
        "SELECT callee_name, dynamic FROM call_edge WHERE callee_name='SOME_VAR'"
    ).fetchone()
    assert edge is not None
    assert edge["dynamic"] == 1
    gap = conn.execute(
        "SELECT id FROM gap WHERE gap_kind='dynamic_target' AND raw LIKE '%SOME_VAR%'"
    ).fetchone()
    assert gap is not None, "a genuinely unbound dynamic target must still raise the gap"
