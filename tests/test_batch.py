"""Guards on the batch narrative harness (Phase 3, option C).

Uses a fake caller (no network, no API key) that returns a canned,
well-formed document for anything except a deliberately-broken first draft,
so the retry-once-then-report path is exercised without depending on a real
model. Acceptance per the plan is "9 fixtures produce N valid documents
unattended, with a cost figure and a retry count reported" -- this repo's
fixture set has 13 batchable (natural/mantis program-level) members; the
other fixtures are data definitions and environment sources that option C
deliberately routes to the CLI path instead, not module docs.
"""

from __future__ import annotations

import json
import re

from mfdoc import batch as batch_mod
from mfdoc.redact import NULL_REDACTOR, Redactor


def _track_module_brief_calls(monkeypatch) -> list[str]:
    """Wrap batch_mod.module_brief to record every member name it's called
    with, while still delegating to the real implementation -- shared by
    tests that assert module_brief() was (or wasn't) called for particular
    members."""
    calls: list[str] = []
    real_module_brief = batch_mod.module_brief

    def counting_module_brief(*args, **kwargs):
        calls.append(args[1] if len(args) > 1 else kwargs.get("member_name"))
        return real_module_brief(*args, **kwargs)

    monkeypatch.setattr(batch_mod, "module_brief", counting_module_brief)
    return calls

GOOD_FRONTMATTER = """---
title: "{member} — test doc"
doc_type: module
system: MOM
module: "{member}"
dialect: natural
library: MILLPROD
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-01-01"
review_status: draft
reviewers: []
confidence_summary:
  verified: 1
sources: ["{member}"]
sme_questions: []
---
"""


def _fake_reconciliation_response(prompt: str, input_tokens: int = 10,
                                   output_tokens: int = 20) -> "batch_mod.ModelResponse":
    """A valid response to batch.py's whole-module narrative-synthesis
    prompt (build_reconciliation_prompt) -- distinguished from an ordinary
    per-member/per-chunk prompt by not carrying a "# Fact brief:" heading.
    Reuses whichever `[[MEMBER:LINE]]` citation the prompt's own per-chunk
    excerpts already carry, so the reconciled sections cite forward exactly
    as the real feature is meant to, rather than inventing one."""
    m = re.search(r"\[\[(\w[\w#@$&.\-]*):(\d+)\]\]", prompt)
    cite = f"[[{m.group(1)}:{m.group(2)}]]" if m else "[[UNKNOWN:1]]"
    text = "\n\n".join(
        f"## {h}\n\nReconciled across chunks {cite}." for h in batch_mod.NARRATIVE_SECTIONS
    )
    return batch_mod.ModelResponse(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


class FakeCaller:
    """Returns a valid doc for every prompt; records prompts it was called with."""

    def __init__(self, fail_first: bool = False):
        self.fail_first = fail_first
        self.calls = 0
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> batch_mod.ModelResponse:
        self.calls += 1
        self.prompts.append(prompt)
        if "# Fact brief:" not in prompt:
            # The one-per-chunked-member narrative-synthesis call, not an
            # ordinary member/chunk brief -- see _fake_reconciliation_response.
            return _fake_reconciliation_response(prompt)
        # A retry prompt carries the "Previous attempt failed" section.
        is_retry = "Previous attempt failed validation" in prompt
        if self.fail_first and not is_retry:
            text = "not even front matter, this will fail validation\n"
        else:
            # Pull the member name back out of the brief's own front-matter-less
            # heading line ("# Fact brief: NAME") so the fake response is valid
            # for whichever member it was generated for.
            member = prompt.split("# Fact brief:")[1].splitlines()[0].strip()
            sections = "\n\n".join(
                f"## {h}\n\nThis module does something [[{member}:1]]."
                for h in batch_mod.NARRATIVE_SECTIONS
            )
            text = (
                GOOD_FRONTMATTER.format(member=member)
                + f"\n# {member}\n\n{sections}\n"
            )
        return batch_mod.ModelResponse(text=text, input_tokens=100, output_tokens=200)


def test_select_batch_members_returns_only_natural_and_mantis_programs(indexed_db):
    members = batch_mod.select_batch_members(indexed_db)
    assert set(members) == {
        "MMP0100", "MMP0200", "MMP9000", "MMP9100", "MMP9200", "MMP9300", "MMP9400", "MMP9500",
        "MMP9600", "MMP9700", "MMP9800", "MMC0100", "ORDENQ", "SCRNENT",
    }


def test_batch_generates_valid_docs_for_all_batchable_members(indexed_db, tmp_path):
    members = batch_mod.select_batch_members(indexed_db)
    caller = FakeCaller()
    writing_rules = "cite everything"
    template = "module template"
    summary = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, writing_rules, template,
        redact=NULL_REDACTOR, concurrency=2, state_path=None,
    )
    assert summary.ok == len(members) == 14
    assert summary.failed == 0
    assert summary.retried == 0
    assert summary.total_input_tokens == 1400
    assert summary.total_output_tokens == 2800
    for member in members:
        subdir = batch_mod._output_subdir(indexed_db, member)
        assert (tmp_path / "out" / subdir / f"{member}.md").exists()


