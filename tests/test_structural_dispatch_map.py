"""Guards on structural.dispatch_edges_for_member/dispatch_map -- the
"what does dispatching on this value actually do" table for a branch that
compares a configurable dispatch field (default: Natural's `*PF-KEY`)
against a literal.

Synthetic facts throughout -- invented field/member names, not any real
site's data -- since what's under test is the scan logic itself.
"""

from __future__ import annotations

import sqlite3

from mfdoc import structural
from mfdoc.db import SCHEMA, insert


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'TESTPROG', 'natural')")
    return conn


def _rc(conn, line_no, construct, condition=None, fields_used=None, literals=None,
        depth=0, end_line=None, pair_line_no=None):
    return insert(
        conn, "rule_candidate", member_id=1, line_no=line_no, construct=construct,
        condition=condition, raw=condition or construct, depth=depth,
        fields_used=fields_used, literals=literals, end_line=end_line,
        pair_line_no=pair_line_no,
    )


def test_a_dispatch_branch_reports_its_calls_and_field_assigns():
    conn = _conn()
    _rc(conn, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=13)
    insert(conn, "call_edge", caller_id=1, callee_name="MENUSEL", call_kind="FETCH",
           dynamic=0, line_no=11)
    _rc(conn, 12, "ASSIGN", fields_used="#SEND-MODE", literals="'UPDATE'", depth=1)

    edges = structural.dispatch_edges_for_member(conn, 1)
    assert len(edges) == 1
    e = edges[0]
    assert e["trigger_value"] == "PF3"
    assert e["line_no"] == 10
    assert {"callee_name": "MENUSEL", "call_kind": "FETCH"} == \
        {"callee_name": e["calls"][0]["callee_name"], "call_kind": e["calls"][0]["call_kind"]}
    assert e["assigns"] == [{"field": "#SEND-MODE", "literal": "'UPDATE'", "line_no": 12}]


def test_a_dynamic_call_target_is_never_included():
    conn = _conn()
    _rc(conn, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=12)
    insert(conn, "call_edge", caller_id=1, callee_name="*PGM-NAME", call_kind="FETCH",
           dynamic=1, line_no=11)

    edges = structural.dispatch_edges_for_member(conn, 1)
    assert edges[0]["calls"] == []


def test_a_condition_not_matching_the_dispatch_field_produces_no_edge():
    conn = _conn()
    _rc(conn, 10, "IF", condition="#RETURN-CODE = '****'", depth=0)

    assert structural.dispatch_edges_for_member(conn, 1) == []


def test_a_field_to_field_comparison_on_the_dispatch_field_produces_no_edge():
    """There is no single literal dispatch value to key a row on."""
    conn = _conn()
    _rc(conn, 10, "IF", condition="*PF-KEY = #SAVED-KEY", depth=0)

    assert structural.dispatch_edges_for_member(conn, 1) == []


def test_a_call_or_assign_outside_the_branch_is_not_included():
    """A call/assign past the branch's own `end_line` (its matching END-IF)
    must not be attributed to this trigger -- it belongs to whatever comes
    next in the module."""
    conn = _conn()
    _rc(conn, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=12)
    insert(conn, "call_edge", caller_id=1, callee_name="MENUSEL", call_kind="FETCH",
           dynamic=0, line_no=11)
    insert(conn, "call_edge", caller_id=1, callee_name="OTHERPGM", call_kind="FETCH",
           dynamic=0, line_no=13)
    _rc(conn, 14, "ASSIGN", fields_used="#UNRELATED", literals="'X'", depth=0)

    edges = structural.dispatch_edges_for_member(conn, 1)
    assert len(edges) == 1
    names = {c["callee_name"] for c in edges[0]["calls"]}
    assert names == {"MENUSEL"}
    assert edges[0]["assigns"] == []


def test_a_call_on_the_else_branch_is_not_attributed_to_the_if_s_own_trigger():
    """The IF row's end_line spans both THEN and ELSE, but this function is
    THEN-only -- a paired ELSE row must clamp the range so a call/assign
    that only happens on the ELSE branch isn't wrongly attributed to the
    IF condition's own literal dispatch value."""
    conn = _conn()
    _rc(conn, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=14)
    insert(conn, "call_edge", caller_id=1, callee_name="MENUSEL", call_kind="FETCH",
           dynamic=0, line_no=11)
    _rc(conn, 12, "ELSE", depth=0, pair_line_no=10)
    insert(conn, "call_edge", caller_id=1, callee_name="OTHERPGM", call_kind="FETCH",
           dynamic=0, line_no=13)

    edges = structural.dispatch_edges_for_member(conn, 1)
    assert len(edges) == 1
    names = {c["callee_name"] for c in edges[0]["calls"]}
    assert names == {"MENUSEL"}
    assert edges[0]["end_line"] == 11


def test_an_unresolved_end_line_falls_back_to_the_if_s_own_line():
    """No matching END-IF found (`end_line` NULL) -- conservative fallback
    (the IF's own line only), not a guess at where the branch really ends."""
    conn = _conn()
    _rc(conn, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=None)
    insert(conn, "call_edge", caller_id=1, callee_name="MENUSEL", call_kind="FETCH",
           dynamic=0, line_no=11)

    edges = structural.dispatch_edges_for_member(conn, 1)
    assert edges[0]["calls"] == []


def test_dispatch_map_renders_one_table_per_member_with_edges():
    conn = _conn()
    _rc(conn, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="MENUSEL", call_kind="FETCH",
           dynamic=0, line_no=11)

    out = structural.dispatch_map(conn)
    assert "doc_type: register" in out
    assert "## TESTPROG" in out
    assert "PF3" in out and "MENUSEL" in out


def test_dispatch_map_reports_no_edges_when_none_found(tmp_path):
    from mfdoc.db import connect

    conn = connect(tmp_path / "index.db")
    out = structural.dispatch_map(conn)
    assert "No dispatch branches found" in out


def test_dispatch_map_honours_a_custom_dispatch_field_pattern():
    """A Mantis-style menu/transfer-option field (no fixed name, unlike
    Natural's built-in *PF-KEY) is picked up once configured, the same way
    a project supplies its own outcome_field_pattern."""
    import re

    conn = _conn()
    _rc(conn, 10, "IF", condition="#MENU-OPTION = '1'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="MMP0200", call_kind="CALLNAT",
           dynamic=0, line_no=11)

    out = structural.dispatch_map(conn, dispatch_field=re.compile(r"#MENU-OPTION"))
    assert "`1`" in out and "MMP0200" in out


def test_dispatch_map_cli(cli_args, derive_result):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, out=None)
    assert cli.cmd_dispatch_map(args) == 0
