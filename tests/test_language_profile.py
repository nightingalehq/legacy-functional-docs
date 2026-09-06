"""Tests for graph.language_profile() and graph.unparsed_line_shapes() --
the deterministic data behind the language-guide document type (issue #64).

Uses an isolated in-memory connection (same pattern as
test_structural_call_graph.py's unresolved-call cases) rather than the
shared session-scoped indexed_db fixture, since these tests need full
control over which dialect each row belongs to.
"""

from __future__ import annotations

import sqlite3

from mfdoc import graph
from mfdoc.db import SCHEMA


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _add_member(conn, name: str, dialect: str) -> int:
    conn.execute("INSERT INTO member (name, dialect) VALUES (?, ?)", (name, dialect))
    return conn.execute("SELECT id FROM member WHERE name=?", (name,)).fetchone()["id"]


def _add_source_line(conn, member_id: int, line_no: int, text: str) -> None:
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES (?, ?, ?)",
        (member_id, line_no, text),
    )


def _fixture_conn():
    """Two invented members, one Natural, one Mantis, each exercising every
    column graph.language_profile() reads -- plus a third, unrelated
    dialect (sql_ddl) with no matching rows anywhere, to exercise the
    all-sections-empty case."""
    conn = _conn()

    nat = _add_member(conn, "NATPROG", "natural")
    for line_no, text in ((10, "IF STATUS EQ 'OPEN'"), (20, "IF STATUS EQ 'CLOSED'"),
                          (30, "WHILE MORE-RECS")):
        _add_source_line(conn, nat, line_no, text)
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, construct, raw) VALUES (?, ?, ?, ?)",
        (nat, 10, "IF", "IF STATUS EQ 'OPEN'"))
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, construct, raw) VALUES (?, ?, ?, ?)",
        (nat, 20, "IF", "IF STATUS EQ 'CLOSED'"))
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, construct, raw) VALUES (?, ?, ?, ?)",
        (nat, 30, "WHILE", "WHILE MORE-RECS"))
    _add_source_line(conn, nat, 40, "READ WIDGETFILE BY ISN")
    conn.execute(
        "INSERT INTO data_access (member_id, line_no, verb, crud, raw) VALUES (?, ?, ?, ?, ?)",
        (nat, 40, "READ", "R", "READ WIDGETFILE BY ISN"))
    _add_source_line(conn, nat, 50, "DEFINE DATA LOCAL 1 #NAME (A20)")
    conn.execute(
        "INSERT INTO variable (member_id, name, format, line_no) VALUES (?, ?, ?, ?)",
        (nat, "#NAME", "A20", 50))
    _add_source_line(conn, nat, 60, "CALLNAT 'SUBPROG1'")
    conn.execute(
        "INSERT INTO call_edge (caller_id, callee_name, call_kind, line_no) VALUES (?, ?, ?, ?)",
        (nat, "SUBPROG1", "CALLNAT", 60))
    _add_source_line(conn, nat, 70, "END TRANSACTION")
    conn.execute(
        "INSERT INTO transaction_marker (member_id, line_no, marker) VALUES (?, ?, ?)",
        (nat, 70, "END TRANSACTION"))
    _add_source_line(conn, nat, 80, "INPUT USING MAP 'WIDGETMAP'")
    conn.execute(
        "INSERT INTO interaction (member_id, line_no, kind, target) VALUES (?, ?, ?, ?)",
        (nat, 80, "INPUT", "WIDGETMAP"))

    mantis = _add_member(conn, "MANPROG", "mantis")
    _add_source_line(conn, mantis, 5, "IF X = 1 THEN")
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, construct, raw) VALUES (?, ?, ?, ?)",
        (mantis, 5, "IF", "IF X = 1 THEN"))

    # entity_link with a real via_member (in scope for mantis' profile)...
    e1 = conn.execute("INSERT INTO entity (name, kind) VALUES ('WIDGET', 'sql_table')")
    e1_id = conn.execute("SELECT id FROM entity WHERE name='WIDGET'").fetchone()["id"]
    e2 = conn.execute("INSERT INTO entity (name, kind) VALUES ('WIDGET-DETAIL', 'sql_table')")
    e2_id = conn.execute("SELECT id FROM entity WHERE name='WIDGET-DETAIL'").fetchone()["id"]
    _add_source_line(conn, mantis, 9, "JOIN WIDGET TO WIDGET-DETAIL")
    conn.execute(
        "INSERT INTO entity_link (from_entity, to_entity, link_kind, via_member, via_line) "
        "VALUES (?, ?, ?, ?, ?)",
        (e1_id, e2_id, "joined_in_code", mantis, 9),
    )
    # ...and one with no via_member at all (data-definition-only link,
    # must never appear in any dialect's profile).
    conn.execute(
        "INSERT INTO entity_link (from_entity, to_entity, link_kind, via_member, via_line) "
        "VALUES (?, ?, ?, NULL, NULL)",
        (e1_id, e2_id, "foreign_key"),
    )

    # gap rows for the "not yet recognized" appendix.
    conn.execute(
        "INSERT INTO gap (member_id, gap_kind, severity, detail, raw) VALUES (?, 'unparsed_line', 'low', 'x', ?)",
        (mantis, "FOO ARG1 ARG2"))
    conn.execute(
        "INSERT INTO gap (member_id, gap_kind, severity, detail, raw) VALUES (?, 'unparsed_line', 'low', 'x', ?)",
        (mantis, "FOO ARG3"))
    conn.execute(
        "INSERT INTO gap (member_id, gap_kind, severity, detail, raw) VALUES (?, 'unparsed_line', 'low', 'x', ?)",
        (mantis, "BAR ARG4"))

    conn.commit()
    return conn