def test_written_doc_gets_the_installed_version_not_whatever_the_model_wrote(indexed_db, tmp_path):
    """FakeCaller's GOOD_FRONTMATTER hardcodes "legacy-functional-docs 0.1.0"
    (a stand-in for a model echoing reference/writing-rules.md's own worked
    example verbatim, since nothing in the prompt tells it what the real
    installed version is) -- the file actually written to disk must carry
    the real installed version regardless, or the staleness check would
    immediately flag every freshly generated document."""
    from mfdoc import __version__

    out_path = tmp_path / "MMP0100.md"
    result = batch_mod.generate_module_doc(
        indexed_db, "MMP0100", out_path, FakeCaller(), "cite everything", "module template",
    )
    assert result.ok, result.problems
    text = out_path.read_text(encoding="utf-8")
    assert f"generated_by: legacy-functional-docs {__version__}" in text


def test_fix_generated_by_version_replaces_regardless_of_what_the_model_wrote():
    from mfdoc import __version__

    text = "---\ntitle: X\ngenerated_by: legacy-functional-docs 0.1.0\n---\nbody\n"
    fixed = batch_mod._fix_generated_by_version(text)
    assert fixed == f"---\ntitle: X\ngenerated_by: legacy-functional-docs {__version__}\n---\nbody\n"


def test_fix_generated_by_version_is_a_no_op_when_the_line_is_missing_or_different_tool():
    """Never invents a generated_by line that isn't there, and never touches
    a different tool's own generated_by (this repo's only writer is itself,
    but the function shouldn't assume that)."""
    no_line = "---\ntitle: X\n---\nbody\n"
    assert batch_mod._fix_generated_by_version(no_line) == no_line

    other_tool = "---\ntitle: X\ngenerated_by: some-other-tool 3.0\n---\nbody\n"
    assert batch_mod._fix_generated_by_version(other_tool) == other_tool


def test_estimate_cost_computes_dollar_amount_when_pricing_configured():
    """estimate_cost is the single shared formula behind run_batch's own
    cost_usd (see test_batch_reports_cost_only_when_pricing_configured
    below) and cmd_classify_rules's cost line in cli.py -- both must reuse
    it rather than retyping the arithmetic inline."""
    cost = batch_mod.estimate_cost(1400, 2800, cost_per_mtok_in=3.0, cost_per_mtok_out=15.0)
    expected = (1400 / 1_000_000) * 3.0 + (2800 / 1_000_000) * 15.0
    assert cost == expected


def test_estimate_cost_returns_none_when_pricing_not_configured():
    """Either rate missing (not just both) must produce the shared
    "unknown" sentinel (None), not a partial/garbage dollar figure."""
    assert batch_mod.estimate_cost(1400, 2800, None, None) is None
    assert batch_mod.estimate_cost(1400, 2800, cost_per_mtok_in=3.0, cost_per_mtok_out=None) is None
    assert batch_mod.estimate_cost(1400, 2800, cost_per_mtok_in=None, cost_per_mtok_out=15.0) is None


def test_batch_reports_cost_only_when_pricing_configured(indexed_db, tmp_path):
    members = batch_mod.select_batch_members(indexed_db)
    caller = FakeCaller()
    summary_no_price = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out1", caller, "rules", "template",
    )
    assert summary_no_price.cost_usd is None

    caller2 = FakeCaller()
    summary_priced = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out2", caller2, "rules", "template",
        cost_per_mtok_in=3.0, cost_per_mtok_out=15.0,
    )
    expected = (1400 / 1_000_000) * 3.0 + (2800 / 1_000_000) * 15.0
    assert summary_priced.cost_usd == expected


def test_batch_retries_once_on_validation_failure_then_reports(indexed_db, tmp_path):
    members = ["MMP0100"]
    caller = FakeCaller(fail_first=True)
    summary = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
    )
    assert summary.retried == 1
    assert summary.ok == 1
    result = summary.results[0]
    assert result.attempts == 2
    # First call is the fresh attempt, second is the retry with failure detail.
    assert caller.calls == 2
    assert "Previous attempt failed validation" in caller.prompts[1]


def test_batch_skips_unchanged_members_on_resume(indexed_db, tmp_path):
    members = ["MMP0100"]
    state_path = tmp_path / "state.json"
    caller = FakeCaller()
    first = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )
    assert first.ok == 1 and first.skipped == 0
    assert caller.calls == 1

    second = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )
    assert second.skipped == 1
    assert second.ok == 1
    assert caller.calls == 1, "resumed run must not re-call the model for an unchanged member"


def test_batch_skips_module_brief_entirely_when_corpus_unchanged(indexed_db, tmp_path, monkeypatch):
    members = ["MMP0100", "MMP0200"]
    state_path = tmp_path / "state.json"
    caller = FakeCaller()
    first = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )
    assert first.ok == 2 and first.skipped == 0

    calls = _track_module_brief_calls(monkeypatch)

    second = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )
    assert second.skipped == 2 and second.ok == 2
    assert calls == [], "unchanged corpus must skip module_brief() entirely, not just the model call"


