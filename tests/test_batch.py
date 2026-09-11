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
import threading
from pathlib import Path

import pytest

from mfdoc import batch as batch_mod
from mfdoc import retry as retry_mod
from mfdoc.redact import NULL_REDACTOR, Redactor
from mfdoc.validate import CITATION


def _fake_clock(monkeypatch) -> None:
    """Monkeypatch batch_mod.time.perf_counter to a deterministic, always-
    advancing-by-0.1-seconds fake, so tests asserting on DocResult/
    BatchSummary duration_s (issue #84) don't depend on real wall-clock
    timing (flaky under CI load) or need real sleeps to produce a
    measurable, non-zero duration. Every _timed_call() does exactly one
    start-tick/end-tick pair around one model call with nothing else in
    this module reading the clock in between, so each timed model call
    reads as exactly 0.1s elapsed under this fake, regardless of how many
    calls a test's caller makes."""
    ticks = {"t": 0.0}

    def fake_perf_counter() -> float:
        ticks["t"] += 0.1
        return ticks["t"]

    monkeypatch.setattr(batch_mod.time, "perf_counter", fake_perf_counter)


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
    as the real feature is meant to, rather than inventing one. Matches via
    validate.CITATION itself (not a bespoke regex) so this fake can't fall
    back to a fabricated [[UNKNOWN:1]] for a real citation whose member name
    uses a character validate.py's own pattern allows but a narrower ad hoc
    regex here wouldn't (e.g. a leading `#`/`@`/`$`/`&`)."""
    m = CITATION.search(prompt)
    cite = f"[[{m.group('member')}:{m.group('from')}]]" if m and m.group("from") else "[[UNKNOWN:1]]"
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
        "MMP0100", "MMP0200", "MMP0400", "MMP9000", "MMP9100", "MMP9200", "MMP9300", "MMP9400",
        "MMP9500", "MMP9550", "MMP9560", "MMP9600", "MMP9700", "MMP9800", "MMC0100", "ORDENQ",
        "SCRNENT", "PRODSCHED",
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
    assert summary.ok == len(members) == 18
    assert summary.failed == 0
    assert summary.retried == 0
    assert summary.total_input_tokens == 1800
    assert summary.total_output_tokens == 3600
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


def test_strip_response_preamble_removes_wrapping_code_fence():
    """A ClaudeCLICaller response (issue #150) can come back as the whole
    document wrapped in a single ```markdown fence -- the fence itself,
    not just lead-in text, must be gone before split_frontmatter sees it,
    or the fence's own opening ``` line is what fails the leading-`---`
    check instead of the real front matter."""
    text = "---\ntitle: X\n---\nbody\n"
    fenced = f"```markdown\n{text}```\n"
    assert batch_mod._strip_response_preamble(fenced) == text


def test_strip_response_preamble_removes_leading_bare_fence_with_no_language():
    text = "---\ntitle: X\n---\nbody\n"
    fenced = f"```\n{text}```"
    assert batch_mod._strip_response_preamble(fenced) == text


def test_strip_response_preamble_removes_preamble_text_before_frontmatter():
    """Claude Code running headless (--provider claude-code) sometimes adds
    a line or two of its own commentary ahead of the document proper, even
    though `--tools ""` stops it editing files -- the prompt's own "start
    with the front matter, no preamble" instruction isn't always followed
    to the letter under that framing."""
    text = "---\ntitle: X\n---\nbody\n"
    preambled = f"Here is the requested document:\n\n{text}"
    assert batch_mod._strip_response_preamble(preambled) == text


def test_strip_response_preamble_is_a_no_op_when_already_clean():
    """The two bare-completion callers (AnthropicCaller/VertexCaller) already
    produce a response starting with `---` -- this must never touch that
    case, or a change here could introduce a regression for callers that
    never had the problem issue #150 describes."""
    text = "---\ntitle: X\n---\nbody\n"
    assert batch_mod._strip_response_preamble(text) == text


def test_strip_response_preamble_leaves_text_with_no_frontmatter_at_all_unchanged():
    """No `---` anywhere in the response at all (e.g. the model refused, or
    produced something completely unrelated) -- nothing to rescue, so the
    original text passes through untouched and split_frontmatter's own
    "missing YAML front matter" error still reports the real raw output,
    not something this function invented or truncated."""
    text = "I can't help with that.\n"
    assert batch_mod._strip_response_preamble(text) == text


def test_strip_response_preamble_does_not_treat_a_prompt_section_separator_as_frontmatter():
    """Reviewer-flagged false positive (PR #152): build_prompt joins its own
    sections with a bare "\\n\\n---\\n\\n" separator -- a fake-echo caller's
    response is literally its whole prompt echoed back, so that separator
    (followed by ordinary prose, not a YAML mapping) sits right near the
    start of the "response" text too. This must be left alone rather than
    mistaken for a real front-matter start and used to truncate everything
    before it."""
    text = (
        "# Fact brief: MMP0100\n\n"
        "some fact content here [[MMP0100:1]].\n\n"
        "---\n\n"
        "A test brief (`mfdoc test-plan`) already exists for this member.\n\n"
        "---\n\n"
        "Write the complete document, starting with the front matter.\n"
    )
    assert batch_mod._strip_response_preamble(text) == text


def test_strip_response_preamble_ignores_a_frontmatter_example_beyond_the_search_window():
    """A worked front-matter *example* quoted verbatim inside a prompt's own
    writing-rules/template text does parse as a real YAML mapping (unlike
    build_prompt's bare separators) -- but it's not the actual response,
    so it must not be mistaken for one just because it happens to appear
    somewhere in a long fake-echo "response". Bounding the search to near
    the start (where a genuine preamble always is) keeps this safe without
    needing to tell the two cases apart any other way."""
    filler = "x" * (batch_mod._PREAMBLE_SEARCH_WINDOW + 200)
    text = (
        f"# Instructions\n\n{filler}\n\n"
        '---\ntitle: "example"\ndoc_type: module\n---\nexample body\n'
    )
    assert batch_mod._strip_response_preamble(text) == text


def test_strip_response_preamble_removes_a_longer_stated_intent_preamble():
    """Regression for issue #197: a live `batch` run (via ClaudeCLICaller)
    leaked a multi-sentence stated-intent preamble ("I'll review ... then
    write ...") ahead of the front matter -- longer than the original
    300-char `_PREAMBLE_SEARCH_WINDOW`, so the real leading `---` fell
    outside the searched slice entirely and the preamble sailed through
    _strip_response_preamble untouched. This reproduces that shape (text
    invented, not the real observed wording) at a length past the old
    window but within the fixed one."""
    text = "---\ntitle: X\n---\nbody\n"
    preamble = (
        "I'll review the module's fact brief and existing rule candidates in "
        "detail, cross-checking each business rule against its cited source "
        "line, then write the corrected functional document, making sure "
        "every business rule carries its [[MEMBER:LINE]] citation back to "
        "source and the front matter fields match the template exactly, "
        "with no invented content anywhere.\n\n"
        "Here is the corrected document:\n\n"
    )
    assert len(preamble) > 300  # would have escaped the pre-#197 window
    preambled = preamble + text
    assert batch_mod._strip_response_preamble(preambled) == text


def test_written_doc_survives_a_wrapped_and_prefaced_claude_cli_response(indexed_db, tmp_path):
    """End-to-end: a caller (standing in for ClaudeCLICaller) that wraps its
    otherwise-valid response in a code fence with lead-in commentary must
    still produce a document that validates -- the normalization has to be
    wired into generate_module_doc's actual write site, not just exist as
    an unused helper."""

    class WrappingCaller(FakeCaller):
        def __call__(self, prompt: str) -> batch_mod.ModelResponse:
            response = super().__call__(prompt)
            wrapped = f"Sure, here's the document:\n\n```markdown\n{response.text}```\n"
            response.text = wrapped
            return response

    out_path = tmp_path / "MMP0100.md"
    result = batch_mod.generate_module_doc(
        indexed_db, "MMP0100", out_path, WrappingCaller(), "cite everything", "module template",
    )
    assert result.ok, result.problems
    text = out_path.read_text(encoding="utf-8")
    assert text.startswith("---")


def test_fix_generated_by_version_still_strips_preamble_when_generated_by_line_is_missing():
    """_fix_generated_by_version is not a full no-op just because there's no
    generated_by: line to correct -- it always runs _strip_response_preamble
    first (that's how every response.text write site gets the issue #150
    fix for free), so a preamble/fence ahead of a document with no
    generated_by: line at all still gets cleaned up."""
    text = "---\ntitle: X\n---\nbody\n"
    preambled = f"Here is the requested document:\n\n{text}"
    assert batch_mod._fix_generated_by_version(preambled) == text


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
    expected = (1800 / 1_000_000) * 3.0 + (3600 / 1_000_000) * 15.0
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


