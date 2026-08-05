"""Guards on generic statement-label handling (issue 4.11b/#25).

MMP9400.nsp exercises labelled statements (`SETA. MOVE ...`) whose label
prefix would otherwise defeat every verb pattern's `^\\s*` anchor. Also
guards that this doesn't disturb the R#/F#/H# loop-label convention
(issue 4.3/#2) those verbs already handle inline -- see
test_loop_label_resolution.py, which exercises MMP9200.nsp unchanged.
"""

from __future__ import annotations


def test_labelled_move_extracts_the_literal_as_a_rule_candidate(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT rc.condition, rc.literals, rc.raw FROM rule_candidate rc
          JOIN member m ON m.id = rc.member_id
         WHERE m.name='MMP9400' AND rc.construct='MOVE'
        """
    ).fetchone()
    assert row is not None, "labelled MOVE must still be captured as a rule candidate"
    assert row["condition"] == "MOVE 'CONF' TO #STATUS"
    assert row["literals"] == "CONF", "the literal must survive unmasking despite the label prefix"


def test_labelled_callnat_records_a_call_edge(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT ce.callee_name, ce.call_kind FROM call_edge ce
          JOIN member m ON m.id = ce.caller_id
         WHERE m.name='MMP9400' AND ce.call_kind='CALLNAT'
        """
    ).fetchone()
    assert row is not None, "labelled CALLNAT must still be captured as a call edge"
    assert row["callee_name"] == "PROGA"


def test_labelled_if_extracts_the_condition(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT rc.condition FROM rule_candidate rc
          JOIN member m ON m.id = rc.member_id
         WHERE m.name='MMP9400' AND rc.construct='IF'
        """
    ).fetchone()
    assert row is not None, "labelled IF must still be captured as a rule candidate"
    assert row["condition"] == "#STATUS = 'CONF'"


def test_unrecognised_statement_after_a_label_stays_an_honest_gap(indexed_db):
    """A label doesn't make an unrecognised verb match -- must not guess."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT g.raw FROM gap g JOIN member m ON m.id = g.member_id
         WHERE m.name='MMP9400' AND g.gap_kind='unparsed_line'
        """
    ).fetchone()
    assert row is not None
    assert row["raw"] == "SETD. FROBNICATE #STATUS"


def test_r_prefixed_loop_labels_are_unaffected_by_generic_label_stripping(indexed_db):
    """MMP9200's R#/F#/H# label convention (issue 4.3) must resolve exactly
    as before -- the generic fallback must never pre-empt it."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT da.entity_name, da.via_view, da.confidence FROM data_access da
          JOIN member m ON m.id = da.member_id
         WHERE m.name='MMP9200' AND da.verb='UPDATE'
        """
    ).fetchone()
    assert row is not None
    assert row["entity_name"] == "MILL-ORDER"
    assert row["via_view"] == "ORDER-VIEW"
    assert row["confidence"] == "verified"
