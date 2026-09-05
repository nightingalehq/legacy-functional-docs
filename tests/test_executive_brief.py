# tests/test_executive_brief.py
from __future__ import annotations

from mfdoc import brief, classify
from mfdoc.validate import CITATION


def test_executive_brief_cites_real_lines(indexed_db):
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    # The bundled fixtures do carry a member literally named MMB0100, but it's
    # a bare JCL job step with no rule candidates/data access of its own (see
    # CLAUDE.md's "never commit client-specific content" for why the
    # fixtures stay this generic rather than mirroring a real client
    # program). Fall back to whichever member actually has rule candidates
    # recorded -- that's what guarantees this brief has something real to
    # cite, which is the whole point of this test.
    member = conn.execute(
        """
        SELECT m.name AS name FROM member m WHERE m.name='MMB0100'
           AND EXISTS (SELECT 1 FROM rule_candidate rc WHERE rc.member_id = m.id)
        """
    ).fetchone()
    if member is None:
        member = conn.execute(
            """
            SELECT m.name AS name FROM member m
             WHERE EXISTS (SELECT 1 FROM rule_candidate rc WHERE rc.member_id = m.id)
             ORDER BY m.name LIMIT 1
            """
        ).fetchone()
    assert member is not None, "fixture has no member with rule candidates to exercise citations"
    out = brief.executive_brief(conn, member["name"])
    cites = list(CITATION.finditer(out))
    assert cites, "expected at least one citation in the executive brief"
    for m in cites:
        member_name = m.group("member").upper()
        line = int(m.group("from")) if m.group("from") else None
        row = conn.execute(
            "SELECT (SELECT MAX(line_no) FROM source_line WHERE member_id=member.id) AS maxline "
            "FROM member WHERE UPPER(name)=?", (member_name,)
        ).fetchone()
        assert row is not None, f"citation to unknown member {member_name}"
        if line is not None:
            assert 1 <= line <= row["maxline"]


def test_executive_brief_includes_top_rules_and_risk_section(indexed_db):
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    member = conn.execute("SELECT name FROM member LIMIT 1").fetchone()
    out = brief.executive_brief(conn, member["name"])
    assert "## Top rules" in out
    assert "## Risk" in out
    assert "## External dependents" in out


def test_executive_brief_unknown_member_raises(indexed_db):
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    import pytest

    with pytest.raises(ValueError):
        brief.executive_brief(conn, "NO-SUCH-MEMBER-XYZ")


def test_executive_brief_handles_ambiguous_member_risk_row(indexed_db):
    """complexity_heatmap() renders an explicit 'ambiguous' row (rather than
    a normal risk-score row) for a bare member name shared by more than one
    real member -- executive_brief's Risk section must recognise that row
    shape and report it plainly instead of trying to parse a risk score out
    of dashes, or crashing.

    `indexed_db` is a session-scoped connection shared by the whole test
    suite (see conftest.py), so the rule_candidate rows inserted here to
    force the ambiguous case are removed again in `finally` -- leaving them
    behind would silently change complexity_heatmap()'s output for every
    other test that runs later in the same session (this bit
    test_structural_complexity.py's own heatmap assertions once already)."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    dup = conn.execute(
        "SELECT name FROM member GROUP BY name HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    assert dup is not None, "fixture has no ambiguous (duplicate-name) member to exercise this path"
    ids = conn.execute("SELECT id FROM member WHERE name=?", (dup["name"],)).fetchall()
    inserted_ids = []
    try:
        for i in ids:
            cur = conn.execute(
                "INSERT INTO rule_candidate (member_id, line_no, depth, construct, raw) "
                "VALUES (?, 1, 1, 'IF', 'IF X')",
                (i["id"],),
            )
            inserted_ids.append(cur.lastrowid)
        conn.commit()

        out = brief.executive_brief(conn, dup["name"])
        assert "## Risk" in out
        assert "ambiguous" in out.lower()
    finally:
        for rid in inserted_ids:
            conn.execute("DELETE FROM rule_candidate WHERE id=?", (rid,))
        conn.commit()