def test_batch_reports_two_attempts_when_the_retry_call_itself_raises(indexed_db, tmp_path):
    """A caller exception on the *retry* call (not the first) means two
    calls were actually attempted -- the first (which produced a document
    that then failed validation) and the retry itself (which raised) --
    so attempts must be 2, matching the normal successful-retry path's
    accounting, not 1 as if only the first call had ever happened. See
    issue #78 review."""

    class RaisesOnRetry:
        def __init__(self):
            self.calls = 0

        def __call__(self, prompt: str) -> batch_mod.ModelResponse:
            self.calls += 1
            if "Previous attempt failed validation" in prompt:
                raise RuntimeError("simulated transient error on retry")
            return batch_mod.ModelResponse(
                text="not even front matter, this will fail validation\n",
                input_tokens=1, output_tokens=1,
            )

    caller = RaisesOnRetry()
    state_path = tmp_path / "state.json"
    summary = batch_mod.run_batch(
        indexed_db, ["MMP0100"], tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )
    assert summary.failed == 1
    result = summary.results[0]
    assert result.attempts == 2
    assert caller.calls == 2
    assert any("retry model call failed" in p for p in result.problems)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["natural/MILLPROD/MMP0100"]["attempts"] == 2


def test_generate_module_doc_from_brief_tracks_duration_and_retries_across_attempts(monkeypatch, indexed_db, tmp_path):
    """DocResult.duration_s sums each attempt's own call time and
    DocResult.retries sums each response's own ModelResponse.retries
    (issue #79's transient-retry count) -- both across every model call
    this one member's generation made, not just the last attempt. Uses a
    first-attempt-fails/second-attempt-succeeds member so both the
    duration and retries accumulation (not just a single-call passthrough)
    is actually exercised."""
    _fake_clock(monkeypatch)
    calls = {"n": 0}

    def caller(prompt: str) -> batch_mod.ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            # Fails validation (no front matter) -- forces a retry -- but
            # still simulates having needed 2 transient retries to get even
            # this far, so `retries` must count it despite the eventual
            # validation failure.
            return batch_mod.ModelResponse(text="not even front matter", input_tokens=1, output_tokens=1, retries=2)
        text = GOOD_FRONTMATTER.format(member="MMP0100") + "\n# MMP0100\n\nDoes something [[MMP0100:1]].\n"
        return batch_mod.ModelResponse(text=text, input_tokens=1, output_tokens=1, retries=1)

    out_path = tmp_path / "MMP0100.md"
    brief = "# Fact brief: MMP0100\n\nSome brief text [[MMP0100:1]].\n"
    result = batch_mod._generate_module_doc_from_brief(
        indexed_db, "MMP0100", brief, out_path, caller, "cite everything", "module template",
    )
    assert result.ok, result.problems
    assert result.attempts == 2
    assert result.retries == 3  # 2 (attempt 1) + 1 (attempt 2)
    assert result.duration_s == pytest.approx(0.2)  # 0.1s/call (fake clock) * 2 calls


def test_is_near_miss_uncited_boundary():
    """`_is_near_miss` (issue #131, generalized by #170) is true only when
    validation failed for reasons that are entirely sentence-localized --
    here, a small number (<= NEAR_MISS_MAX_LOCALIZED) of uncited-and-
    unhedged assertive statements -- and false the moment either bound is
    crossed: too many uncited sentences, or any other (non-localized)
    problem alongside them (here, an invalid citation)."""
    at_limit = ["x"] * batch_mod.NEAR_MISS_MAX_LOCALIZED
    within_bound = {
        "ok": False,
        "problems": [f"{len(at_limit)} assertive statement(s) carry no citation and no hedge"],
        "uncited_assertions": at_limit,
    }
    assert batch_mod._is_near_miss(within_bound)

    over_limit = ["x"] * (batch_mod.NEAR_MISS_MAX_LOCALIZED + 1)
    too_many = {
        "ok": False,
        "problems": [f"{len(over_limit)} assertive statement(s) carry no citation and no hedge"],
        "uncited_assertions": over_limit,
    }
    assert not batch_mod._is_near_miss(too_many)

    mixed_with_other_problem = {
        "ok": False,
        "problems": [
            "invalid citation [[X:1]]: member 'X' is not in the index",
            "1 assertive statement(s) carry no citation and no hedge",
        ],
        "uncited_assertions": ["a"],
    }
    assert not batch_mod._is_near_miss(mixed_with_other_problem)


def test_is_near_miss_reversed_condition_only():
    """Issue #170: a validation failure whose only problem(s) are
    reversed-condition flags (each naming a specific `[[MEMBER:LINE]]`
    citation) is a near-miss too, not just the uncited-assertion case --
    it's exactly as sentence-localized, just from a different check."""
    one_reversed = {
        "ok": False,
        "problems": [
            "comparison direction may be reversed near [[MMP0100:5]]: text reads "
            "as though STATUS-FLAG equals 'OK', but the source condition means "
            "STATUS-FLAG does not equal 'OK'"
        ],
        "uncited_assertions": [],
    }
    assert batch_mod._is_near_miss(one_reversed)

    too_many_reversed = {
        "ok": False,
        "problems": [
            f"comparison direction may be reversed near [[MMP0100:{n}]]: "
            "text reads as though X equals 'Y', but the source condition "
            "means X does not equal 'Y'"
            for n in range(batch_mod.NEAR_MISS_MAX_LOCALIZED + 1)
        ],
        "uncited_assertions": [],
    }
    assert not batch_mod._is_near_miss(too_many_reversed)


def test_is_near_miss_structural_failure_excluded():
    """A structural/whole-document failure (missing front matter) is never
    a near-miss, even alongside a localized finding -- it isn't tied to one
    sentence a targeted patch could fix, so the chunk must still fall
    through to a full retry."""
    missing_front_matter = {
        "ok": False,
        "problems": [
            "front matter missing required key: doc_type",
            "1 assertive statement(s) carry no citation and no hedge",
        ],
        "uncited_assertions": ["a"],
    }
    assert not batch_mod._is_near_miss(missing_front_matter)


def test_near_miss_uncited_assertion_gets_a_targeted_patch_not_a_full_retry(indexed_db, tmp_path):
    """Issue #131: a chunk whose only validation problem is a single
    near-miss uncited assertive statement gets a cheap targeted-patch
    follow-up call (build_localized_patch_prompt) instead of a full chunk
    regeneration -- the second call must carry the flagged-sentence prompt,
    not the writing rules/template/"Previous attempt failed" full-retry
    prompt, and must not consume one of the full-regeneration attempts."""
    calls = {"n": 0}
    prompts: list[str] = []

    def caller(prompt: str) -> batch_mod.ModelResponse:
        calls["n"] += 1
        prompts.append(prompt)
        if calls["n"] == 1:
            return batch_mod.ModelResponse(
                text=(
                    GOOD_FRONTMATTER.format(member="MMP0100")
                    + "\n# MMP0100\n\nDoes something [[MMP0100:1]]. "
                    "The system also validates the account balance before posting.\n"
                ),
                input_tokens=10, output_tokens=20,
            )
        # The targeted patch attempt: fix the flagged sentence with a citation.
        return batch_mod.ModelResponse(
            text=(
                GOOD_FRONTMATTER.format(member="MMP0100")
                + "\n# MMP0100\n\nDoes something [[MMP0100:1]]. "
                "The system also validates the account balance before posting [[MMP0100:1]].\n"
            ),
            input_tokens=5, output_tokens=8,
        )

    out_path = tmp_path / "MMP0100.md"
    brief = "# Fact brief: MMP0100\n\nSome brief text [[MMP0100:1]].\n"
    result = batch_mod._generate_module_doc_from_brief(
        indexed_db, "MMP0100", brief, out_path, caller, "cite everything", "module template",
    )
    assert result.ok, result.problems
    assert calls["n"] == 2
    assert result.attempts == 1  # the patch call doesn't count as a full-retry attempt
    patch_prompt = prompts[1]
    assert "Flagged findings (locate the matching sentence; fix only these)" in patch_prompt
    assert "Uncited assertive statements" in patch_prompt
    assert "truncated to 140 characters" in patch_prompt
    assert "Previous attempt failed validation" not in patch_prompt
    assert "cite everything" not in patch_prompt  # writing rules not resent
    assert "module template" not in patch_prompt  # template not resent


def test_near_miss_patch_failure_falls_back_to_full_chunk_retry(indexed_db, tmp_path):
    """When the targeted patch attempt itself doesn't resolve validation,
    generation must still fall back to the existing full-retry path (with
    its normal max_attempts budget) rather than giving up."""
    calls = {"n": 0}
    prompts: list[str] = []
    near_miss_text = (
        GOOD_FRONTMATTER.format(member="MMP0100")
        + "\n# MMP0100\n\nDoes something [[MMP0100:1]]. "
        "The system also validates the account balance before posting.\n"
    )

    def caller(prompt: str) -> batch_mod.ModelResponse:
        calls["n"] += 1
        prompts.append(prompt)
        if calls["n"] == 1:
            return batch_mod.ModelResponse(text=near_miss_text, input_tokens=10, output_tokens=20)
        if calls["n"] == 2:
            # Patch attempt: model fails to actually fix it.
            return batch_mod.ModelResponse(text=near_miss_text, input_tokens=5, output_tokens=8)
        # Full retry: succeeds.
        return batch_mod.ModelResponse(
            text=(
                GOOD_FRONTMATTER.format(member="MMP0100")
                + "\n# MMP0100\n\nDoes something [[MMP0100:1]]. "
                "The system also validates the account balance before posting [[MMP0100:1]].\n"
            ),
            input_tokens=1, output_tokens=1,
        )

    out_path = tmp_path / "MMP0100.md"
    brief = "# Fact brief: MMP0100\n\nSome brief text [[MMP0100:1]].\n"
    result = batch_mod._generate_module_doc_from_brief(
        indexed_db, "MMP0100", brief, out_path, caller, "cite everything", "module template",
        max_attempts=2,
    )
    assert result.ok, result.problems
    assert calls["n"] == 3
    assert result.attempts == 2
    assert "Previous attempt failed validation" in prompts[2]


