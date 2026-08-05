"""Guards on the batch narrative harness (Phase 3, option C).

Uses a fake caller (no network, no API key) that returns a canned,
well-formed document for anything except a deliberately-broken first draft,
so the retry-once-then-report path is exercised without depending on a real
model. Acceptance per the plan is "9 fixtures produce N valid documents
unattended, with a cost figure and a retry count reported" -- this repo's
fixture set has 4 batchable (natural/mantis program-level) members, not 9;
the other fixtures are data definitions and environment sources that
option C deliberately routes to the CLI path instead, not module docs.
"""

from __future__ import annotations

from mfdoc import batch as batch_mod
from mfdoc.redact import NULL_REDACTOR

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
    assert set(members) == {"MMP0100", "MMP0200", "MMP9000", "ORDENQ"}


def test_batch_generates_valid_docs_for_all_batchable_members(indexed_db, tmp_path):
    members = batch_mod.select_batch_members(indexed_db)
    caller = FakeCaller()
    writing_rules = "cite everything"
    template = "module template"
    summary = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, writing_rules, template,
        redact=NULL_REDACTOR, concurrency=2, state_path=None,
    )
    assert summary.ok == len(members) == 4
    assert summary.failed == 0
    assert summary.retried == 0
    assert summary.total_input_tokens == 400
    assert summary.total_output_tokens == 800
    for member in members:
        assert (tmp_path / "out" / f"{member}.md").exists()


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
    expected = (400 / 1_000_000) * 3.0 + (800 / 1_000_000) * 15.0
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