def test_batch_recomputes_briefs_when_a_source_file_changes(indexed_db, tmp_path, monkeypatch):
    # indexed_db is session-scoped and shared with every other test module,
    # so the sha256 mutation below must be reverted before this test exits.
    row = indexed_db.execute(
        "SELECT source_file_id AS id FROM member WHERE name = 'MMP0100'"
    ).fetchone()
    file_id = row["id"]
    original_sha = indexed_db.execute(
        "SELECT sha256 FROM source_file WHERE id = ?", (file_id,)
    ).fetchone()["sha256"]

    members = ["MMP0100", "MMP0200"]
    state_path = tmp_path / "state.json"
    caller = FakeCaller()
    try:
        first = batch_mod.run_batch(
            indexed_db, members, tmp_path / "out", caller, "rules", "template",
            state_path=state_path,
        )
        assert first.ok == 2

        # Simulate a re-ingest that changed one file's content: bump its
        # sha256 directly, as `mfdoc ingest` would after re-hashing changed
        # source.
        indexed_db.execute(
            "UPDATE source_file SET sha256 = ? WHERE id = ?",
            ("deadbeef" + original_sha, file_id),
        )
        indexed_db.commit()

        calls = _track_module_brief_calls(monkeypatch)

        second = batch_mod.run_batch(
            indexed_db, members, tmp_path / "out", caller, "rules", "template",
            state_path=state_path,
        )
        # Corpus signature changed, so the per-member fallback re-derives
        # every brief -- but since neither member's actual brief text
        # changed (the sha bump didn't touch any fact table), the model is
        # still not re-called.
        assert set(calls) == {"MMP0100", "MMP0200"}
        assert second.skipped == 2 and second.ok == 2
        assert caller.calls == 2, "model must not be re-called when a member's own brief hash is unchanged"
    finally:
        indexed_db.execute(
            "UPDATE source_file SET sha256 = ? WHERE id = ?", (original_sha, file_id)
        )
        indexed_db.commit()


def test_batch_recomputes_when_redact_policy_changes_with_no_source_edits(indexed_db, tmp_path, monkeypatch):
    """redact is a project.yml config knob, not anything a source_file hash
    can see -- the corpus-level skip must not mask a policy change with no
    source edits behind it."""
    members = ["MMP0100", "MMP0200"]
    state_path = tmp_path / "state.json"
    caller = FakeCaller()
    first = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path, redact=NULL_REDACTOR,
    )
    assert first.ok == 2 and first.skipped == 0

    calls = _track_module_brief_calls(monkeypatch)
    changed_redact = Redactor(patterns=[r"MILLPROD"], enabled=True)
    second = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path, redact=changed_redact,
    )
    assert set(calls) == {"MMP0100", "MMP0200"}, (
        "a redact policy change with no source edits must still force module_brief() "
        "to be recomputed, not be masked by the corpus-level skip"
    )


def test_batch_recomputes_when_lexicon_changes_with_no_source_edits(indexed_db, tmp_path, monkeypatch):
    """lexicon is likewise project.yml config, not derived from source_file --
    same soundness requirement as the redact-policy case above."""
    members = ["MMP0100", "MMP0200"]
    state_path = tmp_path / "state.json"
    caller = FakeCaller()
    first = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path, lexicon={},
    )
    assert first.ok == 2 and first.skipped == 0

    calls = _track_module_brief_calls(monkeypatch)
    second = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path, lexicon={"MOM": "Month-of-Month report"},
    )
    assert set(calls) == {"MMP0100", "MMP0200"}, (
        "a lexicon change with no source edits must still force module_brief() "
        "to be recomputed, not be masked by the corpus-level skip"
    )


# --- Chunked rendering for members with many business rules (see DEFAULT_MAX_RULES_PER_CALL) ---

def _seed_fakemod_rules(conn, count: int):
    from mfdoc.db import insert

    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    for n in range(1, count + 1):
        insert(
            conn, "rule_candidate", member_id=1, line_no=n, construct="IF",
            condition=f"COND-{n}", raw=f"IF COND-{n}",
        )
    conn.commit()


def _chunk_aware_module_caller():
    """A fake caller that returns a fully valid single-chunk module doc,
    citing exactly the FAKEMOD:BR-nnn ids present in the prompt it was
    sent -- mirrors what a real model does for one chunk's brief, without a
    real call. Mirrors test_test_batch.py's _chunk_aware_caller.

    Also answers the one-per-chunked-member narrative-synthesis call
    (distinguished by the absence of "# Fact brief:", the per-chunk brief's
    own heading) with a valid five-section reconciliation, so tests
    exercising the full chunked path (chunk generation *and* the whole-
    module overview it now produces) don't need two different fakes."""
    ids_re = re.compile(r"FAKEMOD:BR-\d+")

    def caller(prompt: str) -> batch_mod.ModelResponse:
        if "# Fact brief:" not in prompt:
            return _fake_reconciliation_response(prompt, input_tokens=1, output_tokens=2)
        ids = sorted(set(ids_re.findall(prompt)))
        rule_lines = "\n".join(f"1. **{i}** [[FAKEMOD:1]] rule text." for i in ids)
        sections = "\n\n".join(
            f"## {h}\n\nCovers the module as a whole [[FAKEMOD:1]]." for h in batch_mod.NARRATIVE_SECTIONS[:4]
        )
        text = f"""---
title: "FAKEMOD — module documentation"
doc_type: module
system: MOM
module: FAKEMOD
dialect: natural
library: MILLPROD
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-01-01"
review_status: draft
reviewers: []
confidence_summary:
  verified: {len(ids)}
sources: ["FAKEMOD"]
sme_questions: []
---

# FAKEMOD

{sections}

## Business rules

{rule_lines}

## Outputs and effects

Nothing beyond what's cited above [[FAKEMOD:1]].
"""
        return batch_mod.ModelResponse(text=text, input_tokens=1, output_tokens=2)
    return caller


