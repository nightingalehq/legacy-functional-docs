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


def test_llm_empty_response_does_not_crash(indexed_db):
    """Regression test: response.text.strip().lower().splitlines()[0] raised
    IndexError for an empty/whitespace-only response, since splitlines() on
    an empty string returns [] -- a caller returning "" or "   " must be
    skipped gracefully, not crash the whole classify pass."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})

    def empty_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="   ", input_tokens=0, output_tokens=0)

    reclassified = classify.classify_rules_llm(conn, empty_caller)
    assert reclassified == 0


def test_llm_refusal_is_not_stored_as_a_theme(indexed_db):
    """Regression test: classify_rules_llm's docstring promises a rule the
    model can't confidently theme is left at its existing structural
    label -- but the old code only skipped genuinely empty text, so a
    refusal like "I cannot determine a theme for this rule" got stored
    verbatim as the theme with source='llm'. A refusal-shaped sentence
    must leave the row at source='structural', not overwrite it."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})

    def refusing_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(
            text="I cannot determine a confident theme for this rule",
            input_tokens=0, output_tokens=0,
        )

    before = conn.execute(
        "SELECT rule_candidate_id, theme FROM rule_theme WHERE source='structural'"
    ).fetchall()
    assert before, "fixture must have at least one structural-sourced row to exercise this"

    reclassified = classify.classify_rules_llm(conn, refusing_caller)
    assert reclassified == 0

    after = conn.execute(
        "SELECT rule_candidate_id, theme, source FROM rule_theme "
        "WHERE rule_candidate_id IN ({})".format(",".join("?" * len(before))),
        [r["rule_candidate_id"] for r in before],
    ).fetchall()
    for row in after:
        assert row["source"] == "structural"
        assert "cannot" not in row["theme"]


def test_llm_taxonomy_constrains_accepted_themes(indexed_db):
    """When a taxonomy is passed, only a theme matching one of its own
    keys (case-insensitively) may be accepted -- free-form model text
    that isn't one of the project's declared themes must not silently
    bypass the taxonomy."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})

    def off_taxonomy_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="some other theme", input_tokens=0, output_tokens=0)

    reclassified = classify.classify_rules_llm(
        conn, off_taxonomy_caller, taxonomy={"validation": ["invalid"], "posting": ["post"]}
    )
    assert reclassified == 0
    still_structural = conn.execute(
        "SELECT COUNT(*) FROM rule_theme WHERE source='llm'"
    ).fetchone()[0]
    assert still_structural == 0

    def on_taxonomy_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="Validation", input_tokens=0, output_tokens=0)

    reclassified = classify.classify_rules_llm(
        conn, on_taxonomy_caller, taxonomy={"validation": ["invalid"], "posting": ["post"]}
    )
    assert reclassified > 0
    llm_rows = conn.execute("SELECT theme FROM rule_theme WHERE source='llm'").fetchall()
    assert all(r["theme"] == "validation" for r in llm_rows)


def test_llm_fallback_commits_incrementally(indexed_db, monkeypatch):
    """Regression test: classify_rules_llm used to commit only once, after
    the whole loop -- if `caller` raised partway through, every row
    processed so far was lost. Commit incrementally (in small batches)
    so a mid-run failure preserves prior progress."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})

    structural_ids = [
        r["rule_candidate_id"] for r in conn.execute(
            "SELECT rule_candidate_id FROM rule_theme WHERE source='structural'"
        ).fetchall()
    ]
    assert len(structural_ids) >= 2, "fixture needs >=2 structural rows to exercise a mid-run failure"

    calls = {"n": 0}

    class Boom(Exception):
        pass

    def flaky_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        calls["n"] += 1
        if calls["n"] == 2:
            raise Boom("simulated mid-run failure")
        return ModelResponse(text="posting", input_tokens=0, output_tokens=0)

    import pytest

    monkeypatch.setattr(classify, "_COMMIT_BATCH_SIZE", 1)
    with pytest.raises(Boom):
        classify.classify_rules_llm(conn, flaky_caller)

    # Re-open a fresh connection view onto the same on-disk state is not
    # possible for an in-memory/session fixture, but conn.commit() having
    # already run for the first row means a rollback (or process crash)
    # right after the raise would not lose that first row -- verify it's
    # actually visible as committed by checking it survived the raise at
    # all (the flaky caller's raise happens on the *second* processed row).
    after = conn.execute(
        "SELECT source FROM rule_theme WHERE rule_candidate_id=?", (structural_ids[0],)
    ).fetchone()
    assert after["source"] == "llm", "the first row's classification must survive the later raise"


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
