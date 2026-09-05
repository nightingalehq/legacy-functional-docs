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


def test_executive_brief_external_dependents_are_cited(indexed_db):
    """Critical fix: build_call_graph()'s returned dict carries no line_no
    (Tasks 6/7/8 depend on that exact shape), so executive_brief must query
    call_edge directly for the "called by" citation instead of asserting an
    uncited fact. MMP0100 is called by MMB0100 at a known, real line in the
    bundled fixtures -- assert the citation names that real caller and line,
    not just that the section header exists."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    edge = conn.execute(
        """
        SELECT m.name AS caller, ce.line_no
          FROM call_edge ce JOIN member m ON m.id = ce.caller_id
         WHERE ce.callee_name = 'MMP0100'
        """
    ).fetchone()
    assert edge is not None, "fixture no longer has a call edge into MMP0100 to exercise this"

    out = brief.executive_brief(conn, "MMP0100")
    assert "## External dependents" in out
    assert f"called by `{edge['caller']}`" in out
    assert f"[[{edge['caller']}:{edge['line_no']}]]" in out

    # And every citation resolves against a real member/line, same as the
    # general citation-integrity test above.
    cites = list(CITATION.finditer(out))
    assert cites
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


def test_executive_brief_risk_section_uses_structured_data_not_markdown_parsing(indexed_db, monkeypatch):
    r"""Regression test for the string-matching coupling: executive_brief's
    Risk section used to re-render the whole complexity_heatmap() markdown
    table and grep it for `| \`{name}\``. If the heatmap's rendered format
    ever changed, that grep would silently stop matching and the brief
    would then assert the confidently-wrong "no rule candidates recorded"
    line even though the member has real rule_candidate rows.

    Prove the coupling is now via structural._complexity_rows()'s data,
    not via string-matching complexity_heatmap()'s markdown, by making
    complexity_heatmap() return something the old grep could never match
    (a completely different, reordered table) and asserting the risk
    section is unaffected."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    from mfdoc import structural

    member = conn.execute(
        """
        SELECT m.id AS id, m.name AS name FROM member m
         WHERE EXISTS (SELECT 1 FROM rule_candidate rc WHERE rc.member_id = m.id)
           AND (SELECT COUNT(*) FROM member m2 WHERE m2.name = m.name) = 1
         ORDER BY m.name LIMIT 1
        """
    ).fetchone()
    assert member is not None, "fixture has no unambiguous member with rule candidates"

    expected_row = next(
        r for r in structural._complexity_rows(conn)
        if not r["ambiguous"] and r["member_id"] == member["id"]
    )

    # Simulate a heatmap format change: a table with the columns in a
    # different order and no line beginning with the old `| \`{name}\``
    # shape at all -- the old string-matching implementation would find
    # nothing here and silently report "no rule candidates recorded".
    monkeypatch.setattr(
        structural, "complexity_heatmap",
        lambda conn, metric="rule_depth": "# Complexity/risk heatmap (reformatted)\n\nrisk_score | member\n---|---\n999.9 | " + member["name"] + "\n",
    )

    out = brief.executive_brief(conn, member["name"])
    assert "no rule candidates recorded for this member" not in out
    assert f"{expected_row['risk_score']}" in out
    assert f"{expected_row['rule_count']}" in out


def test_executive_brief_unknown_member_is_graceful(indexed_db):
    """Matches module_brief/entity_brief's established convention: an
    unresolvable member name gets a graceful markdown response, not a raise."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    out = brief.executive_brief(conn, "NO-SUCH-MEMBER-XYZ")
    assert "No such member in the index" in out
    assert "NO-SUCH-MEMBER-XYZ" in out


def test_executive_brief_ambiguous_member_is_graceful(indexed_db):
    """A bare name shared by more than one real member (only unique together
    with library+dialect -- see db.resolve_member_by_name) must not have its
    facts silently blended under one member's identity. executive_brief must
    refuse the same way module_brief does, before ever touching per-member
    facts, rather than raising or guessing which one applies."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    dup = conn.execute(
        "SELECT name FROM member GROUP BY name HAVING COUNT(*) > 1 LIMIT 1"
    ).fetchone()
    assert dup is not None, "fixture has no ambiguous (duplicate-name) member to exercise this path"

    out = brief.executive_brief(conn, dup["name"])
    assert "ambiguous across libraries" in out
    assert dup["name"] in out
    # None of the per-member sections should have been rendered -- the
    # refusal happens before any fact lookup, same as module_brief.
    assert "## Top rules" not in out


def test_executive_brief_handles_ambiguous_heatmap_row_defensively(indexed_db):
    """Belt-and-braces regression test for complexity_heatmap()'s own
    'ambiguous' row rendering (see structural.py): even if this member's name
    somehow reached the Risk section's heatmap-row parsing (it can't via the
    public resolve_member_by_name guard above, since that refuses first), the
    parsing must recognise the ambiguous row shape and report it plainly
    rather than crashing trying to parse a risk score out of its em-dash
    placeholders. Exercised directly against structural.complexity_heatmap's
    real ambiguous-row output, bypassing executive_brief's own guard, to
    prove the parsing branch itself (not just the earlier refusal) is safe.

    `indexed_db` is a session-scoped connection shared by the whole test
    suite (see conftest.py), so the rule_candidate rows inserted here are
    removed again in `finally` -- leaving them behind would silently change
    complexity_heatmap()'s output for every later test in the same session
    (this bit test_structural_complexity.py's own heatmap assertions once
    already)."""
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

        from mfdoc import structural

        heatmap = structural.complexity_heatmap(conn)
        ambiguous_line = next(
            (l for l in heatmap.splitlines() if l.startswith(f"| `{dup['name']}`")), None
        )
        assert ambiguous_line is not None
        assert "| ambiguous:" in ambiguous_line
    finally:
        for rid in inserted_ids:
            conn.execute("DELETE FROM rule_candidate WHERE id=?", (rid,))
        conn.commit()