def test_generate_module_doc_chunks_a_member_with_many_rules(tmp_path):
    """A member whose own rule_candidate count exceeds max_rules_per_call
    renders as several independent chunk documents plus a deterministic
    index doc at the normal out_path -- proves the whole chunked path end
    to end: every chunk validates, the index aggregates real (not invented)
    confidence numbers from the chunks, and every rule across all 5
    candidates ends up in the index."""
    import sqlite3
    from mfdoc.db import SCHEMA
    from mfdoc.validate import validate_doc

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    result = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, _chunk_aware_module_caller(),
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert result.ok is True, result.problems
    assert result.attempts == 3  # ceil(5 / 2) chunks
    assert result.chunked is True

    for i in (1, 2, 3):
        chunk_path = tmp_path / f"FAKEMOD.chunk{i}.md"
        assert chunk_path.exists()
        assert validate_doc(conn, chunk_path)["ok"]

    index_text = out_path.read_text(encoding="utf-8")
    assert "doc_type: module_index" in index_text
    assert "verified: 5" in index_text, "confidence_summary must aggregate all 5 chunked rules"
    # Ranges, not a flat per-id enumeration -- one range per chunk, covering
    # the member's full BR-001..BR-005 span between them.
    assert "`FAKEMOD:BR-001`..`FAKEMOD:BR-002`" in index_text
    assert "`FAKEMOD:BR-003`..`FAKEMOD:BR-004`" in index_text
    assert "`FAKEMOD:BR-005`..`FAKEMOD:BR-005`" in index_text
    # The whole-module narrative synthesis ran (every chunk was ok) and its
    # reconciled sections made it into the assembled document.
    assert "Reconciled across chunks" in index_text
    assert "## Gaps and questions for review" in index_text
    assert "## Chunk files" in index_text

    revalidated = validate_doc(conn, out_path)
    assert revalidated["ok"], revalidated["problems"]


