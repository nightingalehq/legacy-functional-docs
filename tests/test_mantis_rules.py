"""Guards specific to the Mantis rule-candidate scanner."""

from __future__ import annotations

import sqlite3

from mfdoc.db import SCHEMA
from mfdoc.dialects import mantis


def _extract(src: str, member_name: str = "TESTMOD"):
    """Isolated in-memory index for a hand-written snippet -- used for
    shapes not present in the bundled fixtures (an internal DO/PERFORM to
    a locally-declared ENTRY point; a GET key built from a prior
    assignment), rather than editing the shared, line-number-sensitive
    ORDENQ.mantis/SCRNENT.mantis fixtures."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, ?, 'mantis')", (member_name,))
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    mantis.extract(conn, 1, lines, member_name)
    return conn


def test_do_call_to_local_entry_point_resolves_as_internal():
    """A `DO`/`PERFORM` target matching one of this member's own ENTRY
    points must resolve as PERFORM_INTERNAL, the same false-positive-
    avoidance natural.py already does for DEFINE SUBROUTINE -- otherwise
    every internal paragraph call looks like a missing external module in
    the gap register."""
    conn = _extract(
        'PROGRAM "TESTMOD"\n'
        "ENTRY MAIN\n"
        "  DO BUILD_KEY\n"
        "EXIT\n"
        "\n"
        "ENTRY BUILD_KEY\n"
        "  X=1\n"
        "EXIT\n"
    )
    edge = conn.execute(
        "SELECT call_kind, resolved, callee_id FROM call_edge WHERE callee_name='BUILD_KEY'"
    ).fetchone()
    assert edge is not None
    assert edge["call_kind"] == "PERFORM_INTERNAL"
    assert edge["resolved"] == 1
    assert edge["callee_id"] == 1

    routines = conn.execute("SELECT name, start_line, end_line FROM routine ORDER BY start_line").fetchall()
    assert [r["name"] for r in routines] == ["MAIN", "BUILD_KEY"]


def test_key_built_from_prior_assignment_is_traced():
    """The exact shape a key gets built across lines before it's used --
    `LOOKUP_KEY="H"+BUILD_PART(...)+...` then `GET WIDGETFILE01(LOOKUP_KEY)FIRST` --
    must resolve `data_access.key_source_line`/`key_source_expr` back to the
    assignment, not leave the key as an opaque variable name. Without this,
    a narrator has no cue that LOOKUP_KEY is a composed value worth
    explaining rather than a bare token."""
    conn = _extract(
        'PROGRAM "TESTMOD"\n'
        "ENTRY MAIN\n"
        '  LOOKUP_KEY="H"+BUILD_PART(1,1,5)+BUILD_PART(1,7,8)+BUILD_PART(1,10,10)\n'
        "  GET WIDGETFILE01(LOOKUP_KEY)FIRST\n"
        "EXIT\n"
    )
    row = conn.execute(
        "SELECT entity_name, key_expr, key_source_line, key_source_expr FROM data_access WHERE verb='GET'"
    ).fetchone()
    assert row is not None
    assert row["entity_name"] == "WIDGETFILE01"
    assert row["key_expr"] == "WIDGETFILE01(LOOKUP_KEY)FIRST"
    assert row["key_source_line"] == 3
    assert row["key_source_expr"] == '"H"+BUILD_PART(1,1,5)+BUILD_PART(1,7,8)+BUILD_PART(1,10,10)'


def test_key_source_is_none_when_no_prior_assignment_found():
    """A key variable with no preceding assignment in this member (e.g. a
    parameter, or genuinely not traceable from the supplied source) must
    leave key_source_line/key_source_expr NULL -- never guessed."""
    conn = _extract(
        'PROGRAM "TESTMOD"\n'
        "ENTRY MAIN\n"
        "  GET WIDGETFILE01(SOME_KEY)FIRST\n"
        "EXIT\n"
    )
    row = conn.execute(
        "SELECT key_source_line, key_source_expr FROM data_access WHERE verb='GET'"
    ).fetchone()
    assert row["key_source_line"] is None
    assert row["key_source_expr"] is None


def test_if_else_branch_extent_and_pairing_is_recorded():
    """The exact shape reported: an IF's error/hold branch is easy to
    document, but the ELSE's GET/DELETE must not silently disappear.
    end_line on the IF must stop at the ELSE (not swallow its branch too),
    pair_line_no on the ELSE must point back to the IF, and end_line on
    the ELSE must reach the matching END."""
    conn = _extract(
        'PROGRAM "TESTMOD"\n'
        "ENTRY MAIN\n"
        "  IF RECORD_NOT_FOUND = 1\n"
        '    MSG="no matching record found"\n'
        "  ELSE\n"
        "    GET WIDGETFILE01(SCHED_KEY)FIRST\n"
        "    DELETE WIDGETFILE02(SCHED_KEY)\n"
        "  END\n"
        "EXIT\n"
    )
    if_row = conn.execute("SELECT line_no, end_line FROM rule_candidate WHERE construct='IF'").fetchone()
    else_row = conn.execute(
        "SELECT line_no, pair_line_no, end_line FROM rule_candidate WHERE construct='ELSE'"
    ).fetchone()
    assert if_row["line_no"] == 3
    assert if_row["end_line"] == 8, "IF's end_line must stop at END, not be left unresolved"
    assert else_row["pair_line_no"] == 3
    assert else_row["line_no"] == 5
    assert else_row["end_line"] == 8


def test_supra_dml_key_arg_is_also_traced():
    """The same backward-trace applies to Supra DML calls
    (`READM(ORDERMST, ORDER_NO)`), not only GET -- the key argument there is
    a bare comma-separated token, not a parenthesised sub-expression."""
    conn = _extract(
        'PROGRAM "TESTMOD"\n'
        "ENTRY MAIN\n"
        '  ORDER_NO="A1"+"B2"\n'
        "  READM(ORDERMST, ORDER_NO)\n"
        "EXIT\n"
    )
    row = conn.execute(
        "SELECT key_source_line, key_source_expr FROM data_access WHERE verb='READM'"
    ).fetchone()
    assert row is not None
    assert row["key_source_line"] == 3
    assert row["key_source_expr"] == '"A1"+"B2"'


def test_continuation_fold_joins_a_condition_wrapped_with_a_quote_marker(indexed_db):
    """`IF ORDER_WT > 500` wrapping onto `'OR CUST_NO = " "` on the next line
    (ORDENQ.mantis's appended VALIDATE_CREDIT_LIMIT entry) must fold into one
    condition rather than being truncated at the first physical line. Unlike
    Natural's implicit continuation, this export style marks a continuation
    line explicitly with a leading `'`, so the fold has no ambiguity to
    resolve -- missing it would still produce a complete-looking but silently
    truncated citation.

    This example is appended after the file's original EXIT rather than
    inserted into MAIN, deliberately -- inserting mid-file would renumber
    every later line and silently stale the line citations already baked
    into the checked-in generated docs under examples/outputs/ that cite
    MAIN's statements."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT rc.condition FROM rule_candidate rc
        JOIN member m ON m.id = rc.member_id
        WHERE m.name='ORDENQ' AND rc.construct='IF' AND rc.condition LIKE '%ORDER_WT%'
        """
    ).fetchone()
    assert row is not None, "expected an IF rule candidate for ORDENQ"
    assert "CUST_NO" in row["condition"], (
        f"condition truncated at the wrap point, lost the OR clause: {row['condition']!r}"
    )


def test_continuation_line_is_still_visited_and_gapped_on_its_own(indexed_db):
    """The fold only fixes the condition it's merged into -- the continuation
    line itself must still get its own source_line row and still fail to
    stand alone as a statement, the same accepted double-visit behaviour
    Natural's own continuation fold relies on (see test_natural_rules.py).
    A regression that skips re-visiting it would silently under-count
    source_lines/code_lines for the member."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT g.raw FROM gap g
        JOIN member m ON m.id = g.member_id
        WHERE m.name='ORDENQ' AND g.gap_kind='unparsed_line' AND g.raw LIKE "%CUST_NO%"
        """
    ).fetchone()
    assert row is not None, "continuation line must still raise its own unparsed_line gap"


def test_orderq_entry_points_recorded_as_routines(indexed_db):
    """ORDENQ.mantis declares two ENTRY points, MAIN and
    VALIDATE_CREDIT_LIMIT (see the module docstring on the continuation-fold
    test above) -- both must land in the `routine` table with resolved
    boundaries, the same grouping Natural's DEFINE SUBROUTINE gets."""
    conn = indexed_db
    rows = conn.execute(
        """
        SELECT r.name, r.kind, r.start_line, r.end_line FROM routine r
        JOIN member m ON m.id = r.member_id
        WHERE m.name='ORDENQ' ORDER BY r.start_line
        """
    ).fetchall()
    names = [r["name"] for r in rows]
    assert names == ["MAIN", "VALIDATE_CREDIT_LIMIT"]
    for r in rows:
        assert r["kind"] == "mantis_entry"
        assert r["end_line"] is not None
        assert r["start_line"] < r["end_line"]


def test_entry_only_program_still_gets_object_type_program(indexed_db):
    """SCRNENT.mantis has no `PROGRAM "name"` self-declaration -- its only
    self-identifying statement is `ENTRY SCRNENT(...)`, the same shape as a
    real site's CICS-style online/screen program. Without this, such a
    member never gets an object_type and silently falls out of every
    batchable-member query (module docs, test-plan, test-gen) with no error
    -- a real client codebase hit exactly this."""
    conn = indexed_db
    row = conn.execute(
        "SELECT object_type FROM member WHERE name='SCRNENT'"
    ).fetchone()
    assert row is not None, "expected SCRNENT to be ingested"
    assert row["object_type"] == "program"


def test_screen_binding_records_the_real_target_name():
    """`.SCREEN alias("PHYSICAL")` must record the physical screen name in
    `variable.view_of`, not a masked placeholder. mask_literals() replaces a
    quoted literal with an equal-length run of NULs before keyword matching
    (RE_VIEW above already documents why); RE_SCREEN matched against that
    masked text, so `view_of` came back as NUL bytes instead of the screen
    name -- silently breaking referenced_entities()'s join to the screen's
    field inventory, and with it every unused_field gap for online members."""
    conn = _extract(
        'PROGRAM "TESTMOD"\n'
        '.SCREEN MAP("REALSCRN")\n'
        "ENTRY MAIN\n"
        "  CONVERSE MAP\n"
        "EXIT\n"
    )
    row = conn.execute(
        "SELECT view_of FROM variable WHERE scope='screen' AND name='MAP'"
    ).fetchone()
    assert row is not None
    assert row["view_of"] == "REALSCRN"


def test_converse_with_a_bare_unquoted_screen_name_is_not_dynamic():
    """Every checked-in fixture (ORDENQ.mantis, SCRNENT.mantis) writes a
    screen name bare (`CONVERSE ORDSCR1`) -- this is the dialect's normal,
    static case, not a variable holding a screen name at runtime. Unlike
    RE_CALL's program targets (where an unquoted name really can be a
    variable, cross-checked against declared `views`), there is no
    equivalent signal for a screen target, so `dynamic` must stay 0 here
    rather than reusing RE_CALL's "not quoted" heuristic, which would
    misflag this ordinary case."""
    conn = _extract(
        'PROGRAM "TESTMOD"\n'
        "ENTRY MAIN\n"
        "  CONVERSE ORDSCR1\n"
        "EXIT\n"
    )
    row = conn.execute(
        "SELECT target, dynamic FROM interaction WHERE kind='CONVERSE' AND line_no=3"
    ).fetchone()
    assert row is not None
    assert row["target"] == "ORDSCR1"
    assert row["dynamic"] == 0
    edge = conn.execute(
        "SELECT dynamic FROM call_edge WHERE callee_name='ORDSCR1' AND call_kind='INCLUDE'"
    ).fetchone()
    assert edge["dynamic"] == 0