def test_multiple_uncited_assertions_still_trigger_a_full_retry(indexed_db, tmp_path):
    """A genuinely broken response -- more uncited assertions than
    NEAR_MISS_MAX_LOCALIZED allows -- must skip the targeted-patch path
    entirely and go straight to the existing full-chunk retry, unchanged
    from before issue #131's fix."""
    uncited_sentences = "".join(
        f"The system performs step {i}. " for i in range(batch_mod.NEAR_MISS_MAX_LOCALIZED + 1)
    )
    broken_text = (
        GOOD_FRONTMATTER.format(member="MMP0100")
        + "\n# MMP0100\n\n" + uncited_sentences.strip() + "\n"
    )
    calls = {"n": 0}
    prompts: list[str] = []

    def caller(prompt: str) -> batch_mod.ModelResponse:
        calls["n"] += 1
        prompts.append(prompt)
        if calls["n"] == 1:
            return batch_mod.ModelResponse(text=broken_text, input_tokens=10, output_tokens=20)
        return batch_mod.ModelResponse(
            text=(
                GOOD_FRONTMATTER.format(member="MMP0100")
                + "\n# MMP0100\n\nDoes something [[MMP0100:1]].\n"
            ),
            input_tokens=1, output_tokens=1,
        )

    out_path = tmp_path / "MMP0100.md"
    brief = "# Fact brief: MMP0100\n\nSome brief text [[MMP0100:1]].\n"
    result = batch_mod._generate_module_doc_from_brief(
        indexed_db, "MMP0100", brief, out_path, caller, "cite everything", "module template",
    )
    assert result.ok, result.problems
    assert calls["n"] == 2
    assert result.attempts == 2
    assert "Previous attempt failed validation" in prompts[1]
    assert "Flagged findings (locate the matching sentence; fix only these)" not in prompts[1]


def test_reversed_condition_only_gets_a_targeted_patch_not_a_full_retry(indexed_db, tmp_path):
    """Issue #170: a chunk whose only validation problem is a single
    near-miss reversed-condition flag gets the same cheap targeted-patch
    treatment as the uncited-assertion case -- a full chunk regeneration
    must not be triggered just because the localized failure came from a
    different check.

    The repo's own MMP0100 fixture (examples/inputs/natural/MMP0100.nsp)
    has `IF ORDER-VIEW.ORDER-STATUS NE 'CONF'` at line 38 -- ORDER-STATUS
    matches the outcome-field pattern (`\\bSTATUS\\b`), so a sentence
    narrating this as the status *equalling* 'CONF' describes the opposite
    polarity from the source condition, which is exactly the shape
    `_reversed_condition_problems` flags."""
    calls = {"n": 0}
    prompts: list[str] = []

    reversed_text = (
        GOOD_FRONTMATTER.format(member="MMP0100")
        + "\n# MMP0100\n\nThe module rejects the order when the order status "
        "equals 'CONF' [[MMP0100:38]].\n"
    )
    fixed_text = (
        GOOD_FRONTMATTER.format(member="MMP0100")
        + "\n# MMP0100\n\nThe module rejects the order when the order status "
        "is not 'CONF' [[MMP0100:38]].\n"
    )

    def caller(prompt: str) -> batch_mod.ModelResponse:
        calls["n"] += 1
        prompts.append(prompt)
        if calls["n"] == 1:
            return batch_mod.ModelResponse(text=reversed_text, input_tokens=10, output_tokens=20)
        return batch_mod.ModelResponse(text=fixed_text, input_tokens=5, output_tokens=8)

    out_path = tmp_path / "MMP0100.md"
    brief = "# Fact brief: MMP0100\n\nSome brief text [[MMP0100:38]].\n"
    result = batch_mod._generate_module_doc_from_brief(
        indexed_db, "MMP0100", brief, out_path, caller, "cite everything", "module template",
    )
    assert result.ok, result.problems
    assert calls["n"] == 2
    assert result.attempts == 1  # the patch call doesn't count as a full-retry attempt
    patch_prompt = prompts[1]
    assert "Flagged findings (locate the matching sentence; fix only these)" in patch_prompt
    assert "Comparison direction may be reversed" in patch_prompt
    assert "[[MMP0100:38]]" in patch_prompt
    assert "Previous attempt failed validation" not in patch_prompt
    assert "cite everything" not in patch_prompt  # writing rules not resent
    assert "module template" not in patch_prompt  # template not resent


def test_find_confident_citation_clear_match():
    """A sentence whose key tokens (backtick/quote-delimited literals and
    field names) are all present on exactly one already-cited brief line is
    a confident match."""
    brief_lines = [("[[MMP0100:38]]", "- [[MMP0100:38]] when `ORDER-STATUS` equals `'CONF'` the order is rejected")]
    sentence = "The module rejects the order when `ORDER-STATUS` equals `'CONF'`."
    assert batch_mod._find_confident_citation(sentence, brief_lines) == "[[MMP0100:38]]"


def test_find_confident_citation_no_tokens_declines():
    """A sentence with no backtick/quote-delimited tokens at all has
    nothing specific to match on -- must decline rather than guess from
    plain-word overlap."""
    brief_lines = [("[[MMP0100:38]]", "- [[MMP0100:38]] the system validates the account balance before posting")]
    sentence = "The system also validates the account balance before posting."
    assert batch_mod._find_confident_citation(sentence, brief_lines) is None


def test_find_confident_citation_ambiguous_declines():
    """Two brief lines that each carry only part of the sentence's key
    tokens -- neither is a superset -- so there is no unambiguous match."""
    brief_lines = [
        ("[[MMP0100:38]]", "- [[MMP0100:38]] `ORDER-STATUS` is the transaction outcome field"),
        ("[[MMP0100:40]]", "- [[MMP0100:40]] a rejected order is one whose status equals `'CONF'`"),
    ]
    sentence = "The module rejects the order when `ORDER-STATUS` equals `'CONF'`."
    assert batch_mod._find_confident_citation(sentence, brief_lines) is None


def test_splice_citation_wrapped_sentence_declines():
    """A sentence that doesn't appear verbatim in the document text (e.g.
    because it was wrapped across source lines, so the joined single-line
    form `_logical_units` produced isn't a literal substring) must not be
    guessed at -- `_splice_citation` returns `None`."""
    current_text = "Some text.\n\nThe module rejects the order\nwhen ORDER-STATUS equals 'CONF'.\n"
    sentence = "The module rejects the order when ORDER-STATUS equals 'CONF'."
    assert batch_mod._splice_citation(current_text, sentence, "[[MMP0100:38]]") is None


def test_near_miss_uncited_assertion_auto_cited_with_zero_model_calls(indexed_db, tmp_path):
    """Issue #171: when the flagged uncited sentence is a close paraphrase
    of a fact the same brief already states with a citation elsewhere --
    same field names, just missing the `[[MEMBER:LINE]]` tag -- the
    deterministic auto-citation pass must splice that citation in directly
    and re-validate, with no second (patch) model call at all.

    Uses `GRADE-CODE`/`STOCK-VIEW.GRADE-CODE` (line 44 of the repo's own
    MMP0100 fixture: `IF STOCK-VIEW.GRADE-CODE NE ORDER-VIEW.GRADE-CODE`) --
    deliberately not an outcome field (`ORDER-STATUS`, `RETURN-CODE`, ...),
    so `_reversed_condition_problems` never fires here and this test stays
    isolated to the uncited-assertion path issue #171 targets."""
    calls = {"n": 0}

    def caller(prompt: str) -> batch_mod.ModelResponse:
        calls["n"] += 1
        return batch_mod.ModelResponse(
            text=(
                GOOD_FRONTMATTER.format(member="MMP0100")
                + "\n# MMP0100\n\nDoes something [[MMP0100:1]]. "
                "The module skips the row when `GRADE-CODE` does not equal "
                "`STOCK-VIEW.GRADE-CODE`.\n"
            ),
            input_tokens=10, output_tokens=20,
        )

    out_path = tmp_path / "MMP0100.md"
    brief = (
        "# Fact brief: MMP0100\n\n"
        "- [[MMP0100:44]] when `GRADE-CODE` does not equal `STOCK-VIEW.GRADE-CODE` "
        "the row is skipped\n"
    )
    result = batch_mod._generate_module_doc_from_brief(
        indexed_db, "MMP0100", brief, out_path, caller, "cite everything", "module template",
    )
    assert result.ok, result.problems
    assert calls["n"] == 1  # no second (patch) model call needed
    assert result.attempts == 1
    written = out_path.read_text(encoding="utf-8")
    assert "`STOCK-VIEW.GRADE-CODE` [[MMP0100:44]]." in written