def test_language_profile_groups_by_keyword_with_counts_and_citation():
    conn = _fixture_conn()
    natural = graph.language_profile(conn, "natural")

    assert [e["keyword"] for e in natural["control_flow"]] == ["IF", "WHILE"]
    assert natural["control_flow"][0]["count"] == 2
    assert natural["control_flow"][0]["example_member"] == "NATPROG"
    assert natural["control_flow"][0]["example_line"] == 10
    assert natural["control_flow"][0]["example_text"] == "IF STATUS EQ 'OPEN'"

    assert natural["data_access"] == [{
        "keyword": "READ", "count": 1, "example_member": "NATPROG",
        "example_line": 40, "example_text": "READ WIDGETFILE BY ISN",
    }]
    assert natural["structure"][0]["keyword"] == "A20"
    assert natural["calling_conventions"][0]["keyword"] == "CALLNAT"
    assert natural["transactions"][0]["keyword"] == "END TRANSACTION"
    assert natural["screen_interaction"][0]["keyword"] == "INPUT"
    # No entity_link rows are attached to any Natural member.
    assert natural["entity_relationships"] == []


def test_language_profile_is_scoped_per_dialect():
    conn = _fixture_conn()
    natural = graph.language_profile(conn, "natural")
    mantis = graph.language_profile(conn, "mantis")

    # Mantis's one control_flow row must not pick up Natural's facts, and
    # vice versa.
    assert [e["keyword"] for e in mantis["control_flow"]] == ["IF"]
    assert mantis["control_flow"][0]["count"] == 1
    assert mantis["control_flow"][0]["example_member"] == "MANPROG"
    assert natural["control_flow"][0]["count"] == 2  # unaffected by mantis's row

    # A dialect with no rows in a table renders an empty section, not a
    # missing key or an exception.
    empty = graph.language_profile(conn, "sql_ddl")
    for section in ("structure", "control_flow", "data_access", "entity_relationships",
                     "screen_interaction", "transactions", "calling_conventions"):
        assert empty[section] == []


def test_entity_relationships_excludes_data_definition_only_links():
    """A link with via_member IS NULL (declared only at the data-definition
    layer, e.g. Adabas coupling) must never appear in a per-dialect
    language profile -- see graph.language_profile's docstring."""
    conn = _fixture_conn()
    mantis = graph.language_profile(conn, "mantis")

    assert mantis["entity_relationships"] == [{
        "keyword": "joined_in_code", "count": 1, "example_member": "MANPROG",
        "example_line": 9, "example_text": "JOIN WIDGET TO WIDGET-DETAIL",
    }]


def test_unparsed_line_shapes_groups_by_leading_keyword_ranked_by_frequency():
    conn = _fixture_conn()
    shapes = graph.unparsed_line_shapes(conn, "mantis")

    assert shapes[0] == {"keyword": "FOO", "count": 2, "sample": "FOO ARG1 ARG2"}
    assert shapes[1] == {"keyword": "BAR", "count": 1, "sample": "BAR ARG4"}
    assert graph.unparsed_line_shapes(conn, "natural") == []
