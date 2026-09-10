"""Tests for docdrift.py -- the deterministic, no-model-call `mfdoc doc-drift`
check (issue #161). Each test builds a small, invented fact store (no real
client content -- member/rule/entity names here are made up for the test,
per CLAUDE.md) and a matching invented document, then asserts drift is
correctly detected (or correctly not flagged) against it.
"""

from __future__ import annotations

import sqlite3
import textwrap

from mfdoc import docdrift
from mfdoc.db import SCHEMA


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _insert_member(conn, name, dialect="natural", library="TESTLIB"):
    cur = conn.execute(
        "INSERT INTO member (name, dialect, library) VALUES (?, ?, ?)",
        (name, dialect, library),
    )
    return cur.lastrowid


def _insert_rule(conn, member_id, line_no):
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, construct, raw) "
        "VALUES (?, ?, 'IF', 'IF X')",
        (member_id, line_no),
    )


_MODULE_DOC_TEMPLATE = textwrap.dedent("""\
    ---
    title: "{module} — widget release"
    doc_type: module
    system: "WGT"
    module: "{module}"
    dialect: "natural"
    library: "TESTLIB"
    generated_by: legacy-functional-docs 0.1.0
    generated_at: "2026-09-10"
    review_status: draft
    reviewers: []
    confidence_summary:
      verified: {br_count}
      inferred: 0
      unresolved: 0
    sources: ["{module}"]
    sme_questions: []
    ---

    # {module} — widget release

    ## Business rules

    {br_lines}
    {extra_body}
    """)


def _module_doc(module="WGT0100", br_count=3, extra_body=""):
    br_lines = "\n".join(
        f"- rule {n} ({module}:BR-{n:03d}) [[{module}:{n + 10}]]" for n in range(1, br_count + 1)
    )
    return _MODULE_DOC_TEMPLATE.format(
        module=module, br_count=br_count, br_lines=br_lines, extra_body=extra_body,
    )


