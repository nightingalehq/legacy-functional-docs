"""Unit tests for rule-theme classification (classify.py).

Tests for the three-layer fallback classification: (1) project-defined
keyword/regex taxonomy, (2) optional LLM pass (Task 3), (3) structural
fallback (member's library). This test file covers the deterministic
first layer (classify_rules_deterministic).
"""

from __future__ import annotations

import re

import pytest

from mfdoc import classify
from mfdoc.batch import ModelResponse


def _echo_theme_caller(theme: str):
    """A fake caller for the batched protocol: replies with `<id>: theme`
    for every row id it finds in the prompt (`_build_batch_prompt`
    writes each row as a line starting with `<id>:`), regardless of how
    many rows are in the batch or what their real ids are. Stands in for
    a model that reliably answers every row with the same theme."""

    def caller(prompt: str):
        ids = re.findall(r"^(\d+):", prompt, re.MULTILINE)
        text = "\n".join(f"{rc_id}: {theme}" for rc_id in ids)
        return ModelResponse(text=text, input_tokens=0, output_tokens=0)

    return caller


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

    before = conn.execute("SELECT COUNT(*) FROM rule_theme WHERE source='structural'").fetchone()[0]
    result = classify.classify_rules_llm(conn, _echo_theme_caller("posting"))
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


