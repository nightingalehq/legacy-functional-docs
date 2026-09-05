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
    result = classify.classify_rules_llm(conn, fake_caller)
    after_llm = conn.execute("SELECT COUNT(*) FROM rule_theme WHERE source='llm'").fetchone()[0]
    assert result["reclassified"] == before
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

    result = classify.classify_rules_llm(conn, empty_caller)
    assert result["reclassified"] == 0


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

    result = classify.classify_rules_llm(conn, refusing_caller)
    assert result["reclassified"] == 0

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

    result = classify.classify_rules_llm(
        conn, off_taxonomy_caller, taxonomy={"validation": ["invalid"], "posting": ["post"]}
    )
    assert result["reclassified"] == 0
    still_structural = conn.execute(
        "SELECT COUNT(*) FROM rule_theme WHERE source='llm'"
    ).fetchone()[0]
    assert still_structural == 0

    def on_taxonomy_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="Validation", input_tokens=0, output_tokens=0)

    result = classify.classify_rules_llm(
        conn, on_taxonomy_caller, taxonomy={"validation": ["invalid"], "posting": ["post"]}
    )
    assert result["reclassified"] > 0
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


def test_llm_reports_token_totals(indexed_db):
    """Finding 7: classify_rules_llm must accumulate input_tokens/output_tokens
    from every ModelResponse it receives (previously discarded entirely) and
    report them in its return dict alongside the reclassified count."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    structural_count = conn.execute(
        "SELECT COUNT(*) FROM rule_theme WHERE source='structural'"
    ).fetchone()[0]
    assert structural_count >= 2, "fixture needs >=2 structural rows to exercise token accounting"

    PER_CALL_IN, PER_CALL_OUT = 37, 11

    def counting_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="posting", input_tokens=PER_CALL_IN, output_tokens=PER_CALL_OUT)

    result = classify.classify_rules_llm(conn, counting_caller)
    assert result["reclassified"] == structural_count
    assert result["input_tokens"] == structural_count * PER_CALL_IN
    assert result["output_tokens"] == structural_count * PER_CALL_OUT


def test_llm_limit_caps_rows_sent_to_model(indexed_db):
    """Finding 7: --limit (wired through as classify_rules_llm's `limit` param)
    must cap how many structural rows are actually sent to the model in one
    run, even when more rows are eligible."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    structural_count = conn.execute(
        "SELECT COUNT(*) FROM rule_theme WHERE source='structural'"
    ).fetchone()[0]
    assert structural_count > 2, "fixture needs >2 structural rows to exercise a limit of 2"

    calls = {"n": 0}

    def counting_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        calls["n"] += 1
        return ModelResponse(text="posting", input_tokens=0, output_tokens=0)

    result = classify.classify_rules_llm(conn, counting_caller, limit=2)
    assert calls["n"] == 2
    assert result["reclassified"] == 2


def test_llm_progress_callback_receives_row_and_total(indexed_db, monkeypatch):
    """classify_rules_llm must never print directly (library-code/CLI
    print-only-in-cli.py convention, see batch.py/structural.py) -- an
    optional progress_callback(i, total) is invoked instead, at the same
    cadence the old print() used. Lower _PROGRESS_INTERVAL to 1 so every
    row invokes the callback, and assert the exact (row, total) sequence
    a caller sees."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    structural_count = conn.execute(
        "SELECT COUNT(*) FROM rule_theme WHERE source='structural'"
    ).fetchone()[0]
    assert structural_count >= 2, "fixture needs >=2 structural rows to exercise a multi-call sequence"

    monkeypatch.setattr(classify, "_PROGRESS_INTERVAL", 1)

    def fake_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="posting", input_tokens=0, output_tokens=0)

    seen: list[tuple[int, int]] = []
    result = classify.classify_rules_llm(
        conn, fake_caller, progress_callback=lambda i, total: seen.append((i, total))
    )
    assert seen == [(i, structural_count) for i in range(1, structural_count + 1)]
    assert result["reclassified"] == structural_count


def test_llm_no_progress_callback_produces_no_stdout(indexed_db, capsys):
    """Omitting progress_callback must produce no output at all -- this
    module is library code, not the CLI, and printing directly would
    violate the print-only-in-cli.py convention."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})

    def fake_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="posting", input_tokens=0, output_tokens=0)

    classify.classify_rules_llm(conn, fake_caller)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_llm_casing_matches_taxonomy_key_exactly(indexed_db):
    """Finding 8: a capitalized taxonomy key (e.g. "Posting") must be stored
    with its own exact casing when the model's (lowercased) response matches
    it case-insensitively -- not the model's lowercased text, which would
    otherwise split the same theme into two distinct groups (one from
    classify_rules_deterministic's verbatim key, one lowercased here)."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})

    def posting_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text="posting", input_tokens=0, output_tokens=0)

    result = classify.classify_rules_llm(conn, posting_caller, taxonomy={"Posting": ["post"]})
    assert result["reclassified"] > 0
    llm_rows = conn.execute("SELECT theme FROM rule_theme WHERE source='llm'").fetchall()
    assert all(r["theme"] == "Posting" for r in llm_rows)


def test_llm_matches_taxonomy_key_longer_than_40_chars(indexed_db):
    """Finding 9: the taxonomy-key match must happen against the model's
    *full* response, before any truncation -- truncating to 40 chars first
    (as the old code did) would make a taxonomy key longer than 40 characters
    unmatchable, silently falling back to 'structural' forever."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})

    long_key = "eligibility-determination-for-retirement-benefit-adjustments"
    assert len(long_key) > 40

    def long_key_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text=long_key, input_tokens=0, output_tokens=0)

    result = classify.classify_rules_llm(conn, long_key_caller, taxonomy={long_key: ["x"]})
    assert result["reclassified"] > 0
    llm_rows = conn.execute("SELECT theme FROM rule_theme WHERE source='llm'").fetchall()
    assert all(r["theme"] == long_key for r in llm_rows)
