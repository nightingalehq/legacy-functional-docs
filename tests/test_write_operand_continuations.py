"""Guards on WRITE operand-list continuation leads beyond column-spec tokens
(sibling of test_column_spec_continuations.py's issue 4.11a/#24).

MMP9550.nsp's WRITE statement wraps across three lines without any
report-writer column-spec token ("5T"/"30T") riding along -- instead a
quoted-literal lead (CONTINUATION_LEAD_QUOTE), a bare "/" lead
(CONTINUATION_LEAD_SLASH), and a bare field-reference lead
(CONTINUATION_LEAD_FIELD) fold them in, scoped to WRITE/DISPLAY/PRINT/INPUT/
REINPUT the same way CONTINUATION_LEAD_COLSPEC already is (see the fold
loop's RE_WRITE check in natural.py).
"""

from __future__ import annotations

import sqlite3

from mfdoc.db import SCHEMA
from mfdoc.dialects import natural


def _extract(src: str, member_name: str = "TESTMOD"):
    """Same isolated in-memory-index pattern as test_natural_rules.py's
    helper of the same name."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, ?, 'natural')", (member_name,))
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    natural.extract(conn, 1, lines, member_name)
    return conn


def test_quote_slash_and_field_leads_fold_into_one_write_interaction(indexed_db):
    conn = indexed_db
    rows = conn.execute(
        """
        SELECT i.line_no, i.kind, i.fields FROM interaction i
          JOIN member m ON m.id = i.member_id
         WHERE m.name='MMP9550' AND i.kind='WRITE'
        """
    ).fetchall()
    assert len(rows) == 1, "the three physical lines must fold into a single WRITE interaction"
    row = rows[0]
    assert row["line_no"] == 18  # the WRITE keyword's own line
    for token in ("#BATCH-SEQ", "#BATCH-STATUS"):
        assert token in row["fields"], f"{token!r} missing -- continuation lines weren't folded"


def test_folded_continuation_lines_do_not_also_raise_their_own_gap(indexed_db):
    """Same accepted double-visit as MMP9500's own test: each folded
    continuation line is still visited on its own afterwards and correctly
    doesn't stand alone as a statement, but that must not raise a second,
    redundant unparsed_line gap now that folded_lines suppresses it."""
    conn = indexed_db
    rows = conn.execute(
        """
        SELECT g.line_no FROM gap g JOIN member m ON m.id = g.member_id
         WHERE m.name='MMP9550' AND g.gap_kind='unparsed_line' AND g.line_no IN (19, 20, 21)
        """
    ).fetchall()
    assert rows == []


def test_bare_assignment_after_a_write_is_not_folded_into_it():
    """A genuine bare assignment statement ("#FIELD := ...") starting with
    "#" must not be mistaken for another WRITE operand just because it
    follows one -- CONTINUATION_LEAD_FIELD excludes any line containing
    ":=" for exactly this reason."""
    conn = _extract(
        "WRITE 'BATCH LOG'\n"
        "#BATCH-STATUS := 'DONE'\n"
    )
    row = conn.execute("SELECT fields FROM interaction WHERE kind='WRITE'").fetchone()
    assert row is not None
    assert "#BATCH-STATUS" not in (row["fields"] or ""), (
        "the assignment on line 2 must not have folded into the WRITE on line 1"
    )
    assert conn.execute(
        "SELECT 1 FROM rule_candidate WHERE construct='ASSIGN'"
    ).fetchone() is not None, "the assignment must still be recognised as its own statement"
