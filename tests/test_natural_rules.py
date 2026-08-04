"""Guards specific to the Natural rule-candidate scanner."""

from __future__ import annotations


def test_masked_literal_is_not_lost_from_the_condition(indexed_db):
    """`IF ORDER-VIEW.ORDER-STATUS NE 'CONF'` must keep CONF in the stored
    condition. Literal masking exists so keyword matching ignores text inside
    quotes; it must never cost the business value the rule actually turns on."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT rc.condition FROM rule_candidate rc
        JOIN member m ON m.id = rc.member_id
        WHERE m.name='MMP0100' AND rc.construct='IF' AND rc.condition LIKE '%ORDER-STATUS%'
        """
    ).fetchone()
    assert row is not None, "expected an IF rule on ORDER-STATUS for MMP0100"
    assert "CONF" in row["condition"], (
        f"masked literal leaked out of the stored condition: {row['condition']!r}"
    )


def test_write_audit_is_internal_subroutine_not_missing_module(indexed_db):
    """WRITE-AUDIT is DEFINE SUBROUTINE'd inside MMP0100 and PERFORM'd from
    within it. It must resolve as PERFORM_INTERNAL, not surface as an
    unresolved external call -- that false positive wastes real SME review
    time chasing a module that was never missing."""
    conn = indexed_db
    edge = conn.execute(
        """
        SELECT ce.call_kind, ce.resolved FROM call_edge ce
        JOIN member m ON m.id = ce.caller_id
        WHERE m.name='MMP0100' AND ce.callee_name='WRITE-AUDIT'
        """
    ).fetchone()
    assert edge is not None
    assert edge["call_kind"] == "PERFORM_INTERNAL"
    assert edge["resolved"] == 1

    gap = conn.execute(
        "SELECT COUNT(*) AS n FROM gap WHERE gap_kind='unresolved_call' AND detail LIKE '%WRITE-AUDIT%'"
    ).fetchone()
    assert gap["n"] == 0


def test_literal_bearing_moves_are_captured_as_rule_candidates(indexed_db):
    """`MOVE 'RLSD' TO ORDER-VIEW.ORDER-STATUS` assigns a business status
    code and must surface as a rule candidate -- previously MOVE/ADD/etc.
    were matched and silently discarded regardless of content."""
    conn = indexed_db
    rows = {
        r["line_no"]: r["literals"]
        for r in conn.execute(
            """
            SELECT rc.line_no, rc.literals FROM rule_candidate rc
            JOIN member m ON m.id = rc.member_id
            WHERE m.name='MMP0100' AND rc.construct='MOVE'
            """
        ).fetchall()
    }
    assert rows.get(54) == "RLSD"
    assert rows.get(56) == "PART"
    assert rows.get(35) == "10"


def test_pure_accumulation_without_a_literal_is_not_captured(indexed_db):
    """`ADD STOCK-VIEW.AVAIL-WEIGHT TO #AVAIL-TOTAL` has no literal operand --
    it's an accumulator, not a business threshold, and must not be captured
    as a rule candidate (that would bury the moves that do matter)."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT 1 FROM rule_candidate rc JOIN member m ON m.id = rc.member_id
        WHERE m.name='MMP0100' AND rc.construct='ADD' AND rc.condition LIKE '%AVAIL-TOTAL%'
        """
    ).fetchone()
    assert row is None
