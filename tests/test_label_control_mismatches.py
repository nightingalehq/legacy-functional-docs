"""Guards on graph.label_control_pairs_for_member/label_control_mismatches --
the heuristic check for an on-screen label literal and an internal control
(option/mode/action) literal set in the same branch that don't obviously
agree. Deliberately never asserts the two are *wrong* -- see the module
docstring on `label_control_pairs_for_member` -- only that a human should
confirm they mean the same thing.

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


def test_a_label_and_a_differently_worded_control_literal_in_the_same_branch_is_flagged():
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="PF-LABEL", literals="'Send'", depth=1)
    _rc(conn, 12, "ASSIGN", fields_used="#SEND-MODE", literals="'UPDATE'", depth=1)

    findings = graph.label_control_pairs_for_member(conn, 1)
    assert len(findings) == 1
    f = findings[0]
    assert f["label_field"] == "PF-LABEL" and f["label_literal"] == "'Send'"
    assert f["control_field"] == "#SEND-MODE" and f["control_literal"] == "'UPDATE'"
    assert f["label_line"] == 11 and f["control_line"] == 12


def test_a_label_and_a_similarly_worded_control_literal_is_not_flagged():
    """One literal's normalized form contains the other's -- close enough to
    count as obviously consistent, nothing to ask about."""
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="PF-LABEL", literals="'Replace'", depth=1)
    _rc(conn, 12, "ASSIGN", fields_used="#ACTION-MODE", literals="'REPLACE-REC'", depth=1)

    assert graph.label_control_pairs_for_member(conn, 1) == []


def test_a_label_with_no_control_field_in_the_same_branch_is_not_flagged():
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="PF-LABEL", literals="'Send'", depth=1)

    assert graph.label_control_pairs_for_member(conn, 1) == []


def test_a_label_set_in_one_branch_never_pairs_with_a_control_field_in_a_later_branch():
    """Leaving the branch (a row at a shallower depth) must invalidate the
    pending label -- otherwise an unrelated control field set after the
    branch has already ended would be wrongly paired with it."""
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="PF-LABEL", literals="'Send'", depth=1)
    _rc(conn, 12, "END-IF", depth=0)
    _rc(conn, 13, "IF", depth=0)
    _rc(conn, 14, "ASSIGN", fields_used="#SEND-MODE", literals="'UPDATE'", depth=1)

    assert graph.label_control_pairs_for_member(conn, 1) == []


def test_a_label_pairs_with_a_control_field_one_level_deeper_in_a_nested_block():
    """A control field set inside a nested IF entered from the same branch
    the label opened is still "the same branch" for this check's purposes --
    only leaving back out past the label's own depth (see the test above)
    should invalidate it, not simply nesting one level deeper than it."""
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="PF-LABEL", literals="'Send'", depth=1)
    _rc(conn, 12, "IF", depth=1)
    _rc(conn, 13, "ASSIGN", fields_used="#SEND-MODE", literals="'UPDATE'", depth=2)

    findings = graph.label_control_pairs_for_member(conn, 1)
    assert len(findings) == 1
    assert findings[0]["label_field"] == "PF-LABEL"
    assert findings[0]["control_field"] == "#SEND-MODE"
    assert findings[0]["control_line"] == 13


def test_multi_field_assign_rows_are_skipped_entirely():
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="PF-LABEL,OTHERFIELD", literals="'Send'", depth=1)
    _rc(conn, 12, "ASSIGN", fields_used="#SEND-MODE", literals="'UPDATE'", depth=1)

    assert graph.label_control_pairs_for_member(conn, 1) == []


def test_label_control_mismatches_adds_a_gap_and_is_purged_on_rerun():
    conn = _conn()
    _rc(conn, 10, "IF", depth=0)
    _rc(conn, 11, "ASSIGN", fields_used="PF-LABEL", literals="'Send'", depth=1)
    _rc(conn, 12, "ASSIGN", fields_used="#SEND-MODE", literals="'UPDATE'", depth=1)
    conn.commit()

    graph.run_all(conn)
    gaps = conn.execute(
        "SELECT * FROM gap WHERE gap_kind='label_control_mismatch'"
    ).fetchall()
    assert len(gaps) == 1
    assert gaps[0]["member_id"] == 1
    assert gaps[0]["line_no"] == 12
    assert gaps[0]["severity"] == "medium"
    assert "PF-LABEL" in gaps[0]["detail"] and "#SEND-MODE" in gaps[0]["detail"]

    # Re-running derive from the same unchanged facts must not double the gap.
    graph.run_all(conn)
    gaps_again = conn.execute(
        "SELECT * FROM gap WHERE gap_kind='label_control_mismatch'"
    ).fetchall()
    assert len(gaps_again) == 1