def test_near_miss_uncited_assertion_no_confident_match_falls_back_to_patch(indexed_db, tmp_path):
    """Negative case for issue #171: when the flagged sentence's key tokens
    aren't an unambiguous subset of any single already-cited brief line --
    here, two different brief lines each carry only one of the sentence's
    two tokens -- the deterministic pass must find no confident match and
    fall back to the existing model-patch path unchanged, rather than
    guessing which citation to attach."""
    calls = {"n": 0}
    prompts: list[str] = []

    def caller(prompt: str) -> batch_mod.ModelResponse:
        calls["n"] += 1
        prompts.append(prompt)
        if calls["n"] == 1:
            return batch_mod.ModelResponse(
                text=(
                    GOOD_FRONTMATTER.format(member="MMP0100")
                    + "\n# MMP0100\n\nDoes something [[MMP0100:1]]. "
                    "The module skips the row when `GRADE-CODE` does not equal "
                    "`STOCK-VIEW.GRADE-CODE`.\n"
                ),
                input_tokens=10, output_tokens=20,
            )
        return batch_mod.ModelResponse(
            text=(
                GOOD_FRONTMATTER.format(member="MMP0100")
                + "\n# MMP0100\n\nDoes something [[MMP0100:1]]. "
                "The module skips the row when `GRADE-CODE` does not equal "
                "`STOCK-VIEW.GRADE-CODE` [[MMP0100:44]].\n"
            ),
            input_tokens=5, output_tokens=8,
        )

    out_path = tmp_path / "MMP0100.md"
    # Neither brief line alone carries both of the sentence's key tokens,
    # so no single line's tokens are a superset of the sentence's -- the
    # deterministic pass must decline rather than pick either one.
    brief = (
        "# Fact brief: MMP0100\n\n"
        "- [[MMP0100:44]] `GRADE-CODE` is the grade key field on the order\n"
        "- [[MMP0100:45]] a skipped row is one whose grade differs from "
        "`STOCK-VIEW.GRADE-CODE`\n"
    )
    result = batch_mod._generate_module_doc_from_brief(
        indexed_db, "MMP0100", brief, out_path, caller, "cite everything", "module template",
    )
    assert result.ok, result.problems
    assert calls["n"] == 2  # deterministic pass declined; model patch still ran
    assert result.attempts == 1
    patch_prompt = prompts[1]
    assert "Flagged findings (locate the matching sentence; fix only these)" in patch_prompt
    assert "Uncited assertive statements" in patch_prompt


def test_reversed_condition_near_miss_unaffected_by_auto_citation(indexed_db, tmp_path):
    """Issue #171 is uncited-assertion-only: a near-miss made up entirely of
    reversed-condition findings (no uncited assertions at all) must never
    invoke the auto-citation pass, confirmed here by monkeypatching it to
    raise if called -- the reversed-condition path must reach the model
    patch exactly as it did before this issue."""
    def _boom(*a, **k):
        raise AssertionError("_auto_cite_uncited_assertions must not run for a reversed-"
                              "condition-only near-miss")
    orig = batch_mod._auto_cite_uncited_assertions
    batch_mod._auto_cite_uncited_assertions = _boom
    try:
        calls = {"n": 0}
        reversed_text = (
            GOOD_FRONTMATTER.format(member="MMP0100")
            + "\n# MMP0100\n\nThe module rejects the order when the order status "
            "equals 'CONF' [[MMP0100:38]].\n"
        )
        fixed_text = (
            GOOD_FRONTMATTER.format(member="MMP0100")
            + "\n# MMP0100\n\nThe module rejects the order when the order status "
            "is not 'CONF' [[MMP0100:38]].\n"
        )

        def caller(prompt: str) -> batch_mod.ModelResponse:
            calls["n"] += 1
            if calls["n"] == 1:
                return batch_mod.ModelResponse(text=reversed_text, input_tokens=10, output_tokens=20)
            return batch_mod.ModelResponse(text=fixed_text, input_tokens=5, output_tokens=8)

        out_path = tmp_path / "MMP0100.md"
        brief = "# Fact brief: MMP0100\n\nSome brief text [[MMP0100:38]].\n"
        result = batch_mod._generate_module_doc_from_brief(
            indexed_db, "MMP0100", brief, out_path, caller, "cite everything", "module template",
        )
        assert result.ok, result.problems
        assert calls["n"] == 2
    finally:
        batch_mod._auto_cite_uncited_assertions = orig


def test_missing_front_matter_still_gets_a_full_retry(indexed_db, tmp_path):
    """Negative case for issue #170: a structural failure (missing/broken
    front matter entirely) is never sentence-localized, so it must keep
    falling straight through to the existing full-chunk retry path,
    unchanged, even though it's the same shape of "small number of
    problems" a localized near-miss might have."""
    calls = {"n": 0}
    prompts: list[str] = []

    def caller(prompt: str) -> batch_mod.ModelResponse:
        calls["n"] += 1
        prompts.append(prompt)
        if calls["n"] == 1:
            return batch_mod.ModelResponse(text="not even front matter", input_tokens=10, output_tokens=20)
        text = GOOD_FRONTMATTER.format(member="MMP0100") + "\n# MMP0100\n\nDoes something [[MMP0100:1]].\n"
        return batch_mod.ModelResponse(text=text, input_tokens=1, output_tokens=1)

    out_path = tmp_path / "MMP0100.md"
    brief = "# Fact brief: MMP0100\n\nSome brief text [[MMP0100:1]].\n"
    result = batch_mod._generate_module_doc_from_brief(
        indexed_db, "MMP0100", brief, out_path, caller, "cite everything", "module template",
    )
    assert result.ok, result.problems
    assert calls["n"] == 2
    assert result.attempts == 2
    assert "Previous attempt failed validation" in prompts[1]
    assert "Flagged findings (locate the matching sentence; fix only these)" not in prompts[1]


def test_run_batch_tracks_duration_and_retries_for_a_mixed_run(monkeypatch, indexed_db, tmp_path):
    """End-to-end through run_batch(): one member succeeds first try (with
    transient retries along the way), one raises on its only call, and one
    needs (and gets) a validation retry -- BatchSummary.total_duration_s/
    total_retries must sum every member's own DocResult.duration_s/retries,
    and each DocResult itself must carry its own correct per-member figures,
    not just an aggregate total.

    Concurrency is pinned to 1, and the one member needing a second
    (validation-retry) call is placed last: run_batch's pool dispatches the
    *next* queued member's call on the worker thread independently of the
    main thread's per-future retry-call handling, so a retry call for a
    member with more members still queued behind it could race the fake
    clock's shared tick counter against the next member's own call. Putting
    the only retrying member last means nothing is left queued by the time
    its retry runs, so this holds deterministically rather than relying on
    real timing to avoid the race."""
    _fake_clock(monkeypatch)

    def caller(prompt: str) -> batch_mod.ModelResponse:
        if "MMP0100" in prompt:
            # First-try success, but simulates 3 transient retries en route.
            member = "MMP0100"
            text = GOOD_FRONTMATTER.format(member=member) + f"\n# {member}\n\nDoes something [[{member}:1]].\n"
            return batch_mod.ModelResponse(text=text, input_tokens=1, output_tokens=1, retries=3)
        if "MMP9000" in prompt:
            raise RuntimeError("simulated transient error, retries already exhausted inside the caller")
        # MMP0200: fails validation on the first call, succeeds on the retry.
        is_retry = "Previous attempt failed validation" in prompt
        if not is_retry:
            return batch_mod.ModelResponse(text="not even front matter", input_tokens=1, output_tokens=1)
        member = "MMP0200"
        text = GOOD_FRONTMATTER.format(member=member) + f"\n# {member}\n\nDoes something [[{member}:1]].\n"
        return batch_mod.ModelResponse(text=text, input_tokens=1, output_tokens=1, retries=1)

    summary = batch_mod.run_batch(
        indexed_db, ["MMP0100", "MMP9000", "MMP0200"], tmp_path / "out", caller,
        "rules", "template", concurrency=1,
    )
    by_member = {r.member: r for r in summary.results}

    first_try = by_member["MMP0100"]
    assert first_try.ok and first_try.attempts == 1
    assert first_try.retries == 3
    assert first_try.duration_s == pytest.approx(0.1)

    failed = by_member["MMP9000"]
    assert not failed.ok and failed.attempts == 1
    assert failed.retries == 0
    assert failed.duration_s == 0.0  # no timing to report for a call that raised

    retried = by_member["MMP0200"]
    assert retried.ok and retried.attempts == 2
    assert retried.retries == 1  # only the (successful) retry call reported transient retries
    assert retried.duration_s == pytest.approx(0.2)

    assert summary.total_retries == 3 + 0 + 1
    assert summary.total_duration_s == pytest.approx(0.1 + 0.0 + 0.2)


def test_generate_module_doc_chunked_aggregates_duration_and_retries_across_chunks_and_narrative(monkeypatch, tmp_path):
    """A chunked member's DocResult.duration_s/retries must fold in every
    chunk's own call plus the one whole-module narrative-reconciliation
    call -- not just the last chunk, and not omitting the narrative call
    that only runs once every chunk validated ok."""
    import sqlite3
    from mfdoc.db import SCHEMA

    _fake_clock(monkeypatch)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)  # -> 3 chunks with max_rules_per_call=2

    good_caller = _chunk_aware_module_caller()

    def caller(prompt: str) -> batch_mod.ModelResponse:
        response = good_caller(prompt)
        # Attribute one transient retry to each chunk call (never to the
        # narrative-reconciliation call, distinguished the same way the
        # rest of this module tells them apart) so the aggregate is
        # unambiguous: 3 chunks * 1 retry each = 3, narrative contributes 0.
        if "# Fact brief:" in prompt:
            response.retries = 1
        return response

    out_path = tmp_path / "FAKEMOD.md"
    result = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, caller, "writing rules text", "template text", max_rules_per_call=2,
    )
    assert result.ok, result.problems
    assert result.chunked is True
    assert result.retries == 3  # one per chunk call, none from the narrative call
    assert result.duration_s == pytest.approx(0.4)  # 4 timed calls (3 chunks + 1 narrative) * 0.1s