def test_generate_module_doc_reports_failure_when_one_chunk_fails(tmp_path):
    """One bad chunk must fail the whole member (ok=False) with a problem
    naming which chunk, but must not prevent the other chunks from
    rendering and validating on their own."""
    import sqlite3
    from mfdoc.db import SCHEMA
    from mfdoc.validate import validate_doc

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 4)

    good_caller = _chunk_aware_module_caller()
    reconciliation_calls = []

    def flaky_caller(prompt: str) -> batch_mod.ModelResponse:
        if "# Fact brief:" not in prompt:
            reconciliation_calls.append(prompt)
        if "BR-003" in prompt:
            return batch_mod.ModelResponse(text="not a valid document", input_tokens=1, output_tokens=1)
        return good_caller(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    result = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, flaky_caller,
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert result.ok is False
    assert any("chunk 2" in p for p in result.problems)
    assert any("narrative synthesis: skipped" in p for p in result.problems)
    assert not reconciliation_calls, (
        "narrative synthesis must never be attempted when a chunk failed"
    )

    assert (tmp_path / "FAKEMOD.chunk1.md").exists()
    assert validate_doc(conn, tmp_path / "FAKEMOD.chunk1.md")["ok"]

    index_text = out_path.read_text(encoding="utf-8")
    assert "narrative synthesis was skipped" in index_text.lower()


def test_chunked_brief_names_the_chunk_a_routine_in_another_chunk_is_documented_in(tmp_path):
    """A routine whose rules fall in a *different* chunk than the one being
    narrated must be annotated in that chunk's own brief with its concrete
    chunk number -- this is what lets the model write "documented in chunk
    2" instead of the vague, unresolved "covered by a later chunk" real
    generated docs have been observed producing. A routine in the *same*
    chunk gets no such annotation (nothing to forward-reference)."""
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 4)
    insert(conn, "routine", member_id=1, name="ROUTINE-A", kind="natural_subroutine",
           start_line=1, end_line=2)
    insert(conn, "routine", member_id=1, name="ROUTINE-B", kind="natural_subroutine",
           start_line=3, end_line=4)
    conn.commit()

    briefs: dict[int, str] = {}

    def recording_caller(prompt: str) -> batch_mod.ModelResponse:
        import re as _re
        match = _re.search(r"chunk (\d+) of", prompt)
        if match is not None:
            briefs[int(match.group(1))] = prompt
        return _chunk_aware_module_caller()(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    result = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, recording_caller,
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert result.ok is True, result.problems
    assert set(briefs) == {1, 2}

    # Chunk 1 covers ROUTINE-A's own rules (lines 1-2) -- no self-reference.
    assert "`ROUTINE-A` (natural_subroutine)" in briefs[1]
    assert "`ROUTINE-A` (natural_subroutine) [[FAKEMOD:1-2]] **[documented" not in briefs[1]
    # ROUTINE-B's rules fall in chunk 2 -- chunk 1's brief must name it concretely.
    assert "[documented in chunk 2]" in briefs[1]
    # Chunk 2's own brief must likewise point back at chunk 1 for ROUTINE-A
    # (documented there, not here) -- but not at itself for ROUTINE-B.
    assert "[documented in chunk 1]" in briefs[2]
    assert "[documented in chunk 2]" not in briefs[2]


def test_chunk_map_is_correct_when_rule_candidate_rows_share_a_line_no(tmp_path):
    """rule_candidate.line_no has no uniqueness constraint -- two rows can
    legitimately share one (e.g. a compound condition split into several
    rows at the same source line). The routine -> chunk lookup must key off
    each row's own position in the ordered rule_rows list, not a line_no ->
    ordinal dict (which would silently collapse same-line rows to whichever
    is last and could pick the wrong one)."""
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    # Two rule_candidate rows share line_no=1, both inside ROUTINE-A's span.
    for n in (1, 2):
        insert(conn, "rule_candidate", member_id=1, line_no=1, construct="IF",
               condition=f"COND-{n}", raw=f"IF COND-{n}")
    insert(conn, "rule_candidate", member_id=1, line_no=2, construct="IF",
           condition="COND-3", raw="IF COND-3")
    insert(conn, "routine", member_id=1, name="ROUTINE-A", kind="natural_subroutine",
           start_line=1, end_line=1)
    insert(conn, "routine", member_id=1, name="ROUTINE-B", kind="natural_subroutine",
           start_line=2, end_line=2)
    conn.commit()

    briefs: dict[int, str] = {}

    def recording_caller(prompt: str) -> batch_mod.ModelResponse:
        import re as _re
        match = _re.search(r"chunk (\d+) of", prompt)
        if match is not None:
            briefs[int(match.group(1))] = prompt
        return _chunk_aware_module_caller()(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    result = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, recording_caller,
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert result.ok is True, result.problems
    assert set(briefs) == {1, 2}
    # ROUTINE-A's two same-line rules must both land in chunk 1, and
    # ROUTINE-B's own rule in chunk 2 -- not the reverse.
    assert "[documented in chunk 2]" in briefs[1]
    assert "[documented in chunk 1]" in briefs[2]


def test_run_batch_chunks_a_large_member_and_still_batches_small_ones(indexed_db, tmp_path):
    """A member over the rule threshold takes the chunked path while a
    normal-sized member in the same run still goes through the ordinary
    pooled single-call path -- proves run_batch() routes per-member, not
    all-or-nothing."""
    members = batch_mod.select_batch_members(indexed_db)
    assert "MMP0100" in members
    caller = FakeCaller()
    summary = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        max_rules_per_call=1,
    )
    subdir = batch_mod._output_subdir(indexed_db, "MMP0100")
    chunk1 = tmp_path / "out" / subdir / "MMP0100.chunk1.md"
    assert chunk1.exists(), "MMP0100 has more than one rule_candidate in the fixtures and must chunk"
    # A member with zero (or exactly one) rule_candidate never chunks --
    # it should have gone through the ordinary single-call path instead.
    for member in members:
        if member == "MMP0100":
            continue
        subdir = batch_mod._output_subdir(indexed_db, member)
        out_path = tmp_path / "out" / subdir / f"{member}.md"
        assert out_path.exists()
    assert summary.failed == 0


def test_resolve_max_rules_per_call():
    from mfdoc.batch import DEFAULT_MAX_RULES_PER_CALL, _resolve_max_rules_per_call

    assert _resolve_max_rules_per_call(None) == DEFAULT_MAX_RULES_PER_CALL
    assert _resolve_max_rules_per_call(5) == 5
    for bad in (0, -1):
        try:
            _resolve_max_rules_per_call(bad)
            assert False, f"{bad} should have raised"
        except ValueError:
            pass


def test_batch_tolerates_non_dict_state_entry_for_a_member(indexed_db, tmp_path):
    """state[name] is expected to be a per-member dict (run_batch() writes
    {"ok":..., "attempts":..., "brief_sha256":...}), but run_batch() also
    writes a "_corpus_sha256" sentinel into the same flat namespace, whose
    value is a plain hash string -- and an un-normalised --members value
    could in principle collide with it (see cli.py's --members handling).
    Whatever the cause, a non-dict prior for a member must not crash
    run_batch() -- it should just be treated as "no usable prior result"."""
    members = ["MMP0100"]
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"MMP0100": "not-a-dict"}), encoding="utf-8")

    caller = FakeCaller()
    result = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )
    assert result.ok == 1 and result.skipped == 0
    assert caller.calls == 1


def test_retry_prompt_carries_a_specific_hint_for_a_dropped_front_matter_block(indexed_db, tmp_path):
    members = ["MMP0100"]
    caller = FakeCaller(fail_first=True)
    batch_mod.run_batch(indexed_db, members, tmp_path / "out", caller, "rules", "template")
    assert caller.calls == 2
    assert "begin with the literal" in caller.prompts[1]


class _SelfNarratingThenGoodCaller:
    """First response has valid front matter but opens its body with
    commentary instead of the template's required heading; second response
    (the retry) is well-formed."""

    def __init__(self):
        self.calls = 0
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> batch_mod.ModelResponse:
        self.calls += 1
        self.prompts.append(prompt)
        member = prompt.split("# Fact brief:")[1].splitlines()[0].strip()
        if "Previous attempt failed validation" not in prompt:
            text = (
                GOOD_FRONTMATTER.format(member=member)
                + f"\nI'll now document {member} as instructed.\n\n"
                  f"This module does something [[{member}:1]].\n"
            )
        else:
            text = (
                GOOD_FRONTMATTER.format(member=member)
                + f"\n# {member}\n\nThis module does something [[{member}:1]].\n"
            )
        return batch_mod.ModelResponse(text=text, input_tokens=100, output_tokens=200)


def test_retry_prompt_carries_a_specific_hint_for_a_self_narrating_opening(indexed_db, tmp_path):
    members = ["MMP0100"]
    caller = _SelfNarratingThenGoodCaller()
    summary = batch_mod.run_batch(indexed_db, members, tmp_path / "out", caller, "rules", "template")
    assert caller.calls == 2
    assert "Do not narrate what you are about to do" in caller.prompts[1]
    assert summary.ok == 1


