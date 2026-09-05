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


class FakeCaller:
    """Returns a valid doc for every prompt; records prompts it was called with."""

    def __init__(self, fail_first: bool = False):
        self.fail_first = fail_first
        self.calls = 0
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> batch_mod.ModelResponse:
        self.calls += 1
        self.prompts.append(prompt)
        # A retry prompt carries the "Previous attempt failed" section.
        is_retry = "Previous attempt failed validation" in prompt
        if self.fail_first and not is_retry:
            text = "not even front matter, this will fail validation\n"
        else:
            # Pull the member name back out of the brief's own front-matter-less
            # heading line ("# Fact brief: NAME") so the fake response is valid
            # for whichever member it was generated for.
            member = prompt.split("# Fact brief:")[1].splitlines()[0].strip()
            text = (
                GOOD_FRONTMATTER.format(member=member)
                + f"\n# {member}\n\nThis module does something [[{member}:1]].\n"
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
    real call. Mirrors test_test_batch.py's _chunk_aware_caller."""
    import re as _re

    ids_re = _re.compile(r"FAKEMOD:BR-\d+")

    def caller(prompt: str) -> batch_mod.ModelResponse:
        ids = sorted(set(ids_re.findall(prompt)))
        rule_lines = "\n".join(f"1. **{i}** [[FAKEMOD:1]] rule text." for i in ids)
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

## Purpose

Covers the module as a whole [[FAKEMOD:1]].

## Business rules

{rule_lines}
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
    assert "doc_type: module" in index_text
    assert "verified: 5" in index_text, "confidence_summary must aggregate all 5 chunked rules"
    for n in range(1, 6):
        assert f"FAKEMOD:BR-{n:03d}" in index_text

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

    def flaky_caller(prompt: str) -> batch_mod.ModelResponse:
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

    assert (tmp_path / "FAKEMOD.chunk1.md").exists()
    assert validate_doc(conn, tmp_path / "FAKEMOD.chunk1.md")["ok"]


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