class FlakyCaller:
    """Wraps a FakeCaller, raising a transient-looking exception for every
    prompt whose brief is for one of `fail_for_members`, on their first call
    only -- simulates a real network blip/rate-limit hitting exactly one
    member mid-batch, the failure mode #78/#79 exist to make survivable.
    Every other member (and a previously-failed member's later retry-run
    call) goes through to the wrapped FakeCaller normally."""

    def __init__(self, fail_for_members: set[str]):
        self._inner = FakeCaller()
        self._fail_for_members = set(fail_for_members)
        self._failed_once: set[str] = set()
        self._lock = threading.Lock()
        self.calls = 0

    def __call__(self, prompt: str) -> "batch_mod.ModelResponse":
        with self._lock:
            self.calls += 1
            member = None
            if "# Fact brief:" in prompt:
                member = prompt.split("# Fact brief:")[1].splitlines()[0].strip()
            should_fail = (
                member in self._fail_for_members and member not in self._failed_once
            )
            if should_fail:
                self._failed_once.add(member)
        if should_fail:
            raise RuntimeError(f"simulated transient error for {member}")
        return self._inner(prompt)


def test_save_state_never_leaves_a_truncated_file_on_a_mid_write_crash(tmp_path):
    """_save_state must write atomically: a failed replacement must never
    leave a truncated/corrupt JSON file in place of the last good checkpoint
    -- see issue #78 review. The atomic rename is made to raise, then the
    previous good state file is asserted to remain intact and valid JSON."""
    state_path = tmp_path / "state.json"
    batch_mod._save_state(state_path, {"first": "good state"})
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"first": "good state"}

    import os as os_mod

    real_replace = os_mod.replace

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash before the atomic rename completes")

    os_mod.replace = boom
    try:
        with pytest.raises(RuntimeError):
            batch_mod._save_state(state_path, {"second": "state that must not land"})
    finally:
        os_mod.replace = real_replace

    # The original good checkpoint must be untouched -- no partial write
    # ever replaced it, and the failed attempt's temp file must not be left
    # behind either.
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"first": "good state"}
    leftover_tmp_files = [p for p in tmp_path.iterdir() if p.name != "state.json"]
    assert leftover_tmp_files == [], f"temp file(s) leaked: {leftover_tmp_files}"


def test_corpus_sha256_is_checkpointed_before_the_run_finishes(indexed_db, tmp_path):
    """`_corpus_sha256` must be written into the persisted state on the
    *first* incremental checkpoint, not only once at the very end of
    run_batch -- otherwise a process killed partway through a run leaves a
    resumed run unable to take the corpus-level `corpus_unchanged`
    fast-path, even though every member processed before the kill did
    complete and checkpoint successfully. See issue #78 review.

    Simulated by having the second member's call raise `KeyboardInterrupt`
    (a BaseException, not caught by run_batch's per-future `except
    Exception` isolation) so run_batch itself aborts partway through --
    proving the state on disk already reflects the corpus signature from
    the first member's checkpoint, not just a final save that never ran."""
    members = ["MMP0100", "MMP0200"]
    state_path = tmp_path / "state.json"
    inner = FakeCaller()

    def kill_on_second_member(prompt: str) -> "batch_mod.ModelResponse":
        if "# Fact brief:" in prompt:
            member = prompt.split("# Fact brief:")[1].splitlines()[0].strip()
            if member == "MMP0200":
                raise KeyboardInterrupt("simulated process kill")
        return inner(prompt)

    with pytest.raises(KeyboardInterrupt):
        batch_mod.run_batch(
            indexed_db, members, tmp_path / "out", kill_on_second_member, "rules", "template",
            state_path=state_path, concurrency=1,
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "_corpus_sha256" in state, (
        "corpus signature must already be checkpointed from the first "
        "member's save, not deferred to a final save that never happened"
    )
    assert state["natural/MILLPROD/MMP0100"]["ok"] is True


def test_batch_isolates_a_single_member_caller_failure_and_checkpoints_the_rest(indexed_db, tmp_path):
    """A caller exception for one member (ThreadPoolExecutor's fut.result())
    must not crash the whole run or lose the other members' already-
    completed state -- see issue #78. The failed member is reported as
    failed, not silently dropped, and a re-run only needs to redo it."""
    members = ["MMP0100", "MMP0200", "MMC0100"]
    state_path = tmp_path / "state.json"
    caller = FlakyCaller(fail_for_members={"MMP0200"})

    summary = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )

    assert summary.ok == 2
    assert summary.failed == 1
    failed = [r for r in summary.results if not r.ok]
    assert [r.member for r in failed] == ["MMP0200"]
    assert any("simulated transient error" in p for p in failed[0].problems)

    # The two members the caller never raised for must have their success
    # checkpointed to state -- not lost because the run as a whole also
    # contained a failure.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["natural/MILLPROD/MMP0100"]["ok"] is True
    assert state["natural/MILLPROD/MMC0100"]["ok"] is True
    assert state["natural/MILLPROD/MMP0200"]["ok"] is False

    # A re-run must only redo the failed member: the two ok members are
    # skipped (their briefs are unchanged), and MMP0200 -- which no longer
    # raises -- succeeds without needing to touch the others again.
    calls_before_rerun = caller.calls
    second = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )
    assert second.ok == 3
    assert second.failed == 0
    assert second.skipped == 2
    # Only MMP0200's own prompt (one fresh attempt) should have gone through
    # the caller on the re-run -- the other two members made no new calls.
    assert caller.calls == calls_before_rerun + 1


class _SimulatedTransientAPIError(Exception):
    """The specific transient exception RetryMaskedCaller raises on a
    member's first underlying attempt, and the *only* type its internal
    retry catches -- a distinct class (not bare Exception/RuntimeError) so
    the catch below is provably narrow, mirroring call_with_retry's own
    catch of a specific transient-error type rather than everything."""


class RetryMaskedCaller:
    """Simulates what AnthropicCaller/VertexCaller do internally via #79's
    retry/backoff implementation: a transient error on the underlying API
    call is caught and retried *inside the caller itself*, via the real
    `mfdoc.retry.call_with_retry` helper (not a hand-rolled re-implementation
    -- so this test can't drift from the production retry helper's actual
    behavior), so run_batch's ThreadPoolExecutor loop never sees an
    exception for that member at all -- unlike FlakyCaller above, which
    raises *out* to run_batch and relies on #78's per-future isolation to
    survive it.

    Exercises #79 (retry) and #78 (isolation) *together*, in one pass: one
    member's transient failure is fully absorbed by the caller's own retry
    before run_batch ever learns about it, while a second member succeeds
    normally in the same run -- both members' results must come out clean
    in a single `run_batch` call, with no failure recorded for either and
    no second run needed to pick up the retried member.

    The first underlying attempt for a member in `retry_once_for_members`
    genuinely raises `_SimulatedTransientAPIError`; `call_with_retry`
    (`max_retries=1`, real `is_retryable` gate, injected no-op `sleep`)
    catches only that type and retries once. If run_batch's (or
    `call_with_retry`'s own) exception handling regressed and let such an
    error escape unmasked, this test would actually fail rather than
    silently passing on a counter check alone."""

    def __init__(self, retry_once_for_members: set[str]):
        self._inner = FakeCaller()
        self._retry_once_for_members = set(retry_once_for_members)
        self._retried: set[str] = set()
        self._lock = threading.Lock()
        self.calls = 0
        self.attempts = 0

    def __call__(self, prompt: str) -> "batch_mod.ModelResponse":
        with self._lock:
            self.calls += 1
        member = None
        if "# Fact brief:" in prompt:
            member = prompt.split("# Fact brief:")[1].splitlines()[0].strip()

        def do_call():
            with self._lock:
                self.attempts += 1
                should_raise = (member in self._retry_once_for_members
                                and member not in self._retried)
                if should_raise:
                    self._retried.add(member)
            if should_raise:
                # A transient error on the first underlying attempt -- a
                # real raise, not just a counter/continue.
                raise _SimulatedTransientAPIError(
                    f"simulated transient error for {member}"
                )
            return self._inner(prompt)

        return retry_mod.call_with_retry(
            do_call,
            is_retryable=lambda exc: isinstance(exc, _SimulatedTransientAPIError),
            max_retries=1,
            sleep=lambda s: None,
        )