def _counting_caller(caller):
    """Wraps any ModelCaller to also expose a `.calls` count -- used to
    prove a resumed chunk generation makes zero (or exactly the expected
    number of) model calls, not just that it produces the right output."""
    def counted(prompt: str) -> batch_mod.ModelResponse:
        counted.calls += 1
        return caller(prompt)
    counted.calls = 0
    return counted


def test_chunk_resume_makes_no_model_calls_when_every_chunk_brief_is_unchanged(tmp_path):
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    first_caller = _counting_caller(_chunk_aware_module_caller())
    first = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, first_caller,
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert first.ok is True
    assert first_caller.calls == 4  # 3 chunks + 1 whole-module narrative synthesis
    assert first.chunk_state is not None and set(first.chunk_state) == {"1", "2", "3", "_narrative"}

    def exploding_caller(prompt: str) -> batch_mod.ModelResponse:
        raise AssertionError("must not call the model for an unchanged chunk")

    second = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, exploding_caller,
        "writing rules text", "template text", max_rules_per_call=2,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True
    assert second.chunk_state == first.chunk_state


def test_chunk_resume_only_regenerates_the_chunk_whose_own_brief_changed(tmp_path):
    """Changing one rule_candidate's condition text only changes the brief
    -- and so only the model call -- for the chunk whose range that rule
    falls in; the other chunks must be reused untouched."""
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    first_caller = _counting_caller(_chunk_aware_module_caller())
    first = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, first_caller,
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert first_caller.calls == 4  # 3 chunks + 1 whole-module narrative synthesis
    chunk1_before = (tmp_path / "FAKEMOD.chunk1.md").read_text(encoding="utf-8")

    # Rule 3 (line 3) falls in chunk 2's range (rules 3-4) -- change its
    # condition text so only that chunk's brief hash changes.
    conn.execute("UPDATE rule_candidate SET condition='COND-3-CHANGED' WHERE line_no=3")
    conn.commit()

    second_caller = _counting_caller(_chunk_aware_module_caller())
    second = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, second_caller,
        "writing rules text", "template text", max_rules_per_call=2,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True
    # Only chunk 2 re-renders (its own brief hash changed); chunks 1 and 3
    # are reused untouched. The whole-module narrative synthesis is reused
    # too here -- _chunk_aware_module_caller's response only depends on
    # which BR ids are in a chunk's prompt, not the (changed) condition
    # text, so chunk 2's regenerated body is byte-identical to before and
    # the reconciliation's own input hash is unaffected.
    assert second_caller.calls == 1, "only the chunk covering the changed rule should re-render"
    assert (tmp_path / "FAKEMOD.chunk1.md").read_text(encoding="utf-8") == chunk1_before


def test_chunk_resume_falls_back_to_regenerating_a_chunk_whose_cached_file_is_gone(tmp_path):
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    first = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, _chunk_aware_module_caller(),
        "writing rules text", "template text", max_rules_per_call=2,
    )
    (tmp_path / "FAKEMOD.chunk2.md").unlink()

    second_caller = _counting_caller(_chunk_aware_module_caller())
    second = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, second_caller,
        "writing rules text", "template text", max_rules_per_call=2,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True
    assert second_caller.calls == 1
    assert (tmp_path / "FAKEMOD.chunk2.md").exists()


def test_run_batch_persists_chunk_state_and_reuses_it_across_calls(indexed_db, tmp_path):
    """End-to-end through run_batch's own state file, not just the lower-
    level generate_module_doc -- a second run against unchanged facts must
    make zero model calls for the already-chunked member's chunks."""
    members = ["MMP0100"]
    state_path = tmp_path / "state.json"
    first_caller = FakeCaller()
    summary1 = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", first_caller, "rules", "template",
        max_rules_per_call=1, state_path=state_path,
    )
    assert summary1.failed == 0
    saved = json.loads(state_path.read_text())
    subdir = batch_mod._output_subdir(indexed_db, "MMP0100")
    state_key = f"{subdir.as_posix()}/MMP0100"
    assert "chunks" in saved[state_key]
    chunk_count_first_run = first_caller.calls

    second_caller = FakeCaller()
    summary2 = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", second_caller, "rules", "template",
        max_rules_per_call=1, state_path=state_path,
    )
    assert summary2.failed == 0
    assert second_caller.calls == 0, "every chunk should be reused, not re-rendered"
    assert chunk_count_first_run > 0


def test_chunk_processing_labels_names_routines_and_falls_back_to_main_body():
    """One label per (start, end) range, naming the routine(s) whose rules
    fall in it (first-seen order, backtick-quoted), or the plain
    main-body fallback when a range's rules belong to no routine at all."""
    rule_rows = [
        {"line_no": 10},  # in ROUTINE-A
        {"line_no": 11},  # in ROUTINE-A
        {"line_no": 30},  # in ROUTINE-B
        {"line_no": 50},  # main body -- no routine
    ]
    routines = [
        {"name": "ROUTINE-A", "start_line": 5, "end_line": 20},
        {"name": "ROUTINE-B", "start_line": 25, "end_line": 40},
    ]
    ranges = [(1, 2), (3, 3), (4, 4)]

    labels = batch_mod._chunk_processing_labels(rule_rows, routines, ranges)

    assert labels == [
        "`ROUTINE-A`",
        "`ROUTINE-B`",
        "member main body (no internal routine)",
    ]


