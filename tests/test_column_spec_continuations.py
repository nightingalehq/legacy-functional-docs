"""Guards on report-writer column-spec continuation folding (issue 4.11a/#24).

MMP9500.nsp's WRITE statement wraps across three lines using Natural's
"nT"/"nX" column-position tokens ("5T" = tab to column 5) on their own
continuation lines -- a shape CONTINUATION_LEAD (keyword-only) doesn't
cover. CONTINUATION_LEAD_COLSPEC folds them in, scoped to WRITE/DISPLAY/
PRINT statements only (see the fold loop's RE_WRITE check in natural.py).
"""

from __future__ import annotations


def test_column_spec_continuations_fold_into_one_write_interaction(indexed_db):
    conn = indexed_db
    rows = conn.execute(
        """
        SELECT i.line_no, i.kind, i.fields FROM interaction i
          JOIN member m ON m.id = i.member_id
         WHERE m.name='MMP9500' AND i.kind='WRITE'
        """
    ).fetchall()
    assert len(rows) == 1, "the three physical lines must fold into a single WRITE interaction"
    row = rows[0]
    assert row["line_no"] == 13  # the WRITE keyword's own line
    # The quoted literals are masked (a pre-existing, unrelated limitation of
    # _match_interaction's WRITE branch -- it doesn't call orig() on `rest`),
    # but the unquoted column-spec tokens and field names are not literals,
    # so they survive and prove the continuation lines were actually folded
    # in rather than the WRITE interaction only ever seeing its first line.
    for token in ("5T", "30T", "#HEAT-NO", "#CAST-DATE"):
        assert token in row["fields"], f"{token!r} missing -- continuation lines weren't folded"


def test_folded_continuation_lines_are_still_visited_but_stay_harmless_gaps(indexed_db):
    """Same accepted quirk as the 4.5/MMP9000 fixture: a line that's already
    been folded into the preceding statement is still visited on its own by
    the main loop, and correctly doesn't match as a standalone statement."""
    conn = indexed_db
    rows = conn.execute(
        """
        SELECT g.line_no, g.raw FROM gap g JOIN member m ON m.id = g.member_id
         WHERE m.name='MMP9500' AND g.gap_kind='unparsed_line'
         ORDER BY g.line_no
        """
    ).fetchall()
    assert [r["line_no"] for r in rows] == [14, 15]
    assert rows[0]["raw"] == "5T 'HEAT NO:' #HEAT-NO"
    assert rows[1]["raw"] == "30T 'DATE:' #CAST-DATE"