def test_batch_absorbs_a_transient_caller_retry_while_another_member_succeeds(indexed_db, tmp_path):
    """Regression test for the #79/#78 interaction: #79's internal retry and #78's
    per-member isolation must cooperate correctly in one run, not just be
    covered by separate tests of each in isolation. A caller that retries a
    transient failure internally for one member, while a second member
    succeeds normally in the same batch pool, must produce a single clean
    pass -- no failure recorded for the retried member, no re-run needed,
    and the other member's result completely unaffected."""
    members = ["MMP0100", "MMP0200"]
    state_path = tmp_path / "state.json"
    caller = RetryMaskedCaller(retry_once_for_members={"MMP0100"})

    # Keep the normal pool concurrency: the caller synchronizes its shared
    # counters and retry state, so these assertions remain deterministic.
    summary = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )

    # Nothing fails -- the retry was fully absorbed inside the caller
    # before run_batch's per-future isolation (#78) ever had anything to
    # catch, so it never needed to.
    assert summary.ok == 2
    assert summary.failed == 0

    retried_result = next(r for r in summary.results if r.member == "MMP0100")
    other_result = next(r for r in summary.results if r.member == "MMP0200")
    assert retried_result.ok is True
    # From run_batch's perspective this was one clean call -- the caller's
    # internal retry is invisible to the batch-level attempts counter,
    # which only tracks run_batch's own validation-retry path.
    assert retried_result.attempts == 1
    assert retried_result.problems == []
    assert other_result.ok is True
    assert other_result.attempts == 1
    assert other_result.problems == []

    # The caller really did retry once for MMP0100: underlying attempts are
    # exactly one higher than run_batch-visible calls, regardless of how the
    # batch implementation groups or reconciles those calls.
    assert caller.attempts == caller.calls + 1

    # Both members' success is checkpointed -- no second run_batch call is
    # needed to "pick up" the retried member; it's already done.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["natural/MILLPROD/MMP0100"]["ok"] is True
    assert state["natural/MILLPROD/MMP0200"]["ok"] is True


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


def test_batch_recomputes_briefs_when_a_dialect_hash_changes(indexed_db, tmp_path, monkeypatch):
    """issue #194: a dialect-parser code change with no source-file edit at
    all changes `source_file.dialect_hash` (set by `cli.cmd_ingest`) with
    `sha256` left untouched -- the corpus-level skip must notice this
    exactly the way it notices a `sha256` change, not just take the
    corpus-unchanged fast path past it."""
    row = indexed_db.execute(
        "SELECT source_file_id AS id FROM member WHERE name = 'MMP0100'"
    ).fetchone()
    file_id = row["id"]
    original_dialect_hash = indexed_db.execute(
        "SELECT dialect_hash FROM source_file WHERE id = ?", (file_id,)
    ).fetchone()["dialect_hash"]

    members = ["MMP0100", "MMP0200"]
    state_path = tmp_path / "state.json"
    caller = FakeCaller()
    try:
        first = batch_mod.run_batch(
            indexed_db, members, tmp_path / "out", caller, "rules", "template",
            state_path=state_path,
        )
        assert first.ok == 2

        # Simulate a re-ingest picking up an in-place dialect-parser fix:
        # bump dialect_hash directly, as `mfdoc ingest` would after
        # recomputing `cli._dialect_parser_hash` for a changed module,
        # leaving sha256 (and the installed mfdoc version) untouched.
        indexed_db.execute(
            "UPDATE source_file SET dialect_hash = ? WHERE id = ?",
            ("deadbeef" + (original_dialect_hash or ""), file_id),
        )
        indexed_db.commit()

        calls = _track_module_brief_calls(monkeypatch)

        second = batch_mod.run_batch(
            indexed_db, members, tmp_path / "out", caller, "rules", "template",
            state_path=state_path,
        )
        # Corpus signature changed, so the per-member fallback re-derives
        # every brief, the same as a sha256 change would.
        assert set(calls) == {"MMP0100", "MMP0200"}
        assert second.skipped == 2 and second.ok == 2
    finally:
        indexed_db.execute(
            "UPDATE source_file SET dialect_hash = ? WHERE id = ?",
            (original_dialect_hash, file_id),
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


def _seed_fakemod_rules_with_lines(conn, specs: list[tuple[int, int]]):
    """Like _seed_fakemod_rules, but with an explicit (line_no, depth) per
    rule -- lets a test control source-line spacing and nesting depth
    directly, to build a deliberately rule-dense chunk alongside ordinary
    ones (issue #105)."""
    from mfdoc.db import insert

    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    max_line = max(line_no for line_no, _ in specs)
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, ?, 'irrelevant')", (max_line,))
    for n, (line_no, depth) in enumerate(specs, start=1):
        insert(
            conn, "rule_candidate", member_id=1, line_no=line_no, construct="IF",
            condition=f"COND-{n}", raw=f"IF COND-{n}", depth=depth,
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


def test_generate_module_doc_isolates_a_caller_exception_to_one_chunk(tmp_path):
    """A caller exception (transient network error, rate limit, ...) on one
    chunk's model call must not propagate out of the whole chunked-member
    generation and discard the chunk_state already built for every other
    chunk in this same pass -- see issue #78 review. The failing chunk is
    recorded as a failed chunk_state entry (never written to disk, so a
    later run's `reusable` check can't mistake it for done); every other
    chunk's real, validated chunk_state survives."""
    import sqlite3
    from mfdoc.db import SCHEMA
    from mfdoc.validate import validate_doc

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 4)

    good_caller = _chunk_aware_module_caller()

    def flaky_caller(prompt: str) -> batch_mod.ModelResponse:
        if "# Fact brief:" in prompt and "BR-003" in prompt:
            raise RuntimeError("simulated transient error for chunk 2")
        return good_caller(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    result = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, flaky_caller,
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert result.ok is False
    assert any("chunk 2" in p and "model call failed" in p for p in result.problems)
    assert any("narrative synthesis: skipped" in p for p in result.problems)

    # Chunk 1 (unaffected) rendered and validated normally -- its
    # chunk_state entry must be the real, ok one, not lost because chunk 2
    # blew up in the same pass.
    assert result.chunk_state["1"]["ok"] is True
    assert result.chunk_state["2"]["ok"] is False
    assert (tmp_path / "FAKEMOD.chunk1.md").exists()
    assert validate_doc(conn, tmp_path / "FAKEMOD.chunk1.md")["ok"]
    # Chunk 2 never got a response written, so it's not mistakable for done.
    assert not (tmp_path / "FAKEMOD.chunk2.md").exists()


def test_generate_module_doc_failed_chunk_diagnostics_flag_a_rule_dense_outlier(tmp_path):
    """Issue #105: two ordinary chunks (2 rules each, tightly packed,
    shallow) plus one deliberately rule-dense chunk (2 rules, but spanning
    far more source lines with much deeper nesting) -- when the dense
    chunk is the one that fails, its own reported problem must carry a
    density diagnostic marking it an outlier relative to its siblings, so
    a human doesn't have to notice "every other chunk passed cleanly, only
    this one keeps failing" across several runs before suspecting it's a
    genuine complexity problem rather than bad luck."""
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules_with_lines(conn, [
        (1, 1), (2, 1),      # chunk 1: tight, shallow
        (10, 1), (11, 1),    # chunk 2: tight, shallow
        (20, 5), (90, 6),    # chunk 3: sprawling source, deep nesting -- the outlier
    ])

    good_caller = _chunk_aware_module_caller()

    def flaky_caller(prompt: str) -> batch_mod.ModelResponse:
        if "# Fact brief:" in prompt and "BR-005" in prompt:
            return batch_mod.ModelResponse(text="not a valid document", input_tokens=1, output_tokens=1)
        return good_caller(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    result = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, flaky_caller,
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert result.ok is False
    dense_problems = [p for p in result.problems if "chunk 3" in p]
    assert dense_problems, result.problems
    dense_problem = dense_problems[0]
    assert "density:" in dense_problem
    assert "OUTLIER" in dense_problem
    assert "lines/item" in dense_problem or "nesting depth" in dense_problem

    # The other, ordinary chunks were not flagged as outliers anywhere.
    assert not any("chunk 1" in p and "OUTLIER" in p for p in result.problems)
    assert not any("chunk 2" in p and "OUTLIER" in p for p in result.problems)


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
    chunk1 = tmp_path / "out" / subdir / "MMP0100.chunk01.md"
    assert chunk1.exists(), "MMP0100 has more than one rule_candidate in the fixtures and must chunk"
    # MMP0100 chunks into 18 parts here (one rule per call) -- exercise the
    # leading-zero padding this many chunks needs: an unpadded "chunk1.md"
    # would otherwise sort lexicographically ahead of "chunk10.md" through
    # "chunk18.md", listing out of rule order in a plain directory listing.
    # (18, not 17: issue #149 added a low-confidence rule_candidate for
    # MMP0100:48's literal-free `ADD STOCK-VIEW.AVAIL-WEIGHT TO
    # #AVAIL-TOTAL`, previously dropped with no trace at all.)
    chunk_names = sorted(p.name for p in (tmp_path / "out" / subdir).glob("MMP0100.chunk*.md"))
    assert len(chunk_names) == 18
    assert chunk_names == [f"MMP0100.chunk{i:02d}.md" for i in range(1, 19)]
    assert not (tmp_path / "out" / subdir / "MMP0100.chunk1.md").exists()
    # A member with zero (or exactly one) rule_candidate never chunks --
    # it should have gone through the ordinary single-call path instead.
    for member in members:
        if member == "MMP0100":
            continue
        subdir = batch_mod._output_subdir(indexed_db, member)
        out_path = tmp_path / "out" / subdir / f"{member}.md"
        assert out_path.exists()
    assert summary.failed == 0


def test_run_batch_gathers_a_chunked_members_facts_exactly_once(indexed_db, tmp_path, monkeypatch):
    """Issue #183 review feedback: run_batch's own routing/hashing pass
    already builds a MemberFacts for every member (to fingerprint its
    brief and decide chunked vs. not) -- for a member that turns out to
    need chunking, that same MemberFacts must be threaded through to the
    chunk-rendering path, not gathered a second time there. This is the
    orchestration-boundary check the review asked for: the existing
    chunked-path tests only exercise module_brief(facts=...) directly and
    would not catch a regression where run_batch stopped passing `facts`
    through and _generate_module_doc_chunked silently rebuilt it instead."""
    calls: dict[str, int] = {}
    real_build_member_facts = batch_mod.build_member_facts

    def counting_build_member_facts(conn, member_name):
        calls[member_name] = calls.get(member_name, 0) + 1
        return real_build_member_facts(conn, member_name)

    monkeypatch.setattr(batch_mod, "build_member_facts", counting_build_member_facts)

    members = batch_mod.select_batch_members(indexed_db)
    assert "MMP0100" in members
    caller = FakeCaller()
    summary = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        max_rules_per_call=1,
    )
    assert summary.failed == 0
    assert calls.get("MMP0100") == 1, (
        "MMP0100 chunks under max_rules_per_call=1 -- its whole-member facts "
        f"must be gathered exactly once by run_batch, not {calls.get('MMP0100')} times"
    )


def test_run_batch_chunking_prunes_stale_legacy_chunk_files(indexed_db, tmp_path):
    """A rerun after the unpadded->padded chunk-filename migration (or any
    rerun whose chunk width changes) must not leave old-named chunk files
    behind alongside the new ones."""
    members = ["MMP0100"]
    subdir = batch_mod._output_subdir(indexed_db, "MMP0100")
    out_dir = tmp_path / "out" / subdir
    out_dir.mkdir(parents=True)
    # Plant a legacy unpadded file and an unrelated same-stem file that
    # must survive the prune (it isn't a chunk file at all).
    stale = out_dir / "MMP0100.chunk1.md"
    stale.write_text("stale", encoding="utf-8")
    unrelated = out_dir / "MMP0100.notes.md"
    unrelated.write_text("keep me", encoding="utf-8")

    batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", FakeCaller(), "rules", "template",
        max_rules_per_call=1,
    )

    assert not stale.exists()
    assert unrelated.exists()
    assert (out_dir / "MMP0100.chunk01.md").exists()