def test_consolidated_gap_lines_dedupes_gap_rows_and_chunk_sme_questions(tmp_path):
    """Gap-register rows (severity DESC) come first; a chunk's own
    sme_questions add text not already present, and an exact duplicate --
    whether between two chunks or of a gap row's own text -- appears only
    once."""
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    insert(conn, "gap", member_id=1, gap_kind="orphan_module", severity="high",
           detail="No caller found for FAKEMOD. Confirm whether this is dead code.", line_no=None)
    insert(conn, "gap", member_id=1, gap_kind="unused_field", severity="low",
           detail="Field X is never referenced.", line_no=7)
    conn.commit()

    def _chunk_with_questions(path, questions):
        fm_lines = ["---", "sme_questions:"]
        fm_lines += [f'  - "{q}"' for q in questions]
        fm_lines.append("---\n")
        path.write_text("\n".join(fm_lines), encoding="utf-8")

    chunk1 = tmp_path / "FAKEMOD.chunk1.md"
    chunk2 = tmp_path / "FAKEMOD.chunk2.md"
    _chunk_with_questions(chunk1, ["Is X still used by any downstream job?"])
    _chunk_with_questions(chunk2, [
        "Is X still used by any downstream job?",  # duplicate of chunk1's own
        "No caller found for FAKEMOD.",  # duplicate of a gap row's own first sentence
    ])

    lines = batch_mod._consolidated_gap_lines(conn, "FAKEMOD", 1, [chunk1, chunk2])

    # Same "ORDER BY severity DESC" as module_brief's own "Known gaps"
    # section -- a plain text sort, not a severity-priority one, so "low"
    # sorts ahead of "high" (both are gap rows either way, still first).
    assert lines[0].startswith("[low]") and "unused_field" in lines[0]
    assert lines[1].startswith("[high]") and "orphan_module" in lines[1]
    assert lines.count("Is X still used by any downstream job?") == 1
    assert not any(line == "No caller found for FAKEMOD." for line in lines[2:])
    assert len(lines) == 3  # 2 gap rows + 1 genuinely new sme_question


def test_extract_section_finds_named_heading_and_returns_none_when_absent_or_blank():
    body = "# Doc\n\n## Purpose\n\nSome text here.\n\n## Inputs\n\n\n\n## Outputs and effects\n\nMore text.\n"
    assert batch_mod._extract_section(body, "Purpose") == "Some text here."
    assert batch_mod._extract_section(body, "Outputs and effects") == "More text."
    assert batch_mod._extract_section(body, "Inputs") is None  # present but blank
    assert batch_mod._extract_section(body, "Data used") is None  # absent entirely


def test_split_reconciled_sections_reports_missing_headings():
    text = "\n\n".join(
        f"## {h}\n\nfilled in." for h in batch_mod.NARRATIVE_SECTIONS if h != "Inputs"
    )
    sections, missing = batch_mod._split_reconciled_sections(text)
    assert missing == ["Inputs"]
    assert set(sections) == set(batch_mod.NARRATIVE_SECTIONS) - {"Inputs"}


def _valid_narrative_response(cite: str) -> str:
    return "\n\n".join(f"## {h}\n\nReconciled {cite}." for h in batch_mod.NARRATIVE_SECTIONS)


def test_generate_module_index_narrative_succeeds_first_try(tmp_path):
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    conn.commit()

    calls = []

    def caller(prompt):
        calls.append(prompt)
        return batch_mod.ModelResponse(text=_valid_narrative_response("[[FAKEMOD:1]]"), input_tokens=5, output_tokens=6)

    out_path = tmp_path / "FAKEMOD.md"

    def assemble(sections):
        body = "\n".join(f"## {h}\n\n{sections[h]}\n" for h in batch_mod.NARRATIVE_SECTIONS)
        return (
            "---\ntitle: \"FAKEMOD\"\ndoc_type: module_index\nsystem: MOM\n"
            "generated_by: legacy-functional-docs 0.1.0\ngenerated_at: \"2026-01-01\"\n"
            "review_status: draft\nconfidence_summary:\n  verified: 1\nsources: [\"FAKEMOD\"]\n---\n"
            f"\n# FAKEMOD\n\n{body}"
        )

    ok, attempts, in_tok, out_tok, problems, sections = batch_mod._generate_module_index_narrative(
        conn, "FAKEMOD", [(1, "## Purpose\n\nSomething [[FAKEMOD:1]].")], caller,
        "writing rules", None, out_path, assemble, max_attempts=2,
    )
    assert ok is True
    assert attempts == 1
    assert len(calls) == 1
    assert in_tok == 5 and out_tok == 6
    assert problems == []


