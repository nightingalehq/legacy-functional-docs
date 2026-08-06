"""Guards on reporting-mode LOOP/depth inference (issue 4.6/#5).

MMP9600.nsp is unambiguous (LOOP's body is consistently more indented than
the LOOP line) and gets an inferred rule_candidate for LOOP plus a
medium-severity gap. MMP9700.nsp is ambiguous (the body isn't more
indented) and must fall back to the pre-existing high-severity gap with no
inferred rule_candidate at all -- a wrong-looking confident guess is worse
than an admitted gap.
"""

from __future__ import annotations


def test_unambiguous_loop_gets_an_inferred_rule_candidate(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT rc.depth, rc.confidence, rc.construct FROM rule_candidate rc
          JOIN member m ON m.id = rc.member_id
         WHERE m.name='MMP9600' AND rc.construct='LOOP'
        """
    ).fetchone()
    assert row is not None, "an unambiguous LOOP must be recorded as a rule_candidate"
    assert row["confidence"] == "inferred"
    assert row["depth"] == 0


def test_unambiguous_reporting_mode_gets_the_medium_severity_gap(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT g.severity, g.detail FROM gap g JOIN member m ON m.id = g.member_id
         WHERE m.name='MMP9600' AND g.gap_kind='reporting_mode'
        """
    ).fetchone()
    assert row is not None
    assert row["severity"] == "medium"
    assert "inferred" in row["detail"]


def test_ambiguous_loop_produces_no_inferred_rule_candidate(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT 1 FROM rule_candidate rc JOIN member m ON m.id = rc.member_id
         WHERE m.name='MMP9700' AND rc.construct='LOOP'
        """
    ).fetchone()
    assert row is None, "ambiguous indentation must never produce a confident-looking guess"


def test_ambiguous_reporting_mode_keeps_the_original_high_severity_gap(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT g.severity, g.detail FROM gap g JOIN member m ON m.id = g.member_id
         WHERE m.name='MMP9700' AND g.gap_kind='reporting_mode'
        """
    ).fetchone()
    assert row is not None
    assert row["severity"] == "high"
    assert "unreliable" in row["detail"]


def test_structured_mode_members_are_unaffected(indexed_db):
    """MMP0100 (structured) must not pick up any LOOP-inference behaviour --
    depth inference is gated strictly to mode == 'reporting'."""
    conn = indexed_db
    row = conn.execute("SELECT mode FROM member WHERE name='MMP0100'").fetchone()
    assert row["mode"] == "structured"
    row = conn.execute(
        "SELECT 1 FROM rule_candidate WHERE member_id=(SELECT id FROM member WHERE name='MMP0100') "
        "AND confidence='inferred'"
    ).fetchone()
    assert row is None
