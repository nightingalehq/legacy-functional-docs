"""Guards for the testability advisory (mfdoc test-advisory)."""

from __future__ import annotations

import sqlite3

from mfdoc import testadvisor
from mfdoc.db import SCHEMA, insert
from mfdoc.graph import transaction_scopes
from mfdoc.validate import CITATION


def _mid(conn, name):
    return conn.execute("SELECT id FROM member WHERE name=?", (name,)).fetchone()["id"]


def test_ambiguous_member_is_reported_not_silently_dropped():
    """A bare name that collides across libraries must be skipped (nothing
    to guess which library's facts apply) *and* surfaced in `ambiguous`,
    not just disappear from the results with no diagnostic."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    insert(conn, "member", name="FOO", dialect="natural", object_type="program", library="LIBA")
    insert(conn, "member", name="FOO", dialect="natural", object_type="program", library="LIBB")
    insert(conn, "member", name="BAR", dialect="natural", object_type="program", library="LIBA")
    conn.commit()

    run = testadvisor.run_all(conn)
    assert run["ambiguous"] == ["FOO"]
    assert [r["member"] for r in run["results"]] == ["BAR"]

    report = testadvisor.testability_report(conn)
    assert "FOO" in report
    assert "ambiguous" in report.lower()


def test_pure_member_has_no_mocks_or_seams(indexed_db):
    """MMC0100 has no data_access and no outbound calls -- it must classify
    as pure, with nothing to mock (never invented)."""
    conn = indexed_db
    scopes = transaction_scopes(conn)
    r = testadvisor.classify_member(conn, _mid(conn, "MMC0100"), "MMC0100", scopes)
    assert r["classification"] == testadvisor.PURE
    assert r["mocks"] == {"entities": [], "callees": []}
    assert r["seams"] == []
    assert r["gaps"] == []


def test_needs_mock_member_gets_named_seams_not_prose_about_source(indexed_db):
    """MMP9200 reads/writes MILL-ORDER and has no unresolved/dynamic calls --
    it needs mocks, and the seam suggestion must name the exact cited entity,
    not a paraphrase."""
    conn = indexed_db
    scopes = transaction_scopes(conn)
    r = testadvisor.classify_member(conn, _mid(conn, "MMP9200"), "MMP9200", scopes)
    assert r["classification"] == testadvisor.NEEDS_MOCK
    assert "MILL-ORDER" in r["mocks"]["entities"]
    assert r["seams"], "expected at least one seam suggestion"
    assert any("MILL-ORDER" in s for s in r["seams"])
    for s in r["seams"]:
        assert CITATION.search(s)


def test_include_edges_are_not_treated_as_mockable_callees(indexed_db):
    """MMP0100's `LOCAL USING MMLDA01` is an INCLUDE of a data area, not a
    callable dependency -- it must never show up as something to mock or as
    an unresolved-call gap; only its real CALLNATs/gaps should."""
    conn = indexed_db
    scopes = transaction_scopes(conn)
    r = testadvisor.classify_member(conn, _mid(conn, "MMP0100"), "MMP0100", scopes)
    assert "MMLDA01" not in r["mocks"]["callees"]
    assert not any("MMLDA01" in g for g in r["gaps"])


def test_unresolved_call_blocks_classification_as_a_gap(indexed_db):
    """MMP0100 calls MMN0250/MMN0900, whose source the fixture set
    deliberately omits -- that must surface as an untestable-gap, not be
    silently mocked as if the behaviour were known."""
    conn = indexed_db
    scopes = transaction_scopes(conn)
    r = testadvisor.classify_member(conn, _mid(conn, "MMP0100"), "MMP0100", scopes)
    assert r["classification"] == testadvisor.UNTESTABLE_GAP
    assert any("MMN0250" in g or "MMN0900" in g for g in r["gaps"])


def test_multi_entity_transaction_scope_forces_integration_only():
    """A member whose write scope spans more than one entity should be
    routed to an integration test, not forced into a unit test with two
    entities mocked as if they were independent. Uses a synthetic scopes
    list -- no bundled fixture currently exercises a genuinely multi-entity
    commit, and classify_member takes scopes as a plain argument precisely
    so this doesn't need one."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from mfdoc.db import SCHEMA
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute(
        "INSERT INTO data_access (member_id, line_no, verb, crud, entity_name, raw) "
        "VALUES (1, 10, 'UPDATE', 'U', 'ORDER', 'x'), (1, 12, 'STORE', 'C', 'AUDIT', 'y')"
    )
    scopes = [{"module": "FAKEMOD", "commit_line": 15, "entities": ["ORDER", "AUDIT"], "operations": []}]
    r = testadvisor.classify_member(conn, 1, "FAKEMOD", scopes)
    assert r["classification"] == testadvisor.INTEGRATION_ONLY
    assert r["integration_scope"]["entities"] == ["ORDER", "AUDIT"]
    assert r["seams"] == []  # integration-only members aren't given unit-test seam advice


def test_report_groups_by_classification_with_resolvable_citations(indexed_db):
    conn = indexed_db
    out = testadvisor.testability_report(conn)
    assert "## Blocked" in out
    assert "MMP0100" in out
    cites = list(CITATION.finditer(out))
    assert cites
    for m in cites:
        member = m.group("member").upper()
        row = conn.execute("SELECT 1 FROM member WHERE UPPER(name)=?", (member,)).fetchone()
        assert row is not None, f"citation to unknown member {member}"


def test_rerun_does_not_duplicate_sme_question_gaps(indexed_db):
    """transaction_scopes() adds an sme_question gap as a side effect;
    run_all() must purge that gap kind before recomputing, or repeated
    test-advisory runs would silently inflate the gap count every time."""
    conn = indexed_db
    testadvisor.run_all(conn)
    before = conn.execute("SELECT COUNT(*) FROM gap WHERE gap_kind='sme_question'").fetchone()[0]
    testadvisor.run_all(conn)
    after = conn.execute("SELECT COUNT(*) FROM gap WHERE gap_kind='sme_question'").fetchone()[0]
    assert before == after