def test_prune_stale_chunk_files_skips_directories_and_ignores_unlink_errors(tmp_path):
    """A directory (or any entry that can't be removed) that happens to
    match the chunk-file naming pattern must not abort pruning -- best
    effort cleanup, never a hard failure over cosmetic tidiness."""
    out_path = tmp_path / "FAKEMOD.md"
    tmp_path.mkdir(exist_ok=True)
    stale_dir = tmp_path / "FAKEMOD.chunk1.md"
    stale_dir.mkdir()
    stale_file = tmp_path / "FAKEMOD.chunk2.md"
    stale_file.write_text("stale", encoding="utf-8")

    batch_mod._prune_stale_chunk_files(out_path, {"FAKEMOD.chunk01.md"})

    assert stale_dir.is_dir()  # untouched, not raised on
    assert not stale_file.exists()


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


def test_chunk_resume_regenerates_a_chunk_the_prior_run_recorded_as_failed(tmp_path):
    """A chunk that failed leaves its last (invalid) attempt on disk, so
    reusing it on the strength of an unchanged brief hash alone would
    re-validate the same bad file forever and never re-render it."""
    import sqlite3
    from mfdoc.db import SCHEMA
    from mfdoc.validate import validate_doc

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 4)

    good_caller = _chunk_aware_module_caller()

    def flaky_caller(prompt: str) -> batch_mod.ModelResponse:
        if "BR-003" in prompt and "# Fact brief:" in prompt:
            return batch_mod.ModelResponse(text="not a valid document", input_tokens=1, output_tokens=1)
        return good_caller(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    first = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, flaky_caller,
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert first.ok is False
    assert first.chunk_state["2"]["ok"] is False
    assert (tmp_path / "FAKEMOD.chunk2.md").exists(), "the failed attempt is left on disk"
    chunk1_before = (tmp_path / "FAKEMOD.chunk1.md").read_text(encoding="utf-8")

    second = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, good_caller,
        "writing rules text", "template text", max_rules_per_call=2,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True, second.problems
    assert validate_doc(conn, tmp_path / "FAKEMOD.chunk2.md")["ok"]
    assert (tmp_path / "FAKEMOD.chunk1.md").read_text(encoding="utf-8") == chunk1_before


def test_chunk_resume_regenerates_a_reused_chunk_that_fails_revalidation(tmp_path):
    """A cached chunk the prior run recorded clean but that no longer
    validates must fall back to a normal regeneration, not be reported as a
    terminal failure."""
    import sqlite3
    from mfdoc.db import SCHEMA
    from mfdoc.validate import validate_doc

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    first = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, _chunk_aware_module_caller(),
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert first.ok is True, first.problems
    (tmp_path / "FAKEMOD.chunk2.md").write_text("not a valid document", encoding="utf-8")

    second = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, _chunk_aware_module_caller(),
        "writing rules text", "template text", max_rules_per_call=2,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True, second.problems
    assert validate_doc(conn, tmp_path / "FAKEMOD.chunk2.md")["ok"]


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


def test_plan_batch_reports_a_member_with_no_prior_state_as_render(indexed_db, tmp_path):
    """No --state file at all (or an empty one) -- every member is a fresh
    render, never chunked here (MMP0100's rule count is under any
    reasonable default threshold)."""
    plan = batch_mod.plan_batch(indexed_db, ["MMP0100"], tmp_path / "out")
    assert plan.corpus_unchanged is False
    assert len(plan.members) == 1
    assert plan.members[0].status == "render"
    assert plan.members_render == 1 and plan.members_skip == 0 and plan.members_chunked == 0


def test_plan_batch_reports_skip_after_a_real_run_with_unchanged_facts(indexed_db, tmp_path):
    members = ["MMP0100"]
    state_path = tmp_path / "state.json"
    caller = FakeCaller()
    first = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )
    assert first.ok == 1

    plan = batch_mod.plan_batch(indexed_db, members, tmp_path / "out", state_path=state_path)
    assert plan.corpus_unchanged is True
    assert plan.members[0].status == "skip"
    assert plan.members_skip == 1 and plan.members_render == 0


def test_plan_batch_falls_back_to_member_level_skip_when_only_the_corpus_signature_changed(
    indexed_db, tmp_path,
):
    """A sha256 bump with no actual fact-table change (nothing module_brief
    reads changed) invalidates the cheap corpus-level fast path, but the
    per-member brief-hash fallback still finds the same brief text and
    reports skip -- matching test_batch_recomputes_briefs_when_a_source_file_
    changes's real run_batch behavior (the model is not re-called either)."""
    row = indexed_db.execute(
        "SELECT source_file_id AS id FROM member WHERE name = 'MMP0100'"
    ).fetchone()
    file_id = row["id"]
    original_sha = indexed_db.execute(
        "SELECT sha256 FROM source_file WHERE id = ?", (file_id,)
    ).fetchone()["sha256"]

    members = ["MMP0100"]
    state_path = tmp_path / "state.json"
    caller = FakeCaller()
    try:
        first = batch_mod.run_batch(
            indexed_db, members, tmp_path / "out", caller, "rules", "template",
            state_path=state_path,
        )
        assert first.ok == 1

        indexed_db.execute(
            "UPDATE source_file SET sha256 = ? WHERE id = ?",
            ("deadbeef" + original_sha, file_id),
        )
        indexed_db.commit()

        plan = batch_mod.plan_batch(indexed_db, members, tmp_path / "out", state_path=state_path)
        assert plan.corpus_unchanged is False, "corpus-level fast path must not apply here"
        assert plan.members[0].status == "skip", "per-member brief hash is still unchanged"
    finally:
        indexed_db.execute("UPDATE source_file SET sha256 = ? WHERE id = ?", (original_sha, file_id))
        indexed_db.commit()