def test_llm_refusal_check_runs_on_untruncated_response(indexed_db):
    """Regression test: the refusal/non-answer check must run on the FULL
    model response, not on the string already truncated to 40 chars for
    storage. A refusal whose tell-tale marker word falls past character 40
    would otherwise slip through the (now-truncated) check and get stored
    as if it were a real theme."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})

    long_prefix = "posting adjustment reconciliation workflow "
    assert len(long_prefix) > 40, "prefix must itself exceed the 40-char truncation point"
    refusal_text = long_prefix + "i cannot classify this rule with confidence"

    def refusing_caller(prompt: str):
        from mfdoc.batch import ModelResponse
        return ModelResponse(text=refusal_text, input_tokens=0, output_tokens=0)

    before = conn.execute(
        "SELECT rule_candidate_id FROM rule_theme WHERE source='structural'"
    ).fetchall()
    assert before, "fixture must have at least one structural-sourced row to exercise this"

    result = classify.classify_rules_llm(conn, refusing_caller)
    assert result["reclassified"] == 0

    after = conn.execute(
        "SELECT source, theme FROM rule_theme WHERE rule_candidate_id IN ({})".format(
            ",".join("?" * len(before))
        ),
        [r["rule_candidate_id"] for r in before],
    ).fetchall()
    for row in after:
        assert row["source"] == "structural"


def test_llm_taxonomy_constrains_accepted_themes(indexed_db):
    """When a taxonomy is passed, only a theme matching one of its own
    keys (case-insensitively) may be accepted -- free-form model text
    that isn't one of the project's declared themes must not silently
    bypass the taxonomy."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})

    result = classify.classify_rules_llm(
        conn, _echo_theme_caller("some other theme"), taxonomy={"validation": ["invalid"], "posting": ["post"]}
    )
    assert result["reclassified"] == 0
    still_structural = conn.execute(
        "SELECT COUNT(*) FROM rule_theme WHERE source='llm'"
    ).fetchone()[0]
    assert still_structural == 0

    result = classify.classify_rules_llm(
        conn, _echo_theme_caller("Validation"), taxonomy={"validation": ["invalid"], "posting": ["post"]}
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
        calls["n"] += 1
        if calls["n"] == 2:
            raise Boom("simulated mid-run failure")
        ids = re.findall(r"^(\d+):", prompt, re.MULTILINE)
        return ModelResponse(text="\n".join(f"{rc_id}: posting" for rc_id in ids), input_tokens=0, output_tokens=0)

    import pytest

    monkeypatch.setattr(classify, "_COMMIT_BATCH_SIZE", 1)
    # batch_size=1 so each row is its own model call -- this test is about
    # commit granularity surviving a mid-run raise, not about batching
    # itself, so it needs the flaky_caller's 2nd *call* to land on the
    # 2nd *row* the same way it did before batching existed.
    with pytest.raises(Boom):
        classify.classify_rules_llm(conn, flaky_caller, batch_size=1)

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


def test_llm_limit_is_pushed_into_sql_not_sliced_in_python(indexed_db):
    """--limit previously fetched every eligible row and sliced in Python
    -- fine for correctness but wasteful for a large project's full
    structural-sourced set. The query itself must now carry a SQL LIMIT
    so the database only returns the requested number of rows."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    eligible = conn.execute(
        "SELECT COUNT(*) FROM rule_theme WHERE source='structural'"
    ).fetchone()[0]
    assert eligible >= 2, "fixture needs >=2 eligible rows for this test to be meaningful"

    executed_sql = []
    conn.set_trace_callback(executed_sql.append)
    try:
        def fake_caller(prompt: str):
            from mfdoc.batch import ModelResponse
            return ModelResponse(text="posting", input_tokens=0, output_tokens=0)

        classify.classify_rules_llm(conn, fake_caller, limit=1)
    finally:
        conn.set_trace_callback(None)

    select_statements = [sql for sql in executed_sql if "FROM rule_candidate rc" in sql]
    assert select_statements, "expected the eligible-rows query to run"
    assert "LIMIT" in select_statements[0], (
        "limit must be pushed into the SQL query, not applied by slicing "
        "the fetched rows in Python"
    )


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
        ids = re.findall(r"^(\d+):", prompt, re.MULTILINE)
        text = "\n".join(f"{rc_id}: posting" for rc_id in ids)
        return ModelResponse(text=text, input_tokens=PER_CALL_IN, output_tokens=PER_CALL_OUT)

    # batch_size=1 -- one model call per row, so per-call token totals map
    # 1:1 onto structural_count the same way they did before batching.
    result = classify.classify_rules_llm(conn, counting_caller, batch_size=1)
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
        calls["n"] += 1
        ids = re.findall(r"^(\d+):", prompt, re.MULTILINE)
        return ModelResponse(text="\n".join(f"{rc_id}: posting" for rc_id in ids), input_tokens=0, output_tokens=0)

    # batch_size=1 so the call count itself still reflects exactly how
    # many rows were sent, matching this test's original one-call-per-row
    # assumption even though batching now groups calls by default.
    result = classify.classify_rules_llm(conn, counting_caller, limit=2, batch_size=1)
    assert calls["n"] == 2
    assert result["reclassified"] == 2


def test_llm_limit_selection_is_deterministic_across_runs():
    """Follow-up finding 4: the query feeding classify_rules_llm's loop
    had no ORDER BY, so `--limit N` capped an implementation-defined
    subset of eligible rows -- not necessarily the same subset from one
    run to the next. Rows are inserted here in an order scrambled
    relative to (member_id, line_no) specifically so an unordered SELECT
    would be likely to return them differently across runs; the query's
    `ORDER BY rc.member_id, rc.line_no` must make `limit=2` always pick
    the same two lowest-(member_id, line_no) rows, run after run."""
    import sqlite3
    from mfdoc.db import SCHEMA

    def build_conn():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO member (name, dialect) VALUES ('LIMTEST', 'natural')")
        member_id = conn.execute("SELECT id FROM member WHERE name='LIMTEST'").fetchone()["id"]
        # Inserted out of (member_id, line_no) order: line_no 30, then 10,
        # then 20 -- rowid/insertion order disagrees with line_no order.
        for line_no in (30, 10, 20):
            conn.execute(
                "INSERT INTO rule_candidate (member_id, line_no, construct, condition, raw) "
                "VALUES (?, ?, 'IF', 'cond', 'raw')",
                (member_id, line_no),
            )
        for rc_id, in conn.execute("SELECT id FROM rule_candidate").fetchall():
            conn.execute(
                "INSERT INTO rule_theme (rule_candidate_id, theme, source) VALUES (?, 'unknown', 'structural')",
                (rc_id,),
            )
        conn.commit()
        return conn

    def run_once():
        conn = build_conn()

        classify.classify_rules_llm(conn, _echo_theme_caller("posting"), limit=2)
        reclassified_lines = {
            r["line_no"]
            for r in conn.execute(
                "SELECT rc.line_no FROM rule_candidate rc "
                "JOIN rule_theme rt ON rt.rule_candidate_id = rc.id "
                "WHERE rt.source = 'llm'"
            ).fetchall()
        }
        return reclassified_lines

    first_run = run_once()
    second_run = run_once()
    # The two lowest line_no values (10, 20) must be the ones selected --
    # not line_no 30, which would win under plain insertion/rowid order.
    assert first_run == {10, 20}
    assert second_run == {10, 20}


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

    seen: list[tuple[int, int]] = []
    result = classify.classify_rules_llm(
        conn, _echo_theme_caller("posting"), progress_callback=lambda i, total: seen.append((i, total))
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

    result = classify.classify_rules_llm(conn, _echo_theme_caller("posting"), taxonomy={"Posting": ["post"]})
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

    result = classify.classify_rules_llm(conn, _echo_theme_caller(long_key), taxonomy={long_key: ["x"]})
    assert result["reclassified"] > 0
    llm_rows = conn.execute("SELECT theme FROM rule_theme WHERE source='llm'").fetchall()
    assert all(r["theme"] == long_key for r in llm_rows)


# --- Batching (issue #167): _build_batch_prompt / _parse_batch_response ---


def test_build_batch_prompt_references_row_ids():
    """Each row must appear in the prompt tagged with its own
    rule_candidate id (not just its position in the batch), so a
    response can be matched back to the right row unambiguously even if
    the model reorders or skips one."""
    items = [
        (101, "MOD1", "cond-a", "lit-a"),
        (202, "MOD2", "cond-b", "lit-b"),
    ]
    prompt = classify._build_batch_prompt(items)
    assert "101:" in prompt
    assert "202:" in prompt
    assert "cond-a" in prompt
    assert "lit-b" in prompt


def test_parse_batch_response_matches_ids_regardless_of_order():
    text = "202: posting\n101: eligibility\n"
    parsed = classify._parse_batch_response(text, [101, 202])
    assert parsed == {101: "eligibility", 202: "posting"}


def test_parse_batch_response_missing_row_stays_none():
    """A row the model skipped entirely (no line at all) must map to
    None -- the caller leaves it 'structural', not guessed."""
    text = "101: eligibility\n"
    parsed = classify._parse_batch_response(text, [101, 202])
    assert parsed == {101: "eligibility", 202: None}


def test_parse_batch_response_garbled_line_does_not_misassign_others():
    """A malformed/unparseable line for one row must not be attributed to
    any row, and must not disturb parsing of the other, well-formed
    lines in the same response."""
    text = "101: eligibility\nnonsense line with no id\n202: posting\n"
    parsed = classify._parse_batch_response(text, [101, 202])
    assert parsed == {101: "eligibility", 202: "posting"}


def test_parse_batch_response_ignores_id_outside_the_batch():
    """A line naming an id that wasn't part of this batch's request must
    be ignored rather than accepted -- it can't be reliably attributed to
    any row this call was actually asked about."""
    text = "999: posting\n101: eligibility\n"
    parsed = classify._parse_batch_response(text, [101])
    assert parsed == {101: "eligibility"}


def test_llm_batch_with_one_malformed_row_leaves_only_that_row_structural(indexed_db):
    """End-to-end: a batch of several rows where the response is missing
    a usable line for exactly one of them must reclassify every other
    row in the batch and leave only the malformed one at 'structural' --
    not drop or misassign anything else in the batch."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    structural_rows = conn.execute(
        "SELECT rule_candidate_id FROM rule_theme WHERE source='structural' "
        "ORDER BY rule_candidate_id"
    ).fetchall()
    assert len(structural_rows) >= 3, "fixture needs >=3 structural rows to exercise this"
    ids = [r["rule_candidate_id"] for r in structural_rows]
    broken_id = ids[1]  # some row in the middle of the batch, not first/last

    def caller(prompt: str):
        row_ids = [int(m) for m in re.findall(r"^(\d+):", prompt, re.MULTILINE)]
        lines = []
        for rc_id in row_ids:
            if rc_id == broken_id:
                lines.append("this line has no id prefix at all")
            else:
                lines.append(f"{rc_id}: posting")
        return ModelResponse(text="\n".join(lines), input_tokens=0, output_tokens=0)

    result = classify.classify_rules_llm(conn, caller, batch_size=len(ids))
    assert result["unparsed"] == 1
    assert result["reclassified"] == len(ids) - 1

    rows = conn.execute(
        "SELECT rule_candidate_id, source, theme FROM rule_theme "
        "WHERE rule_candidate_id IN ({})".format(",".join("?" * len(ids))),
        ids,
    ).fetchall()
    by_id = {r["rule_candidate_id"]: r for r in rows}
    assert by_id[broken_id]["source"] == "structural"
    for rc_id in ids:
        if rc_id != broken_id:
            assert by_id[rc_id]["source"] == "llm"
            assert by_id[rc_id]["theme"] == "posting"


