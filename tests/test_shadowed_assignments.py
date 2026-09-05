"""Guards on graph.shadowed_assignments[_for_member] -- the dead-store
(definite-assignment) check for a conditional write to a field that gets
unconditionally overwritten before anything reads it.

Synthetic facts throughout -- invented field/member names, not any real
site's data -- since what's under test is the scan logic itself.
"""

from __future__ import annotations

import sqlite3

from mfdoc import graph
from mfdoc.db import SCHEMA, insert


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'TESTPROG', 'natural')")
    return conn


def _rc(conn, line_no, construct, fields_used=None, literals=None, depth=0):
    return insert(
        conn, "rule_candidate", member_id=1, line_no=line_no, construct=construct,
        condition="x", raw="x", depth=depth, fields_used=fields_used, literals=literals,
    )


def test_both_if_and_else_branch_writes_are_shadowed_by_a_later_unconditional_write():
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="FLAGFIELD", literals="TRUE", depth=1)
    _rc(conn, 12, "ELSE", depth=0)
    _rc(conn, 13, "ASSIGN", fields_used="FLAGFIELD", literals="FALSE", depth=1)
    _rc(conn, 15, "ASSIGN", fields_used="FLAGFIELD", literals="FALSE", depth=0)
    # First read is well after the override -- irrelevant to whether 11/13 are shadowed.
    _rc(conn, 40, "IF", fields_used="FLAGFIELD", depth=0)

    findings = graph.shadowed_assignments_for_member(conn, 1)
    lines = {f["line_no"] for f in findings}
    assert lines == {11, 13}
    for f in findings:
        assert f["override_line"] == 15
        assert f["override_literal"] == "FALSE"


def test_a_read_between_the_conditional_write_and_any_later_write_makes_it_safe():
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="FLAGFIELD", literals="TRUE", depth=1)
    _rc(conn, 20, "IF", fields_used="FLAGFIELD", depth=0)  # reads FLAGFIELD before any override
    _rc(conn, 30, "ASSIGN", fields_used="FLAGFIELD", literals="FALSE", depth=0)

    assert graph.shadowed_assignments_for_member(conn, 1) == []


def test_a_conditional_write_never_touched_again_is_left_alone():
    """Nothing to compare against -- guessing this is dead without a later
    write actually overwriting it would be exactly the kind of invented
    finding this check exists to avoid."""
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="FLAGFIELD", literals="TRUE", depth=1)

    assert graph.shadowed_assignments_for_member(conn, 1) == []


def test_an_unconditional_write_shadowed_by_another_unconditional_write_is_still_caught():
    """`depth >= 1` is only required of the *earlier* write -- an
    unconditional write pointlessly overwritten by a later unconditional
    write is the same dead-store shape, just without a branch involved."""
    conn = _conn()
    _rc(conn, 10, "ASSIGN", fields_used="FLAGFIELD", literals="TRUE", depth=0)
    _rc(conn, 20, "ASSIGN", fields_used="FLAGFIELD", literals="FALSE", depth=0)

    assert graph.shadowed_assignments_for_member(conn, 1) == []


def test_multi_field_assign_rows_are_skipped_entirely():
    """Narrow on purpose: a row touching more than one field is ambiguous
    about which field is actually being written vs merely referenced, so it
    is excluded from the scan rather than guessed at."""
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="FLAGFIELD,OTHERFIELD", literals="TRUE", depth=1)
    _rc(conn, 15, "ASSIGN", fields_used="FLAGFIELD", literals="FALSE", depth=0)

    assert graph.shadowed_assignments_for_member(conn, 1) == []


def test_shadowed_assignments_adds_a_gap_and_is_purged_on_rerun():
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="FLAGFIELD", literals="TRUE", depth=1)
    _rc(conn, 15, "ASSIGN", fields_used="FLAGFIELD", literals="FALSE", depth=0)
    conn.commit()

    graph.run_all(conn)
    gaps = conn.execute(
        "SELECT * FROM gap WHERE gap_kind='shadowed_assignment'"
    ).fetchall()
    assert len(gaps) == 1
    assert gaps[0]["line_no"] == 11
    assert gaps[0]["member_id"] == 1
    assert "FLAGFIELD" in gaps[0]["detail"]

    # Re-running derive from the same unchanged facts must not double the gap.
    graph.run_all(conn)
    gaps_again = conn.execute(
        "SELECT * FROM gap WHERE gap_kind='shadowed_assignment'"
    ).fetchall()
    assert len(gaps_again) == 1


def test_natural_boolean_flag_shadowed_by_an_unconditional_reset_is_caught():
    """End-to-end regression for the motivating real case: a hardcoded-ID
    branch sets a debug flag one way per user via bare TRUE/FALSE (not a
    quoted string or number), then an unconditional reset immediately
    follows before anything reads the flag -- this only works now that
    natural.py recognises bare TRUE/FALSE as a literal value at all."""
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    insert(conn, "member", name="TESTPROG", dialect="natural", object_type="subprogram")
    mid = conn.execute("SELECT id FROM member WHERE name='TESTPROG'").fetchone()["id"]
    src = (
        "IF *USER = 'T#21T'\n"
        "  #DEBUG := TRUE\n"
        "ELSE\n"
        "  #DEBUG := FALSE\n"
        "END-IF\n"
        "#DEBUG := FALSE\n"
    )
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    natural.extract(conn, mid, lines, "TESTPROG")
    conn.commit()

    findings = graph.shadowed_assignments_for_member(conn, mid)
    lines_found = {f["line_no"] for f in findings}
    assert lines_found == {2, 4}
    for f in findings:
        assert f["field"] == "DEBUG"
        assert f["override_line"] == 6
