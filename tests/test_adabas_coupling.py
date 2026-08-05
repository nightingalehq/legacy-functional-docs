"""Guards on Adabas coupling detection (issue 4.7).

entity_link already supported link_kind='coupled' in the schema; nothing
emitted it. TEST-COUPLE.ddm/.fdt exercise both the recognised case (a
remark naming a target file) and the honest-gap case (a remark that
mentions coupling but names no target).
"""

from __future__ import annotations


def test_coupled_field_produces_an_entity_link_to_the_named_target_file(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT el.link_kind, el.confidence, a.name AS from_name, b.name AS to_name, b.fnr
          FROM entity_link el
          JOIN entity a ON a.id = el.from_entity
          JOIN entity b ON b.id = el.to_entity
         WHERE el.link_kind='coupled'
        """
    ).fetchone()
    assert row is not None, "expected a 'coupled' entity_link from TEST-COUPLE's CROSS-REF field"
    assert row["from_name"] == "TEST-COUPLE"
    # FNR 045 is MILL-ORDER's own file -- the coupled link must resolve to
    # the same, already-reconciled entity, not a second FILE-045 placeholder.
    assert row["to_name"] == "MILL-ORDER"
    assert row["fnr"] == "045"
    # Inferred, not verified: this comes from parsing free remark text, not
    # a structurally-defined field like DBID/FNR.
    assert row["confidence"] == "inferred"


def test_ambiguous_coupling_mention_becomes_a_gap_not_a_guess(indexed_db):
    """AMBIGUOUS-NOTE's remark says "coupling" but names no target file --
    must not invent one, must raise a gap for an SME to resolve instead."""
    conn = indexed_db
    gap = conn.execute(
        """
        SELECT 1 FROM gap g JOIN member m ON m.id = g.member_id
         WHERE g.gap_kind='dynamic_target' AND m.name='TEST-COUPLE'
           AND g.detail LIKE '%AMBIGUOUS-NOTE%coupling%'
        """
    ).fetchone()
    assert gap is not None

    no_guess = conn.execute(
        """
        SELECT 1 FROM entity_link el
          JOIN entity a ON a.id = el.from_entity
         WHERE el.link_kind='coupled' AND a.name='TEST-COUPLE' AND el.link_name LIKE '%AMBIGUOUS%'
        """
    ).fetchone()
    assert no_guess is None, "must not fabricate a coupled link when no target file was named"