def test_llm_batching_reduces_call_count_below_row_count(indexed_db):
    """The whole point of #167: grouping rows into one prompt per batch
    must actually reduce the number of caller() invocations relative to
    one-call-per-row, for a rule count bigger than one batch."""
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    structural_count = conn.execute(
        "SELECT COUNT(*) FROM rule_theme WHERE source='structural'"
    ).fetchone()[0]
    assert structural_count >= 4, "fixture needs >=4 structural rows to exercise batching"

    calls = {"n": 0}

    def counting_caller(prompt: str):
        calls["n"] += 1
        ids = re.findall(r"^(\d+):", prompt, re.MULTILINE)
        return ModelResponse(text="\n".join(f"{rc_id}: posting" for rc_id in ids), input_tokens=0, output_tokens=0)

    batch_size = max(2, structural_count // 2)
    result = classify.classify_rules_llm(conn, counting_caller, batch_size=batch_size)
    import math

    assert calls["n"] == math.ceil(structural_count / batch_size)
    assert calls["n"] < structural_count
    assert result["reclassified"] == structural_count


# --- DEFAULT_TAXONOMY / taxonomy_from_options (issue #172) -----------------
#
# These exercise the built-in fallback taxonomy against this repo's own
# bundled fixtures (examples/) -- the only "real" data available in this
# repo (see CLAUDE.md). The fixture set is tiny (59 rule_candidate rows
# from invented steel-mill-order-management source), so these patterns are
# grounded in genuine shapes found there, not proof the taxonomy performs
# this well on an arbitrary real project's rule mix -- a real project will
# still want to declare its own `options.overview.themes.taxonomy` once it
# has a representative sample of its own 'structural' rows to review.


def test_taxonomy_from_options_falls_back_to_default_when_unset(indexed_db):
    assert classify.taxonomy_from_options(None) is classify.DEFAULT_TAXONOMY
    assert classify.taxonomy_from_options({}) is classify.DEFAULT_TAXONOMY
    assert classify.taxonomy_from_options(
        {"overview": {"themes": {"taxonomy": {}}}}
    ) is classify.DEFAULT_TAXONOMY


def test_taxonomy_from_options_declared_taxonomy_replaces_not_merges(indexed_db):
    """A project's own declared taxonomy is used exactly as given -- it
    must not come back with DEFAULT_TAXONOMY's themes mixed in, the same
    replace-not-merge convention outcome_field_pattern/dispatch_field_pattern
    already use."""
    declared = {"posting": ["post"]}
    result = classify.taxonomy_from_options({"overview": {"themes": {"taxonomy": declared}}})
    assert result == declared
    assert "status" not in result
    assert "error-handling" not in result


def test_default_taxonomy_classifies_return_code_assignment_as_error_handling(indexed_db):
    conn = indexed_db
    row = conn.execute(
        "SELECT id FROM rule_candidate WHERE condition LIKE '%RETURN-CODE%' LIMIT 1"
    ).fetchone()
    assert row, "fixture must have a RETURN-CODE assignment to exercise this"
    classify.classify_rules_deterministic(conn, classify.DEFAULT_TAXONOMY)
    theme = conn.execute(
        "SELECT theme, source FROM rule_theme WHERE rule_candidate_id=?", (row["id"],)
    ).fetchone()
    assert theme["source"] == "keyword"
    assert theme["theme"] == "error-handling"


def test_default_taxonomy_classifies_status_field_as_status(indexed_db):
    conn = indexed_db
    row = conn.execute(
        "SELECT id FROM rule_candidate "
        "WHERE condition LIKE '%ORDER-STATUS%' AND condition NOT LIKE '%RETURN-CODE%' LIMIT 1"
    ).fetchone()
    assert row, "fixture must have a status-field comparison to exercise this"
    classify.classify_rules_deterministic(conn, classify.DEFAULT_TAXONOMY)
    theme = conn.execute(
        "SELECT theme, source FROM rule_theme WHERE rule_candidate_id=?", (row["id"],)
    ).fetchone()
    assert theme["source"] == "keyword"
    assert theme["theme"] == "status"


def test_default_taxonomy_status_matches_underscore_separated_field_names(indexed_db):
    """Regression for the lookaround-boundary choice over `\\b`: `\\bSTATUS\\b`
    would fail to isolate `STATUS` inside an underscore-joined identifier
    like `SCHED_STATUS` (underscore counts as a word character), even though
    it correctly isolates the hyphen-joined `ORDER-STATUS` convention. Both
    naming styles appear in this repo's own fixtures (Natural vs. the
    Mantis-style examples), so the pattern must catch both."""
    conn = indexed_db
    row = conn.execute(
        "SELECT id FROM rule_candidate WHERE condition LIKE '%SCHED_STATUS%' LIMIT 1"
    ).fetchone()
    assert row, "fixture must have an underscore-separated STATUS field to exercise this"
    classify.classify_rules_deterministic(conn, classify.DEFAULT_TAXONOMY)
    theme = conn.execute(
        "SELECT theme, source FROM rule_theme WHERE rule_candidate_id=?", (row["id"],)
    ).fetchone()
    assert theme["source"] == "keyword"
    assert theme["theme"] == "status"


def test_default_taxonomy_classifies_blank_field_check_as_validation(indexed_db):
    conn = indexed_db
    row = conn.execute(
        "SELECT id FROM rule_candidate WHERE literals = ' ' LIMIT 1"
    ).fetchone()
    assert row, "fixture must have a blank/space-literal comparison to exercise this"
    classify.classify_rules_deterministic(conn, classify.DEFAULT_TAXONOMY)
    theme = conn.execute(
        "SELECT theme, source FROM rule_theme WHERE rule_candidate_id=?", (row["id"],)
    ).fetchone()
    assert theme["source"] == "keyword"
    assert theme["theme"] == "validation"


def test_default_taxonomy_classifies_required_message_as_validation(indexed_db):
    conn = indexed_db
    row = conn.execute(
        "SELECT id FROM rule_candidate WHERE condition LIKE '%required%' LIMIT 1"
    ).fetchone()
    assert row, "fixture must have a 'required' validation message to exercise this"
    classify.classify_rules_deterministic(conn, classify.DEFAULT_TAXONOMY)
    theme = conn.execute(
        "SELECT theme, source FROM rule_theme WHERE rule_candidate_id=?", (row["id"],)
    ).fetchone()
    assert theme["source"] == "keyword"
    assert theme["theme"] == "validation"


def test_default_taxonomy_classifies_no_records_found_gap_as_data_access(indexed_db):
    conn = indexed_db
    row = conn.execute(
        "SELECT id FROM rule_candidate WHERE condition LIKE '%no records found%' LIMIT 1"
    ).fetchone()
    assert row, "fixture must have a 'no records found' gap phrase to exercise this"
    classify.classify_rules_deterministic(conn, classify.DEFAULT_TAXONOMY)
    theme = conn.execute(
        "SELECT theme, source FROM rule_theme WHERE rule_candidate_id=?", (row["id"],)
    ).fetchone()
    assert theme["source"] == "keyword"
    assert theme["theme"] == "data-access"


def test_default_taxonomy_classifies_message_field_assignment_as_messaging(indexed_db):
    conn = indexed_db
    row = conn.execute(
        "SELECT id FROM rule_candidate WHERE condition LIKE '%#MESSAGE%' LIMIT 1"
    ).fetchone()
    assert row, "fixture must have a message-field assignment to exercise this"
    classify.classify_rules_deterministic(conn, classify.DEFAULT_TAXONOMY)
    theme = conn.execute(
        "SELECT theme, source FROM rule_theme WHERE rule_candidate_id=?", (row["id"],)
    ).fetchone()
    assert theme["source"] == "keyword"
    assert theme["theme"] == "messaging"


def test_default_taxonomy_classifies_arithmetic_verb_as_calculation(indexed_db):
    conn = indexed_db
    row = conn.execute(
        "SELECT id FROM rule_candidate WHERE condition LIKE 'ADD %' LIMIT 1"
    ).fetchone()
    assert row, "fixture must have an ADD statement to exercise this"
    classify.classify_rules_deterministic(conn, classify.DEFAULT_TAXONOMY)
    theme = conn.execute(
        "SELECT theme, source FROM rule_theme WHERE rule_candidate_id=?", (row["id"],)
    ).fetchone()
    assert theme["source"] == "keyword"
    assert theme["theme"] == "calculation"


def test_default_taxonomy_shrinks_the_structural_fallback_set(indexed_db):
    """The whole point of #172: against this repo's own fixtures, the
    built-in taxonomy must classify a real share of what an empty/no
    taxonomy would otherwise leave entirely 'structural' -- not just one
    or two hand-picked rows."""
    conn = indexed_db
    total = conn.execute("SELECT COUNT(*) FROM rule_candidate").fetchone()[0]
    counts = classify.classify_rules_deterministic(conn, classify.DEFAULT_TAXONOMY)
    assert counts["keyword"] + counts["structural"] == total
    assert counts["keyword"] > 0
    # at least half of this fixture set's rows get a real keyword theme --
    # see the module-docstring-adjacent comment above on how representative
    # (or not) this small a sample is
    assert counts["keyword"] >= total // 2


def test_default_taxonomy_does_not_regress_an_explicit_project_taxonomy(indexed_db):
    """A project that declares its own taxonomy must still see exactly its
    own keywords take effect (via taxonomy_from_options), not have
    DEFAULT_TAXONOMY's themes silently mixed in alongside them."""
    conn = indexed_db
    declared = {"posting": ["invalid"]}
    resolved = classify.taxonomy_from_options({"overview": {"themes": {"taxonomy": declared}}})
    classify.classify_rules_deterministic(conn, resolved)
    themes_used = {
        r["theme"] for r in conn.execute(
            "SELECT DISTINCT theme FROM rule_theme WHERE source='keyword'"
        ).fetchall()
    }
    assert themes_used <= {"posting"}