def test_generate_module_index_narrative_retries_once_on_missing_section(tmp_path):
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    conn.commit()

    responses = [
        "\n\n".join(
            f"## {h}\n\nReconciled [[FAKEMOD:1]]." for h in batch_mod.NARRATIVE_SECTIONS if h != "Inputs"
        ),
        _valid_narrative_response("[[FAKEMOD:1]]"),
    ]
    calls = []

    def caller(prompt):
        calls.append(prompt)
        text = responses[len(calls) - 1]
        return batch_mod.ModelResponse(text=text, input_tokens=1, output_tokens=1)

    out_path = tmp_path / "FAKEMOD.md"

    def assemble(sections):
        body = "\n".join(f"## {h}\n\n{sections[h]}\n" for h in batch_mod.NARRATIVE_SECTIONS)
        return (
            "---\ntitle: \"FAKEMOD\"\ndoc_type: module_index\nsystem: MOM\n"
            "generated_by: legacy-functional-docs 0.1.0\ngenerated_at: \"2026-01-01\"\n"
            "review_status: draft\nconfidence_summary:\n  verified: 1\nsources: [\"FAKEMOD\"]\n---\n"
            f"\n# FAKEMOD\n\n{body}"
        )

    ok, attempts, in_tok, out_tok, problems, sections = batch_mod._generate_module_index_narrative(
        conn, "FAKEMOD", [(1, "## Purpose\n\nSomething [[FAKEMOD:1]].")], caller,
        "writing rules", None, out_path, assemble, max_attempts=2,
    )
    assert ok is True
    assert attempts == 2
    assert len(calls) == 2
    assert "missing" in calls[1].lower() or "Inputs" in calls[1]


def test_generate_module_index_narrative_fails_after_max_attempts(tmp_path):
    """A reconciliation response that never carries a real citation keeps
    failing validate_doc's own citation-resolution check -- ok=False after
    both attempts, problems non-empty."""
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    conn.commit()

    def caller(prompt):
        text = "\n\n".join(
            f"## {h}\n\nThe module does something with no citation at all."
            for h in batch_mod.NARRATIVE_SECTIONS
        )
        return batch_mod.ModelResponse(text=text, input_tokens=1, output_tokens=1)

    out_path = tmp_path / "FAKEMOD.md"

    def assemble(sections):
        body = "\n".join(f"## {h}\n\n{sections[h]}\n" for h in batch_mod.NARRATIVE_SECTIONS)
        return (
            "---\ntitle: \"FAKEMOD\"\ndoc_type: module_index\nsystem: MOM\n"
            "generated_by: legacy-functional-docs 0.1.0\ngenerated_at: \"2026-01-01\"\n"
            "review_status: draft\nconfidence_summary:\n  verified: 1\nsources: [\"FAKEMOD\"]\n---\n"
            f"\n# FAKEMOD\n\n{body}"
        )

    ok, attempts, in_tok, out_tok, problems, sections = batch_mod._generate_module_index_narrative(
        conn, "FAKEMOD", [(1, "## Purpose\n\nSomething [[FAKEMOD:1]].")], caller,
        "writing rules", None, out_path, assemble, max_attempts=2,
    )
    assert ok is False
    assert attempts == 2
    assert problems


def test_consolidated_gap_lines_keeps_distinct_gaps_with_identical_detail_text(tmp_path):
    """Several gap rows commonly share identical `detail` text (e.g. every
    unparsed_line gap for a member reads the same templated sentence,
    differing only by line_no) -- deduping on text alone would collapse
    them down to one and silently drop the rest. Dedup must be keyed on
    (gap_kind, line_no, text), not text alone."""
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    same_detail = "Statement not recognised by the Natural scanner in FAKEMOD."
    insert(conn, "gap", member_id=1, gap_kind="unparsed_line", severity="low",
           detail=same_detail, line_no=10)
    insert(conn, "gap", member_id=1, gap_kind="unparsed_line", severity="low",
           detail=same_detail, line_no=20)
    conn.commit()

    lines = batch_mod._consolidated_gap_lines(conn, "FAKEMOD", 1, [])
    assert len(lines) == 2, "both gap rows must survive dedup, not just one"
    assert any(":10]]" in line for line in lines)
    assert any(":20]]" in line for line in lines)


def test_generate_module_index_narrative_rejects_a_citation_not_in_any_excerpt(tmp_path):
    """A reconciled section citing a real, resolvable line that never
    appeared in any given chunk excerpt must be rejected -- validate_doc's
    citation-resolution check alone can't catch this (the citation *does*
    resolve), so this is the deterministic check that must."""
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 2, 'irrelevant')")
    conn.commit()

    def caller(prompt):
        # Cites [[FAKEMOD:2]] -- a real, resolvable line, but never given in
        # any chunk excerpt (only [[FAKEMOD:1]] was).
        return batch_mod.ModelResponse(
            text=_valid_narrative_response("[[FAKEMOD:2]]"), input_tokens=1, output_tokens=1,
        )

    out_path = tmp_path / "FAKEMOD.md"

    def assemble(sections):
        body = "\n".join(f"## {h}\n\n{sections[h]}\n" for h in batch_mod.NARRATIVE_SECTIONS)
        return (
            "---\ntitle: \"FAKEMOD\"\ndoc_type: module_index\nsystem: MOM\n"
            "generated_by: legacy-functional-docs 0.1.0\ngenerated_at: \"2026-01-01\"\n"
            "review_status: draft\nconfidence_summary:\n  verified: 1\nsources: [\"FAKEMOD\"]\n---\n"
            f"\n# FAKEMOD\n\n{body}"
        )

    ok, attempts, in_tok, out_tok, problems, sections = batch_mod._generate_module_index_narrative(
        conn, "FAKEMOD", [(1, "## Purpose\n\nSomething [[FAKEMOD:1]].")], caller,
        "writing rules", None, out_path, assemble, max_attempts=2,
    )
    assert ok is False
    assert sections is None
    assert any("not present in any given chunk excerpt" in p for p in problems)
