"""Guards for the test-plan derive stage (mfdoc test-plan)."""

from __future__ import annotations

import json
import sqlite3

from mfdoc import testplan
from mfdoc.db import SCHEMA, insert
from mfdoc.dialects import natural
from mfdoc.validate import CITATION


def _member_with_if_else(lines: list[str]):
    """A minimal in-memory index with one Natural member scanned for real
    (not hand-built rule_candidate rows) -- exercises natural.py's actual
    depth bookkeeping for IF/ELSE, not an assumption about it."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    mid = insert(conn, "member", name="TESTIF", dialect="natural", object_type="program")
    natural.extract(conn, mid, [(i + 1, None, line) for i, line in enumerate(lines)], "TESTIF")
    conn.commit()
    return conn, mid


def test_derives_a_scenario_per_branch_with_matching_br_id(indexed_db):
    """MMP0100:BR-004 is `IF ORDER-VIEW.ORDER-STATUS NE 'CONF'` -- the same
    rule_candidate row brief.rules_register numbers as BR-004 (verified via
    brief.rules_register's own output). A test scenario for it must carry
    the identical id, not a second numbering scheme that only counts branch
    constructs."""
    conn = indexed_db
    res = testplan.run_all(conn, member_name="MMP0100")
    assert res["test_cases"] > 0

    row = conn.execute(
        "SELECT * FROM test_case WHERE scenario_name='MMP0100:BR-004'"
    ).fetchone()
    assert row is not None
    when = json.loads(row["when_json"])
    assert when["construct"] == "IF"
    assert "CONF" in when["condition"]
    assert row["status"] == "characterization"
    assert row["kind"] == "unit"

    from mfdoc import brief as brief_mod
    rules_out = brief_mod.rules_register(conn)
    assert "**MMP0100:BR-004**" in rules_out


def test_given_json_carries_parameters_and_mocks_not_invented(indexed_db):
    """MMP0100's parameters and its data-access/call facts are known --
    given_json must reflect exactly those, not a guess at what a unit test
    would need."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    row = conn.execute(
        "SELECT * FROM test_case WHERE member_id=(SELECT id FROM member WHERE name='MMP0100') LIMIT 1"
    ).fetchone()
    given = json.loads(row["given_json"])
    param_names = {p["name"] for p in given["parameters"]}
    assert {"#ORDER-NO", "#PLANT", "#RETURN-CODE"} <= param_names
    assert "MILL-ORDER" in given["mocks"]["entities"] or "STOCK-BALANCE" in given["mocks"]["entities"]
    assert "MMN0250" in given["mocks"]["callees"] or "MMN0900" in given["mocks"]["callees"]


def test_then_json_carries_cited_source_excerpt_not_a_guessed_value(indexed_db):
    """then_json must be traceable source text, never an invented assertion."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    row = conn.execute(
        "SELECT * FROM test_case WHERE scenario_name='MMP0100:BR-004'"
    ).fetchone()
    then = json.loads(row["then_json"])
    assert then["source_excerpt"]
    assert any("RETURN-CODE" in line for line in then["source_excerpt"])
    assert CITATION.match(then["citation"])


def test_when_branch_body_stops_at_next_sibling_when(indexed_db):
    """MMP0100's `DECIDE FOR FIRST CONDITION` has three WHEN branches whose
    own consequence statements natural.py records at the *same* depth as
    the WHEN itself (not deeper) -- body reconstruction must stop at the
    next WHEN, not swallow every remaining WHEN's statements into the first
    branch's excerpt."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    mid = conn.execute("SELECT id FROM member WHERE name='MMP0100'").fetchone()["id"]
    rows = conn.execute(
        "SELECT * FROM test_case WHERE member_id=? AND when_json LIKE '%AVAIL-TOTAL >= ORDER-VIEW.ORDER-WEIGHT\"%'",
        (mid,),
    ).fetchall()
    first_when = next(
        r for r in rows
        if json.loads(r["when_json"])["condition"] == "#AVAIL-TOTAL >= ORDER-VIEW.ORDER-WEIGHT"
    )
    then = json.loads(first_when["then_json"])
    assert any("RLSD" in line for line in then["source_excerpt"])
    assert not any("PART" in line for line in then["source_excerpt"])


