"""Guards on COMPRESS/SEPARATE operand-list continuations (sibling of
test_write_operand_continuations.py).

COMPRESS/SEPARATE build the same kind of multi-operand list as
WRITE/DISPLAY/PRINT (RE_COMPRESS_SEPARATE, scoped into the fold loop's
operand-continuation check alongside WRITE/INPUT/REINPUT). MMP9800.nsp's
own COMPRESS fixture only exercises a continuation line that already
carries the closing INTO keyword; MMP9560.nsp exercises a bare
field-reference operand line *before* INTO.
"""

from __future__ import annotations


def test_bare_field_operand_before_into_folds_into_the_compress(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT condition FROM rule_candidate rc JOIN member m ON m.id = rc.member_id
         WHERE m.name='MMP9560' AND rc.construct='COMPRESS' AND rc.line_no=12
        """
    ).fetchone()
    assert row is not None, "expected one folded COMPRESS rule_candidate at line 12"
    assert "#BATCH-SEQ" in row["condition"], (
        f"bare field-reference continuation not folded into the COMPRESS condition: {row['condition']!r}"
    )
    assert "#MESSAGE" in row["condition"], (
        f"INTO target not folded into the COMPRESS condition: {row['condition']!r}"
    )


def test_folded_continuation_line_does_not_also_raise_its_own_gap(indexed_db):
    conn = indexed_db
    rows = conn.execute(
        """
        SELECT g.line_no FROM gap g JOIN member m ON m.id = g.member_id
         WHERE m.name='MMP9560' AND g.gap_kind='unparsed_line' AND g.line_no IN (13, 14)
        """
    ).fetchall()
    assert rows == []
