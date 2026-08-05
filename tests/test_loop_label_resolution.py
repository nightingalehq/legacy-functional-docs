"""Guards on loop-label resolution for UPDATE (label) / DELETE (label)
(issue 4.3). MMP9200.nsp exercises the recognised case (F1. opened by a
FIND) and the honest-gap case (a label nothing ever opened).
"""

from __future__ import annotations


def test_update_with_recognised_loop_label_resolves_to_the_opened_view(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT da.entity_name, da.via_view, da.confidence FROM data_access da
          JOIN member m ON m.id = da.member_id
         WHERE m.name='MMP9200' AND da.verb='UPDATE'
        """
    ).fetchone()
    assert row is not None
    assert row["entity_name"] == "MILL-ORDER", "UPDATE (F1.) must resolve to what F1.'s FIND opened"
    assert row["via_view"] == "ORDER-VIEW"
    assert row["confidence"] == "verified"

    gap = conn.execute(
        """
        SELECT 1 FROM gap g JOIN member m ON m.id = g.member_id
         WHERE m.name='MMP9200' AND g.gap_kind='dynamic_target' AND g.detail LIKE 'UPDATE refers%'
        """
    ).fetchone()
    assert gap is None, "a resolved label must not also raise the unresolved-target gap"


def test_delete_with_unrecognised_loop_label_stays_an_honest_gap(indexed_db):
    """DELETE (X9.) -- nothing in MMP9200 ever opens a loop labelled X9.
    Must not guess a target; must raise the dynamic_target gap."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT da.entity_name, da.confidence FROM data_access da
          JOIN member m ON m.id = da.member_id
         WHERE m.name='MMP9200' AND da.verb='DELETE'
        """
    ).fetchone()
    assert row is not None
    assert row["entity_name"] is None
    assert row["confidence"] == "unresolved"

    gap = conn.execute(
        """
        SELECT 1 FROM gap g JOIN member m ON m.id = g.member_id
         WHERE m.name='MMP9200' AND g.gap_kind='dynamic_target' AND g.detail LIKE 'DELETE refers%'
        """
    ).fetchone()
    assert gap is not None