def test_if_else_gets_two_scenarios_not_one_bleeding_into_the_other():
    """A plain IF/ELSE/END-IF must derive one scenario per branch, each
    carrying only its own consequence -- not the true-branch's excerpt
    absorbing the ELSE keyword and the else-clause's own consequence (see
    natural._match_rules: ELSE doesn't bump depth again, unlike a WHEN/CASE
    sibling, so body reconstruction needs to know to stop there anyway)."""
    conn, mid = _member_with_if_else([
        "IF #STATUS EQ 'A'",
        "  MOVE 1 TO #RESULT",
        "ELSE",
        "  MOVE 2 TO #RESULT",
        "END-IF",
    ])
    rows = testplan.build_member_test_cases(conn, mid, "TESTIF")
    by_construct = {json.loads(r["when_json"])["construct"]: r for r in rows}
    assert set(by_construct) == {"IF", "ELSE"}

    if_then = json.loads(by_construct["IF"]["then_json"])
    assert any("MOVE 1" in line for line in if_then["source_excerpt"])
    assert not any("MOVE 2" in line for line in if_then["source_excerpt"])
    assert not any("ELSE" in line for line in if_then["source_excerpt"])

    else_then = json.loads(by_construct["ELSE"]["then_json"])
    assert any("MOVE 2" in line for line in else_then["source_excerpt"])
    assert not any("MOVE 1" in line for line in else_then["source_excerpt"])


def test_scoped_run_on_an_ambiguous_name_does_not_delete_existing_rows():
    """Two batchable members named FOO in different libraries: a scoped
    `run_all(member_name="FOO")` must refuse to rebuild (it can't know which
    library's facts apply) *and* leave any of FOO's already-derived
    test_case rows untouched -- not silently wipe them while reporting
    success, the way an unconditional DELETE keyed only on the bare name
    would."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    m1 = insert(conn, "member", name="FOO", dialect="natural", object_type="program", library="LIBA")
    insert(conn, "member", name="FOO", dialect="natural", object_type="program", library="LIBB")
    insert(
        conn, "test_case", member_id=m1, kind="unit", scenario_name="FOO:BR-001",
        given_json="{}", when_json="{}", then_json="{}",
        status="characterization", citation="FOO:1", confidence="verified",
    )
    conn.commit()

    res = testplan.run_all(conn, member_name="FOO")
    assert res["members"] == 0
    assert res["ambiguous"] == ["FOO"]

    remaining = conn.execute("SELECT COUNT(*) FROM test_case").fetchone()[0]
    assert remaining == 1, "ambiguous scoped run must not delete the other library's test_case rows"


def test_rerun_is_idempotent(indexed_db):
    """Re-running test-plan against unchanged source must not accumulate
    duplicate rows -- it's a full rebuild, like graph.run_all's derived gaps."""
    conn = indexed_db
    first = testplan.run_all(conn, member_name="MMP0100")
    second = testplan.run_all(conn, member_name="MMP0100")
    assert first == second


def test_scoping_to_one_member_leaves_others_untouched(indexed_db):
    conn = indexed_db
    testplan.run_all(conn)
    before = conn.execute(
        "SELECT COUNT(*) FROM test_case WHERE member_id=(SELECT id FROM member WHERE name='MMP0200')"
    ).fetchone()[0]
    testplan.run_all(conn, member_name="MMP0100")
    after = conn.execute(
        "SELECT COUNT(*) FROM test_case WHERE member_id=(SELECT id FROM member WHERE name='MMP0200')"
    ).fetchone()[0]
    assert before == after
    assert before > 0


def test_fetch_test_case_rows_orders_by_source_line_not_insertion_order():
    """testbatch.py's routine-aware chunking assumes rows come back in
    source-line order (rule_candidate rows sharing a routine must be
    contiguous) -- inserting test_case rows out of that order (BR-002's
    rule at a later line than BR-001's, but stored first) must not change
    the order fetch_test_case_rows returns them in."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'x')")

    rc_late = insert(conn, "rule_candidate", member_id=1, line_no=10, construct="IF", raw="x")
    rc_early = insert(conn, "rule_candidate", member_id=1, line_no=5, construct="IF", raw="x")

    def _tc(rc_id, name):
        insert(
            conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc_id,
            scenario_name=name,
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
            status="characterization", citation="FAKEMOD:1", confidence="verified",
        )

    # Inserted in reverse of source-line order on purpose.
    _tc(rc_late, "FAKEMOD:BR-LATE")
    _tc(rc_early, "FAKEMOD:BR-EARLY")

    system, rows, ambiguous = testplan.fetch_test_case_rows(conn, "FAKEMOD")
    assert not ambiguous
    assert [r["scenario_name"] for r in rows] == ["FAKEMOD:BR-EARLY", "FAKEMOD:BR-LATE"]