def test_plan_batch_matches_generate_module_doc_chunk_reuse(tmp_path):
    """The core promise of the dry-run preview: its chunk-level reuse count
    for a chunked member must match what a real generate_module_doc call
    would actually do -- computed via the same _chunk_reuse_ok, not a
    second, potentially-drifting copy of the reuse rule."""
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)

    # Match plan_batch's own out_path/state_key computation exactly
    # (out_dir / _output_subdir / "<member>.md") rather than assume a flat
    # layout -- plan_batch always nests by dialect/library, same as
    # run_batch itself.
    out_dir = tmp_path / "out"
    subdir = batch_mod._output_subdir(conn, "FAKEMOD")
    out_path = out_dir / subdir / "FAKEMOD.md"
    first = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, _chunk_aware_module_caller(),
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert first.ok is True
    assert first.attempts == 3  # chunks rendered, per DocResult.attempts' chunked meaning

    state_key = f"{subdir.as_posix()}/FAKEMOD"
    state = {state_key: {"ok": True, "chunks": first.chunk_state}}
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    # Change rule 3's condition -- same perturbation as
    # test_chunk_resume_only_regenerates_the_chunk_whose_own_brief_changed --
    # only chunk 2 (rules 3-4) should need a re-render.
    conn.execute("UPDATE rule_candidate SET condition='COND-3-CHANGED' WHERE line_no=3")
    conn.commit()

    plan = batch_mod.plan_batch(
        conn, ["FAKEMOD"], out_dir, state_path=state_path, max_rules_per_call=2,
    )
    assert plan.members[0].status == "chunked"
    assert plan.members[0].chunk_count == 3
    assert plan.members[0].chunks_reusable == 2
    assert plan.members[0].chunks_to_render == 1

    second = batch_mod.generate_module_doc(
        conn, "FAKEMOD", out_path, _chunk_aware_module_caller(),
        "writing rules text", "template text", max_rules_per_call=2,
        prior_chunks=first.chunk_state,
    )
    actually_reused = sum(
        1 for i in range(1, 4)
        if second.chunk_state[str(i)]["brief_sha256"] == first.chunk_state[str(i)]["brief_sha256"]
    )
    assert actually_reused == plan.members[0].chunks_reusable


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
    """Gap-register rows (highest real severity first) come first; a
    chunk's own sme_questions add text not already present, and an exact
    duplicate -- whether between two chunks or of a gap row's own text --
    appears only once."""
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

    # Real severity priority (db.GAP_SEVERITY_ORDER_SQL), shared with
    # module_brief's own "Known gaps" section -- "high" sorts first, not
    # "low" (TEXT lexicographic order would put "low" ahead of "high").
    assert lines[0].startswith("[high]") and "orphan_module" in lines[0]
    assert lines[1].startswith("[low]") and "unused_field" in lines[1]
    assert lines.count("Is X still used by any downstream job?") == 1
    assert not any(line == "No caller found for FAKEMOD." for line in lines[2:])
    assert len(lines) == 3  # 2 gap rows + 1 genuinely new sme_question


def test_extract_section_finds_named_heading_and_returns_none_when_absent_or_blank():
    body = "# Doc\n\n## Purpose\n\nSome text here.\n\n## Inputs\n\n\n\n## Outputs and effects\n\nMore text.\n"
    assert batch_mod._extract_section(body, "Purpose") == "Some text here."
    assert batch_mod._extract_section(body, "Outputs and effects") == "More text."
    assert batch_mod._extract_section(body, "Inputs") is None  # present but blank
    assert batch_mod._extract_section(body, "Data used") is None  # absent entirely


def test_extract_section_is_fence_aware_and_ignores_a_hash_hash_line_inside_a_code_block():
    """A `##`-prefixed line inside a fenced code block (a Mermaid comment, an
    example markdown snippet quoted in the section's own prose) must never
    be mistaken for the next section's heading -- a regex-only scan over
    the whole body can't tell the difference and would truncate early."""
    body = (
        "# Doc\n\n"
        "## Purpose\n\n"
        "Explains the flow, with an example:\n\n"
        "```\n"
        "## This looks like a heading but is inside a fence\n"
        "```\n\n"
        "Still part of Purpose.\n\n"
        "## Inputs\n\n"
        "Real next section.\n"
    )
    assert batch_mod._extract_section(body, "Purpose") == (
        "Explains the flow, with an example:\n\n"
        "```\n"
        "## This looks like a heading but is inside a fence\n"
        "```\n\n"
        "Still part of Purpose."
    )
    assert batch_mod._extract_section(body, "Inputs") == "Real next section."


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

    ok, attempts, in_tok, out_tok, problems, sections, duration_s, retries = batch_mod._generate_module_index_narrative(
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

    ok, attempts, in_tok, out_tok, problems, sections, duration_s, retries = batch_mod._generate_module_index_narrative(
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

    ok, attempts, in_tok, out_tok, problems, sections, duration_s, retries = batch_mod._generate_module_index_narrative(
        conn, "FAKEMOD", [(1, "## Purpose\n\nSomething [[FAKEMOD:1]].")], caller,
        "writing rules", None, out_path, assemble, max_attempts=2,
    )
    assert ok is False
    assert attempts == 2
    assert problems


def test_render_module_index_doc_raises_on_chunk_entries_routine_labels_mismatch():
    """chunk_entries and routine_labels must be the same length -- a plain
    zip() would silently truncate to the shorter list (dropping trailing
    chunks from the rendered index) instead of surfacing the mismatch."""
    result = batch_mod.DocResult("FAKEMOD", "FAKEMOD.chunk01.md", True, 1, 1, 1, [])
    chunk_entries = [
        (1, (1, 1), Path("FAKEMOD.chunk01.md"), result),
        (2, (2, 2), Path("FAKEMOD.chunk02.md"), result),
    ]
    sections = {h: "text [[FAKEMOD:1]]." for h in batch_mod.NARRATIVE_SECTIONS}
    with pytest.raises(ValueError):
        batch_mod._render_module_index_doc(
            "FAKEMOD", "MOM", chunk_entries, {"verified": 1, "inferred": 0, "unresolved": 0},
            ["label only for chunk 1"], [], sections,
        )


def test_consolidated_gap_lines_orders_by_real_severity_priority(tmp_path):
    """Gap rows must sort by real severity priority (high, medium, low), not
    gap.severity's own TEXT/lexicographic order (which would put medium,
    low, high in that order)."""
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    insert(conn, "gap", member_id=1, gap_kind="k1", severity="medium", detail="Medium one.", line_no=1)
    insert(conn, "gap", member_id=1, gap_kind="k2", severity="low", detail="Low one.", line_no=2)
    insert(conn, "gap", member_id=1, gap_kind="k3", severity="high", detail="High one.", line_no=3)
    conn.commit()

    lines = batch_mod._consolidated_gap_lines(conn, "FAKEMOD", 1, [])
    assert [l.split("]")[0] + "]" for l in lines] == ["[high]", "[medium]", "[low]"]


def test_consolidated_gap_lines_dedupes_sme_question_against_a_later_gap_sentence(tmp_path):
    """seen_content must track every sentence of a gap's detail, not just
    the first -- an sme_question duplicating a later sentence (or the full
    multi-sentence detail) must still be recognised as already covered."""
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    insert(
        conn, "gap", member_id=1, gap_kind="orphan_module", severity="high", line_no=5,
        detail="No caller found for FAKEMOD. The module may still be invoked via JCL not ingested here.",
    )
    conn.commit()

    chunk = tmp_path / "FAKEMOD.chunk01.md"
    chunk.write_text(
        '---\nsme_questions:\n  - "The module may still be invoked via JCL not ingested here."\n---\n',
        encoding="utf-8",
    )

    lines = batch_mod._consolidated_gap_lines(conn, "FAKEMOD", 1, [chunk])
    assert len(lines) == 1, "the sme_question duplicates the gap's own second sentence and must be dropped"


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


def test_consolidated_gap_lines_cites_every_sentence_in_a_multi_sentence_detail():
    """A gap `detail` with more than one sentence must keep every sentence
    (not just the first) -- but each one still needs its own citation
    immediately before it, or validate_doc's uncited-assertion check would
    flag a later sentence that happens to open with an assertive phrase."""
    import sqlite3
    from mfdoc.db import SCHEMA, insert
    from mfdoc.validate import _uncited_assertions

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    insert(
        conn, "gap", member_id=1, gap_kind="orphan_module", severity="high", line_no=5,
        detail="No caller found for FAKEMOD. The module may still be invoked via JCL not ingested here.",
    )
    conn.commit()

    lines = batch_mod._consolidated_gap_lines(conn, "FAKEMOD", 1, [])
    assert len(lines) == 1
    line = lines[0]
    assert "No caller found for FAKEMOD." in line
    assert "The module may still be invoked via JCL not ingested here." in line
    # Both sentences must carry their own citation, not just the first.
    assert line.count("[[FAKEMOD:5]]") == 2
    assert _uncited_assertions(line) == []


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

    ok, attempts, in_tok, out_tok, problems, sections, duration_s, retries = batch_mod._generate_module_index_narrative(
        conn, "FAKEMOD", [(1, "## Purpose\n\nSomething [[FAKEMOD:1]].")], caller,
        "writing rules", None, out_path, assemble, max_attempts=2,
    )
    assert ok is False
    assert sections is None
    assert any("not present in any given chunk excerpt" in p for p in problems)


def test_generate_module_index_narrative_retry_prompt_carries_provenance_problem(tmp_path):
    """A rejected attempt whose only failure is a provenance violation
    (validate_doc itself finds nothing wrong -- the citation resolves)
    must still put that problem in the next attempt's retry_note, or the
    model gets no signal and just repeats the same invented citation."""
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 2, 'irrelevant')")
    conn.commit()

    prompts: list[str] = []

    def caller(prompt):
        prompts.append(prompt)
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

    batch_mod._generate_module_index_narrative(
        conn, "FAKEMOD", [(1, "## Purpose\n\nSomething [[FAKEMOD:1]].")], caller,
        "writing rules", None, out_path, assemble, max_attempts=2,
    )

    assert len(prompts) == 2
    assert "Previous attempt failed validation" in prompts[1]
    assert "not present in any given chunk excerpt" in prompts[1]