def test_rule_count_drift_detected_when_fact_store_has_more_rules(tmp_path):
    conn = _conn()
    mid = _insert_member(conn, "WGT0100")
    for line in (10, 20, 30, 40):  # 4 rules now, doc only cites 3
        _insert_rule(conn, mid, line)
    conn.commit()

    doc_path = tmp_path / "WGT0100.md"
    doc_path.write_text(_module_doc(br_count=3), encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert not result["ok"]
    assert not result["skipped"]
    assert any("BR-003" in p and "4 rule candidate" in p for p in result["problems"])


def test_rule_count_no_drift_when_counts_match(tmp_path):
    conn = _conn()
    mid = _insert_member(conn, "WGT0100")
    for line in (10, 20, 30):
        _insert_rule(conn, mid, line)
    conn.commit()

    doc_path = tmp_path / "WGT0100.md"
    doc_path.write_text(_module_doc(br_count=3), encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert result["ok"]
    assert result["problems"] == []


def test_rule_count_drift_skipped_when_no_br_ids_cited(tmp_path):
    conn = _conn()
    mid = _insert_member(conn, "WGT0100")
    _insert_rule(conn, mid, 10)
    conn.commit()

    doc_path = tmp_path / "WGT0100.md"
    doc_path.write_text(_module_doc(br_count=0), encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert result["ok"]


def test_rule_count_drift_flags_member_no_longer_in_index(tmp_path):
    conn = _conn()
    conn.commit()  # no members at all

    doc_path = tmp_path / "WGT0100.md"
    doc_path.write_text(_module_doc(br_count=2), encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert not result["ok"]
    assert any("no longer resolves" in p for p in result["problems"])


def test_rule_count_drift_flags_ambiguous_member(tmp_path):
    conn = _conn()
    _insert_member(conn, "WGT0100", library="LIBA")
    _insert_member(conn, "WGT0100", library="LIBB")
    conn.commit()

    doc_path = tmp_path / "WGT0100.md"
    doc_path.write_text(_module_doc(br_count=2), encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert not result["ok"]
    assert any("ambiguous" in p for p in result["problems"])


def test_sources_drift_flags_missing_name(tmp_path):
    conn = _conn()
    _insert_member(conn, "WGT0100")
    conn.commit()

    text = textwrap.dedent("""\
        ---
        title: "WGT — functional overview"
        doc_type: system-overview
        system: "WGT"
        generated_by: legacy-functional-docs 0.1.0
        generated_at: "2026-09-10"
        review_status: draft
        reviewers: []
        confidence_summary:
          verified: 0
          inferred: 0
          unresolved: 0
        sources: ["WGT0100", "WGT9999"]
        sme_questions: []
        ---

        # WGT — functional overview

        Nothing checkable in the body.
        """)
    doc_path = tmp_path / "system-overview.md"
    doc_path.write_text(text, encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert not result["ok"]
    assert any("WGT9999" in p and "no longer resolves" in p for p in result["problems"])


def test_sources_drift_accepts_an_entity_name(tmp_path):
    conn = _conn()
    conn.execute("INSERT INTO entity (name, kind) VALUES ('WIDGET-MASTER', 'ddm')")
    conn.commit()

    text = textwrap.dedent("""\
        ---
        title: "WGT — functional overview"
        doc_type: system-overview
        system: "WGT"
        generated_by: legacy-functional-docs 0.1.0
        generated_at: "2026-09-10"
        review_status: draft
        reviewers: []
        confidence_summary: {verified: 0, inferred: 0, unresolved: 0}
        sources: ["WIDGET-MASTER"]
        sme_questions: []
        ---

        # WGT — functional overview

        Nothing checkable in the body.
        """)
    doc_path = tmp_path / "system-overview.md"
    doc_path.write_text(text, encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert result["ok"]


def _gap_register_doc(total, high, medium, low):
    return textwrap.dedent(f"""\
        ---
        title: "WGT — gap register"
        doc_type: gap-register
        system: "WGT"
        generated_by: legacy-functional-docs 0.1.0
        generated_at: "2026-09-10"
        review_status: draft
        reviewers: []
        confidence_summary: {{verified: 0, inferred: 0, unresolved: {total}}}
        sources: []
        sme_questions: []
        ---

        # WGT — gap register

        {total} gaps total from the automated pass (`mfdoc coverage`: {high} high,
        {medium} medium, {low} low).
        """)


def _insert_gap(conn, severity, member_id=None):
    conn.execute(
        "INSERT INTO gap (member_id, gap_kind, severity, detail) VALUES (?, 'missing_source', ?, 'x')",
        (member_id, severity),
    )


def test_gap_register_drift_detected(tmp_path):
    conn = _conn()
    _insert_gap(conn, "high")
    _insert_gap(conn, "high")
    _insert_gap(conn, "medium")
    conn.commit()  # 3 total: 2 high, 1 medium, 0 low

    doc_path = tmp_path / "gap-register.md"
    doc_path.write_text(_gap_register_doc(total=5, high=3, medium=1, low=1), encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert not result["ok"]
    problems = "\n".join(result["problems"])
    assert "5 gap(s) total" in problems and "current fact store has 3" in problems
    assert "3 high-severity" in problems and "current fact store has 2" in problems
    assert "1 low-severity" in problems and "current fact store has 0" in problems


def test_gap_register_no_drift_when_counts_match(tmp_path):
    conn = _conn()
    _insert_gap(conn, "high")
    _insert_gap(conn, "medium")
    _insert_gap(conn, "low")
    conn.commit()

    doc_path = tmp_path / "gap-register.md"
    doc_path.write_text(_gap_register_doc(total=3, high=1, medium=1, low=1), encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert result["ok"]


def _system_overview_doc(pct):
    return textwrap.dedent(f"""\
        ---
        title: "WGT — functional overview"
        doc_type: system-overview
        system: "WGT"
        generated_by: legacy-functional-docs 0.1.0
        generated_at: "2026-09-10"
        review_status: draft
        reviewers: []
        confidence_summary: {{verified: 0, inferred: 0, unresolved: 0}}
        sources: []
        sme_questions: []
        ---

        # WGT — functional overview

        | Input | Supplied | Coverage impact |
        |---|---|---|
        | Natural source | Yes | {pct}% line recognition |
        """)


def test_coverage_rate_drift_detected(tmp_path):
    conn = _conn()
    mid = _insert_member(conn, "WGT0100")
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES (?, 1, 'X'), (?, 2, 'Y')",
        (mid, mid),
    )
    conn.commit()  # 0 unparsed of 2 lines -> 100.0% line recognition today

    doc_path = tmp_path / "system-overview.md"
    doc_path.write_text(_system_overview_doc(pct="80.0"), encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert not result["ok"]
    assert any("80.0% line recognition" in p and "100.0%" in p for p in result["problems"])


def test_coverage_rate_no_drift_when_matching(tmp_path):
    conn = _conn()
    mid = _insert_member(conn, "WGT0100")
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES (?, 1, 'X'), (?, 2, 'Y')",
        (mid, mid),
    )
    conn.commit()

    doc_path = tmp_path / "system-overview.md"
    doc_path.write_text(_system_overview_doc(pct="100.0"), encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert result["ok"]


def test_register_doc_type_is_skipped(tmp_path):
    conn = _conn()
    conn.commit()
    text = textwrap.dedent("""\
        ---
        title: "Gap summary"
        doc_type: register
        ---

        # Gap summary
        """)
    doc_path = tmp_path / "gap-summary.md"
    doc_path.write_text(text, encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert result["skipped"]
    assert result["ok"]
    assert result["problems"] == []


def test_missing_front_matter_is_skipped_not_a_crash(tmp_path):
    conn = _conn()
    conn.commit()
    doc_path = tmp_path / "plain.md"
    doc_path.write_text("# just a heading, no front matter\n", encoding="utf-8")

    result = docdrift.check_document(conn, doc_path)
    assert result["skipped"]
    assert result["ok"]


def test_check_tree_walks_a_directory(tmp_path):
    conn = _conn()
    mid = _insert_member(conn, "WGT0100")
    for line in (10, 20, 30):
        _insert_rule(conn, mid, line)
    conn.commit()

    (tmp_path / "clean.md").write_text(_module_doc(br_count=3), encoding="utf-8")
    (tmp_path / "stale.md").write_text(_module_doc(br_count=2), encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("not markdown", encoding="utf-8")

    res = docdrift.check_tree(conn, tmp_path)
    assert res["documents"] == 2
    assert res["documents_checked"] == 2
    assert res["documents_drifted"] == 1
    assert res["total_mismatches"] == 1


def test_cli_doc_drift_exit_code(tmp_path):
    from types import SimpleNamespace

    import yaml

    from mfdoc import cli
    from mfdoc.db import connect

    index_db = tmp_path / "index.db"
    conn = connect(index_db)
    mid = _insert_member(conn, "WGT0100")
    for line in (10, 20, 30):
        _insert_rule(conn, mid, line)
    conn.commit()
    conn.close()

    config_path = tmp_path / "project.yml"
    config_path.write_text(
        yaml.safe_dump({"index_db": str(index_db), "sources": []}), encoding="utf-8"
    )

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "WGT0100.md").write_text(_module_doc(br_count=3), encoding="utf-8")
    assert cli.cmd_doc_drift(SimpleNamespace(config=str(config_path), docs=str(docs_dir))) == 0

    (docs_dir / "WGT0100.md").write_text(_module_doc(br_count=2), encoding="utf-8")
    assert cli.cmd_doc_drift(SimpleNamespace(config=str(config_path), docs=str(docs_dir))) == 1