def test_member_rule_fingerprint_breaks_line_no_ties_with_id():
    """Copilot review follow-up on issue #195's fix: same-line
    rule_candidate rows are explicitly supported (natural.py can record
    more than one per source line), so `member_rule_fingerprint`'s query
    must order by `id` as an explicit secondary key, matching
    `build_member_test_cases`/`brief.fetch_rule_candidate_rows`, instead of
    leaving tied `line_no` rows to SQLite's unspecified tie order -- a
    later query-plan/index change could otherwise reorder them with no
    fact actually changing, silently marking every existing sidecar's
    fingerprint stale. Locks in the query text itself as a regression
    guard, since the ordering contract isn't otherwise reliably observable
    through SQLite's typical (but unspecified) tie behavior."""
    queries: list[str] = []
    real_execute = sqlite3.Connection.execute

    class _CapturingConn(sqlite3.Connection):
        def execute(self, sql, *args):
            queries.append(sql)
            return real_execute(self, sql, *args)

    conn = sqlite3.connect(":memory:", factory=_CapturingConn)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'x')")
    insert(conn, "rule_candidate", member_id=1, line_no=1, construct="IF", raw="x")
    insert(conn, "rule_candidate", member_id=1, line_no=1, construct="IF", raw="y")
    conn.commit()
    queries.clear()

    fp = testplan.member_rule_fingerprint(conn, "FAKEMOD")
    assert fp is not None
    assert any("ORDER BY line_no, id" in q for q in queries), (
        "member_rule_fingerprint must break line_no ties with an explicit id order"
    )


def test_member_test_case_aligned_with_rule_candidate():
    """Copilot review follow-up on issue #195's fix: aligned when every
    current rule_candidate branch row already has a test_case row (the
    normal, post-`mfdoc test-plan` state); misaligned the moment a new
    rule_candidate row appears with no test_case row yet (a `derive`
    rebuild `mfdoc test-plan` hasn't caught up to); unaffected by an
    unrelated *extra* test_case row (an overlay-sourced scenario, or one a
    since-removed rule_candidate row left behind) -- the concern here is
    specifically a *missing* id, not an exact match."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'x')")
    rc1 = insert(conn, "rule_candidate", member_id=1, line_no=1, construct="IF", raw="x")
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1, scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()
    assert testplan.member_test_case_aligned_with_rule_candidate(conn, "FAKEMOD") is True

    # An extra test_case row with no current rule_candidate counterpart --
    # must not be reported as misaligned.
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-999",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()
    assert testplan.member_test_case_aligned_with_rule_candidate(conn, "FAKEMOD") is True

    # A new rule_candidate row with no test_case row yet -- misaligned.
    insert(conn, "rule_candidate", member_id=1, line_no=2, construct="IF", raw="y")
    conn.commit()
    assert testplan.member_test_case_aligned_with_rule_candidate(conn, "FAKEMOD") is False

    assert testplan.member_test_case_aligned_with_rule_candidate(conn, "NOSUCHMEMBER") is False


def test_register_lists_scenarios_with_resolvable_citations(indexed_db):
    conn = indexed_db
    testplan.run_all(conn)
    out = testplan.test_plan_register(conn)
    assert "MMP0100:BR-004" in out
    cites = list(CITATION.finditer(out))
    assert cites
    for m in cites:
        member = m.group("member").upper()
        line = int(m.group("from"))
        row = conn.execute(
            "SELECT (SELECT MAX(line_no) FROM source_line WHERE member_id=member.id) AS maxline "
            "FROM member WHERE UPPER(name)=?", (member,)
        ).fetchone()
        assert row is not None, f"citation to unknown member {member}"
        assert 1 <= line <= row["maxline"], f"citation {m.group(0)} out of range"
