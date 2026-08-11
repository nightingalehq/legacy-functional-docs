"""Guards on the batch narrative harness (Phase 3, option C).

Uses a fake caller (no network, no API key) that returns a canned,
well-formed document for anything except a deliberately-broken first draft,
so the retry-once-then-report path is exercised without depending on a real
model. Acceptance per the plan is "9 fixtures produce N valid documents
unattended, with a cost figure and a retry count reported" -- this repo's
fixture set has 12 batchable (natural/mantis program-level) members; the
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
        "MMP9600", "MMP9700", "MMC0100", "ORDENQ",
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
    assert summary.ok == len(members) == 12
    assert summary.failed == 0
    assert summary.retried == 0
    assert summary.total_input_tokens == 1200
    assert summary.total_output_tokens == 2400
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
    expected = (1200 / 1_000_000) * 3.0 + (2400 / 1_000_000) * 15.0
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
