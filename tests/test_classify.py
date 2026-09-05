"""Unit tests for rule-theme classification (classify.py).

Tests for the three-layer fallback classification: (1) project-defined
keyword/regex taxonomy, (2) optional LLM pass (Task 3), (3) structural
fallback (member's library). This test file covers the deterministic
first layer (classify_rules_deterministic).
"""

from __future__ import annotations

from mfdoc import classify


def test_keyword_match_wins_over_structural_fallback(indexed_db):
    conn = indexed_db
    taxonomy = {"validation": ["invalid", "error"]}
    counts = classify.classify_rules_deterministic(conn, taxonomy)
    assert counts["keyword"] + counts["structural"] == conn.execute(
        "SELECT COUNT(*) FROM rule_candidate"
    ).fetchone()[0]
    row = conn.execute(
        "SELECT source FROM rule_theme rt JOIN rule_candidate rc ON rc.id = rt.rule_candidate_id "
        "WHERE rt.theme='validation' LIMIT 1"
    ).fetchone()
    assert row is None or row["source"] == "keyword"


def test_every_rule_candidate_ends_up_classified(indexed_db):
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    unclassified = conn.execute(
        "SELECT COUNT(*) FROM rule_candidate rc "
        "WHERE NOT EXISTS (SELECT 1 FROM rule_theme rt WHERE rt.rule_candidate_id = rc.id)"
    ).fetchone()[0]
    assert unclassified == 0


def test_rerun_upserts_not_duplicates(indexed_db):
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={"validation": ["invalid"]})
    before = conn.execute("SELECT COUNT(*) FROM rule_theme").fetchone()[0]
    classify.classify_rules_deterministic(conn, taxonomy={"validation": ["invalid"]})
    after = conn.execute("SELECT COUNT(*) FROM rule_theme").fetchone()[0]
    assert before == after
