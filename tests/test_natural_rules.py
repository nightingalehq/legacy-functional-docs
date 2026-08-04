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
