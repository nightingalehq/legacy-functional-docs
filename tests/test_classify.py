"""Unit tests for rule-theme classification (classify.py).

Tests for the three-layer fallback classification: (1) project-defined
keyword/regex taxonomy, (2) optional LLM pass (Task 3), (3) structural
fallback (member's library). This test file covers the deterministic
first layer (classify_rules_deterministic).
"""

from __future__ import annotations

import pytest

from mfdoc import classify


@pytest.fixture(autouse=True)
def _reset_rule_theme(indexed_db):
    """`indexed_db` is a session-scoped connection shared by every test in
    this module (and, via `mfdoc classify-rules`, test_cli_classify_rules.py
    too) -- rule_theme.source only ever moves forward (structural -> keyword
    or -> llm, never back, see classify_rules_deterministic/classify_rules_llm's
    own docstrings), so without a reset a later test's assertions about a
    specific transition depend on exactly what an earlier test left behind.
    Clearing the table before each test gives every test the same starting
    point: whatever this session's ingest+derive produced, with nothing
    classified yet."""
    indexed_db.execute("DELETE FROM rule_theme")
    indexed_db.commit()
    yield


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


def test_llm_fallback_reclassifies_structural_rows(indexed_db):
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})

    def fake_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="posting", input_tokens=0, output_tokens=0)

    before = conn.execute("SELECT COUNT(*) FROM rule_theme WHERE source='structural'").fetchone()[0]
    reclassified = classify.classify_rules_llm(conn, fake_caller)
    after_llm = conn.execute("SELECT COUNT(*) FROM rule_theme WHERE source='llm'").fetchone()[0]
    assert reclassified == before
    assert after_llm == before


def test_llm_fallback_never_touches_keyword_rows(indexed_db):
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={"validation": [".*"]})  # everything matches

    def fake_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="whatever", input_tokens=0, output_tokens=0)

    classify.classify_rules_llm(conn, fake_caller)
    still_keyword = conn.execute("SELECT COUNT(*) FROM rule_theme WHERE source='keyword'").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM rule_theme").fetchone()[0]
    assert still_keyword == total
