"""Guards on call-edge and entity resolution across dialects."""

from __future__ import annotations


def test_ordline_resolves_to_exactly_one_entity(indexed_db):
    """Supra label matching inside linkpath blocks, plus kind-guessing that
    can differ depending on ingest order, previously produced two ORDLINE
    entities instead of one merged record."""
    conn = indexed_db
    rows = conn.execute("SELECT id, kind FROM entity WHERE UPPER(name)='ORDLINE'").fetchall()
    assert len(rows) == 1, f"expected exactly one ORDLINE entity, found {[dict(r) for r in rows]}"


def test_mill_order_adabas_file_is_merged_not_duplicated(indexed_db, derive_result):
    """The DDM names MILL-ORDER's physical file only by DBID/FNR (FILE-045);
    the FDT names it properly. Left unreconciled, the data model shows two
    stores where there is one -- a phantom entity."""
    assert derive_result["adabas_entities_merged"] == 1
    conn = indexed_db
    placeholder = conn.execute(
        "SELECT 1 FROM entity WHERE kind='adabas_file' AND name LIKE 'FILE-%'"
    ).fetchone()
    assert placeholder is None, "a FILE-nnn placeholder survived reconciliation"
    named = conn.execute(
        "SELECT fnr FROM entity WHERE kind='adabas_file' AND name='MILL-ORDER'"
    ).fetchone()
    assert named is not None and named["fnr"] == "045"


def test_no_idcams_control_cards_mined_as_natural_stack(indexed_db):
    """SYSIN belonging to an IDCAMS step is a control-card stream (REPRO,
    DFSORT, IEBGENER), not a stacked Natural session. Mining it for program
    names manufactures call edges to things like REPRO and OUTDATASET."""
    conn = indexed_db
    rows = conn.execute(
        "SELECT callee_name FROM call_edge WHERE callee_name IN ('REPRO','OUTDATASET')"
    ).fetchall()
    assert not rows, f"IDCAMS control-card tokens leaked into the call graph: {rows}"


def test_mmp0100_reachable_via_cmsynin_stack_not_orphan(indexed_db):
    """MMP0100 is started directly from MMB0100.jcl's CMSYNIN stack (LOGON
    MILLPROD / MMP0100), not CALLNAT'd. Without reading the stack, every
    batch Natural program looks like unreferenced dead code."""
    conn = indexed_db
    edge = conn.execute(
        "SELECT 1 FROM call_edge WHERE callee_name='MMP0100' AND call_kind='EXEC_PGM'"
    ).fetchone()
    assert edge is not None

    gap = conn.execute(
        """
        SELECT COUNT(*) AS n FROM gap g JOIN member m ON m.id = g.member_id
        WHERE g.gap_kind='orphan_module' AND m.name='MMP0100'
        """
    ).fetchone()
    assert gap["n"] == 0


def test_infrastructure_dds_do_not_become_entities(indexed_db):
    """STEPLIB, DDCARD and CMPRINT are infrastructure DDs, not business data
    stores. Registering them inflates the entity count with load libraries
    and print files, which then appear in the data model as undefined
    entities and bury the real gaps."""
    conn = indexed_db
    rows = conn.execute(
        "SELECT name FROM entity WHERE name IN ('STEPLIB','DDCARD','CMPRINT')"
    ).fetchall()
    assert not rows, f"infrastructure DD(s) leaked into entity: {rows}"


def test_external_first_token_is_library_not_callee(indexed_db):
    """ORDENQ's `EXTERNAL "STEELLIB","PRICECALC"` names the library first.
    Recording STEELLIB as a callee fabricates a call edge to something that
    is not a program."""
    conn = indexed_db
    rows = conn.execute(
        "SELECT callee_name FROM call_edge WHERE call_kind='CALL' AND args LIKE 'EXTERNAL declaration%'"
    ).fetchall()
    names = {r["callee_name"] for r in rows}
    assert "STEELLIB" not in names
    assert "PRICECALC" in names