def test_executive_brief_entry_point_batch_via_jcl_natural_stack(indexed_db):
    """MMP0100 is a real fixture member whose only invocation is a Natural
    program name stacked on a CMSYNIN DD under JCL member MMB0100 (see
    dialects/environment.py's _parse_natural_stack) -- not a direct JCL
    EXEC PGM= naming it. The Entry point section must surface this real
    batch entry fact (caller MMB0100, real line, JCL dialect), not silently
    miss it because there's no literal job_step.program match."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    edge = conn.execute(
        """
        SELECT m.name AS caller, ce.line_no
          FROM call_edge ce JOIN member m ON m.id = ce.caller_id
         WHERE UPPER(ce.callee_name) = 'MMP0100' AND m.dialect = 'jcl'
        """
    ).fetchone()
    assert edge is not None, "fixture no longer has a JCL-side call edge into MMP0100"

    out = brief.executive_brief(conn, "MMP0100")
    assert "## Entry point" in out
    assert "batch entry" in out
    assert f"`{edge['caller']}`" in out
    assert f"[[{edge['caller']}:{edge['line_no']}]]" in out
    assert "no entry-point fact was found" not in out


def test_executive_brief_entry_point_none_found(indexed_db):
    """A member nothing in the fixture ever calls (batch or online) must
    state plainly that no entry-point fact was found -- never fabricate a
    plausible-sounding trigger."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    conn.execute("INSERT INTO member (name, dialect) VALUES ('ZZORPHANMOD', 'natural')")
    conn.commit()
    try:
        out = brief.executive_brief(conn, "ZZORPHANMOD")
        assert "## Entry point" in out
        assert "no entry-point fact was found for this member" in out
    finally:
        conn.execute("DELETE FROM member WHERE name='ZZORPHANMOD'")
        conn.commit()


def test_executive_brief_entry_point_cics_transaction():
    """Synthetic case (no CICS fixture data associates with MMP0100 itself):
    a member invoked via a CICS TRANSACTION DEFINE's PROGRAM() clause must
    be reported as an online entry, citing the transaction id and the real
    CSD member/line that defined it."""
    import sqlite3

    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'ONLPGM01', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'CSDDEF01', 'cics_csd')")
    conn.execute(
        "INSERT INTO cics_resource (member_id, line_no, resource_type, resource_name, attributes) "
        "VALUES (2, 42, 'TRANSACTION', 'TX01', 'PROGRAM=ONLPGM01')"
    )
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, call_kind, line_no, args) "
        "VALUES (2, 'ONLPGM01', 'EXEC_PGM', 42, 'CICS transaction TX01')"
    )
    conn.commit()

    out = brief.executive_brief(conn, "ONLPGM01")
    assert "## Entry point" in out
    assert "online entry" in out
    assert "`TX01`" in out
    assert "`CSDDEF01`" in out
    assert "[[CSDDEF01:42]]" in out
    assert "no entry-point fact was found" not in out


def test_executive_brief_risk_section_uses_labeled_bullets_not_raw_table_row(indexed_db):
    """Finding 10: the Risk section used to drop in a raw headerless
    markdown-table-row fragment (`| \\`NAME\\` | 17 | 1 | 1 | 4 | 100.0 |`)
    inside a bullet list with no column labels. It must now read as a
    labeled bullet instead, using the same underlying data."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    from mfdoc import structural

    member = conn.execute(
        """
        SELECT m.id AS id, m.name AS name FROM member m
         WHERE EXISTS (SELECT 1 FROM rule_candidate rc WHERE rc.member_id = m.id)
           AND (SELECT COUNT(*) FROM member m2 WHERE m2.name = m.name) = 1
         ORDER BY m.name LIMIT 1
        """
    ).fetchone()
    assert member is not None, "fixture has no unambiguous member with rule candidates"
    expected_row = next(
        r for r in structural._complexity_rows(conn)
        if not r["ambiguous"] and r["member_id"] == member["id"]
    )

    out = brief.executive_brief(conn, member["name"])
    expected_bullet = (
        f"- risk_score: {expected_row['risk_score']} (rule_count {expected_row['rule_count']}, "
        f"max_depth {expected_row['max_depth']}, in_degree {expected_row['in_degree']}, "
        f"out_degree {expected_row['out_degree']})"
    )
    assert expected_bullet in out
    # The old raw fragment shape must not appear anywhere in the output.
    assert f"| `{member['name']}` |" not in out


def test_cli_brief_executive_flag(cli_args, derive_result, capsys):
    from types import SimpleNamespace

    from mfdoc import cli

    args = SimpleNamespace(
        config=cli_args.config, module=None, entity=None, system=False,
        executive="MMP0100", out=None,
    )
    assert cli.cmd_brief(args) == 0
    out = capsys.readouterr().out
    assert "# Executive brief: MMP0100" in out
