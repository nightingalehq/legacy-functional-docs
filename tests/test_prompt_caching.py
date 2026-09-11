"""Issue #159: build_prompt/build_reconciliation_prompt's stable-prefix
helpers, and run_batch's wiring of them into any caller that opts in via
set_cache_prefixes. No real network call or `anthropic` package is needed
here -- these guard the pure-Python prompt-splitting logic in batch.py
itself; test_anthropic_caller.py/test_vertex_caller.py guard the callers
that actually consume it.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path

from mfdoc.batch import (
    _apply_cache_prefixes,
    build_member_prompt_cache_prefix,
    build_prompt,
    build_prompt_cache_prefix,
    build_prompt_parts,
    build_reconciliation_prompt,
    build_reconciliation_prompt_cache_prefix,
    build_reconciliation_prompt_parts,
    generate_module_doc,
)
from mfdoc.brief import MemberFacts, build_member_facts, member_shared_prefix
from mfdoc.redact import NULL_REDACTOR


def test_build_prompt_is_exactly_its_own_parts_joined():
    """Single source of truth: build_prompt must never diverge from
    "\\n\\n---\\n\\n".join(build_prompt_parts(...))."""
    brief, rules, template = "a fact brief", "some writing rules", "a template"
    assert build_prompt(brief, rules, template) == "\n\n---\n\n".join(
        build_prompt_parts(brief, rules, template)
    )
    assert build_prompt(brief, rules, template, "retry note") == "\n\n---\n\n".join(
        build_prompt_parts(brief, rules, template, "retry note")
    )


def test_build_prompt_cache_prefix_is_a_true_prefix_of_build_prompt():
    """The whole point: AnthropicCaller/VertexCaller must be able to find
    this prefix with a plain `str.startswith`, no re-parsing."""
    rules, template = "some writing rules", "a template"
    prefix = build_prompt_cache_prefix(rules, template)
    prompt = build_prompt("a fact brief", rules, template)
    assert prompt.startswith(prefix)
    # And the prefix stops exactly where the fact brief starts.
    assert prompt[len(prefix):].startswith("# Fact brief\n\n")


def test_build_prompt_cache_prefix_is_identical_across_different_briefs_and_retries():
    """The entire caching win depends on this prefix being byte-identical
    across every chunk/member/retry sharing one writing_rules/template --
    only the brief (and retry note) may vary."""
    rules, template = "some writing rules", "a template"
    prefix_a = build_prompt_cache_prefix(rules, template)
    prompt_one_member = build_prompt("brief for member one", rules, template)
    prompt_another_member = build_prompt("a totally different brief", rules, template)
    prompt_on_retry = build_prompt("brief for member one", rules, template, "fix your citations")
    assert prompt_one_member.startswith(prefix_a)
    assert prompt_another_member.startswith(prefix_a)
    assert prompt_on_retry.startswith(prefix_a)


def test_build_prompt_cache_prefix_changes_when_writing_rules_or_template_change():
    """A prefix computed for one project's writing_rules/template must not
    be mistaken for a match against a different project's -- the retained
    behavior here is just plain string equality/startswith, no cross-project
    leakage risk, but worth pinning down explicitly."""
    prefix_a = build_prompt_cache_prefix("rules A", "template A")
    prefix_b = build_prompt_cache_prefix("rules B", "template B")
    assert prefix_a != prefix_b


def test_build_reconciliation_prompt_is_exactly_its_own_parts_joined():
    sources = ["### Chunk 1\n\nsome excerpt [[MOD:1]]."]
    assert build_reconciliation_prompt("SOMEMOD", sources, "rules", "index template") == (
        "\n\n---\n\n".join(
            build_reconciliation_prompt_parts("SOMEMOD", sources, "rules", "index template")
        )
    )


def test_build_reconciliation_prompt_cache_prefix_is_a_true_prefix():
    sources = ["### Chunk 1\n\nsome excerpt [[MOD:1]]."]
    rules, index_template = "some writing rules", "an index template"
    prefix = build_reconciliation_prompt_cache_prefix(rules, index_template)
    prompt = build_reconciliation_prompt("SOMEMOD", sources, rules, index_template)
    assert prompt.startswith(prefix)


def test_build_reconciliation_prompt_cache_prefix_is_identical_across_different_members():
    """This is the reordering issue #159 required: member_name and the
    chunk excerpts must come *after* the stable writing-rules/template
    prefix, not before it, or the prefix could never match across members
    at all (a cache breakpoint requires an exact match of everything before
    it, not just the marked block's own text)."""
    rules, index_template = "some writing rules", "an index template"
    prefix = build_reconciliation_prompt_cache_prefix(rules, index_template)
    sources = ["### Chunk 1\n\nan excerpt [[MOD:1]]."]
    prompt_module_one = build_reconciliation_prompt("MODULEONE", sources, rules, index_template)
    prompt_module_two = build_reconciliation_prompt("A_DIFFERENT_MODULE", sources, rules, index_template)
    assert prompt_module_one.startswith(prefix)
    assert prompt_module_two.startswith(prefix)


def test_build_reconciliation_prompt_cache_prefix_without_an_index_template():
    """index_template is optional (None) -- the prefix helper must derive
    the same (shorter) prefix build_reconciliation_prompt itself produces
    when none is configured, not silently include a stale/absent section."""
    rules = "some writing rules"
    prefix = build_reconciliation_prompt_cache_prefix(rules, None)
    sources = ["### Chunk 1\n\nan excerpt [[MOD:1]]."]
    prompt = build_reconciliation_prompt("SOMEMOD", sources, rules, None)
    assert prompt.startswith(prefix)
    assert "Module-index template" not in prefix


class _CacheAwareFakeCaller:
    """Stands in for AnthropicCaller/VertexCaller's opt-in surface without
    depending on either module -- only `set_cache_prefixes` matters here."""

    def __init__(self):
        self.registered: list[str] | None = None

    def set_cache_prefixes(self, prefixes) -> None:
        self.registered = list(prefixes)

    def __call__(self, prompt):  # pragma: no cover - not exercised by these tests
        raise NotImplementedError


def test_apply_cache_prefixes_registers_both_prefixes_on_an_opted_in_caller():
    caller = _CacheAwareFakeCaller()
    _apply_cache_prefixes(caller, "some writing rules", "a template", "an index template")
    assert caller.registered == [
        build_prompt_cache_prefix("some writing rules", "a template"),
        build_reconciliation_prompt_cache_prefix("some writing rules", "an index template"),
    ]


def test_apply_cache_prefixes_is_a_no_op_for_a_caller_without_the_hook():
    """ClaudeCLICaller and the fake-echo test caller have no
    `set_cache_prefixes` method at all -- this must not raise, and must not
    add one either (that would silently change what `hasattr` sees on a
    later call elsewhere)."""

    def plain_caller(prompt):  # pragma: no cover - never called
        raise NotImplementedError

    _apply_cache_prefixes(plain_caller, "rules", "template", None)
    assert not hasattr(plain_caller, "set_cache_prefixes")


# --- issue #214: member-level shared-prefix cache tier ---------------------

def test_build_member_prompt_cache_prefix_is_a_true_prefix_of_the_fact_brief_section():
    """build_member_prompt_cache_prefix's output must line up exactly with
    where build_prompt_parts's own '# Fact brief\\n\\n' heading puts a
    member's shared_prefix + separator, ahead of that chunk's own
    module_brief() text -- the same contract build_prompt_cache_prefix has
    for the project-level tier."""
    shared_prefix = "some member-level shared context"
    member_prefix = build_member_prompt_cache_prefix(shared_prefix)
    brief = shared_prefix + "\n\n---\n\nchunk-specific brief text"
    prompt = build_prompt(brief, "some writing rules", "a template")
    project_prefix = build_prompt_cache_prefix("some writing rules", "a template")
    assert prompt[len(project_prefix):].startswith(member_prefix)
    assert prompt[len(project_prefix) + len(member_prefix):] == "chunk-specific brief text"


def test_build_member_prompt_cache_prefix_differs_for_different_shared_prefixes():
    assert build_member_prompt_cache_prefix("member A's context") != build_member_prompt_cache_prefix(
        "member B's context"
    )


class _MemberCacheAwareFakeCaller:
    """Records both tiers a chunked member's run registers, and every
    prompt it's actually called with -- proves run_batch/generate_module_doc
    wire the member-level tier (issue #214) the same way _apply_cache_
    prefixes already wires the project-level one."""

    def __init__(self):
        self.member_registered: list[list[str]] = []
        self.project_registered: list[list[str]] = []
        self.prompts_seen: list[str] = []

    def set_member_cache_prefixes(self, prefixes) -> None:
        self.member_registered.append(list(prefixes))

    def set_cache_prefixes(self, prefixes) -> None:
        self.project_registered.append(list(prefixes))

    def __call__(self, prompt):
        self.prompts_seen.append(prompt)
        ids_re = re.compile(r"FAKEMOD:BR-\d+")
        if "# Fact brief:" not in prompt:
            # The one narrative-reconciliation call -- give it a minimal
            # valid five-section response, same shape test_batch.py's
            # _chunk_aware_module_caller uses.
            from mfdoc.batch import NARRATIVE_SECTIONS

            sections = "\n\n".join(
                f"## {h}\n\nCovers the module as a whole [[FAKEMOD:1]]." for h in NARRATIVE_SECTIONS
            )
            return _fake_response(
                f"{sections}\n", input_tokens=1, output_tokens=1,
            )
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

Does something [[FAKEMOD:1]].

## How it is invoked

Something invokes it [[FAKEMOD:1]].

## Inputs

Nothing beyond what's cited above [[FAKEMOD:1]].

## Data used

Nothing beyond what's cited above [[FAKEMOD:1]].

## Business rules

{rule_lines}

## Outputs and effects

Nothing beyond what's cited above [[FAKEMOD:1]].
"""
        return _fake_response(text, input_tokens=1, output_tokens=2)


def _fake_response(text, input_tokens, output_tokens):
    from mfdoc.batch import ModelResponse

    return ModelResponse(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


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


def test_generate_module_doc_chunked_registers_one_member_prefix_before_the_chunk_loop():
    """Issue #214: a chunked member must register its own member-level
    shared-prefix exactly once, before any of its chunks are rendered --
    not once per chunk (that would defeat the point: the whole reason to
    register it once is so every chunk's build_prompt() call shares the
    exact same registered text)."""
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)  # -> 3 chunks with max_rules_per_call=2

    caller = _MemberCacheAwareFakeCaller()
    facts = build_member_facts(conn, "FAKEMOD")
    assert isinstance(facts, MemberFacts)
    expected_prefix = build_member_prompt_cache_prefix(member_shared_prefix(facts, NULL_REDACTOR))

    result = generate_module_doc(
        conn, "FAKEMOD", _tmp_out_path(), caller, "writing rules text", "template text",
        max_rules_per_call=2,
    )
    assert result.chunked is True
    assert caller.member_registered == [[expected_prefix]]
    # Copilot review round 3 on PR #215: generate_module_doc is a public,
    # direct call path (like this test) that never goes through run_batch's
    # own _apply_cache_prefixes -- without also registering the project-
    # level prefix here, a fresh caller would have no project tier active,
    # AnthropicCaller._content's outer match would fail for every prompt,
    # and no cache_control would ever be applied to anything at all, project
    # or member tier, even though shared_prefix's text is still (correctly,
    # per the dedup fix) the only copy of those facts in the prompt.
    assert caller.project_registered, "project-level prefix must also be registered on this path"

    # And every per-chunk prompt (not the narrative-reconciliation call)
    # actually starts with the registered member-level prefix, right after
    # the "# Fact brief\n\n" heading build_prompt_parts always adds.
    chunk_prompts = [p for p in caller.prompts_seen if "# Fact brief:" in p]
    assert chunk_prompts, "expected at least one per-chunk prompt"
    for prompt in chunk_prompts:
        assert expected_prefix in prompt


def test_generate_module_doc_chunked_does_not_duplicate_shared_facts_in_the_actual_prompt():
    """The core of Copilot review round 3 on PR #215: a cache hit only
    changes billing, not how many tokens are in the request -- so a
    chunk's own module_brief() text must not still carry the same whole-
    member facts shared_prefix already sent. Checked here at the full
    prompt-assembly level (not just module_brief's own output), so a
    regression in how batch.py wires shared_prefix through would be
    caught even if module_brief's own unit tests still passed."""
    from mfdoc.db import SCHEMA

    from mfdoc.db import insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)  # -> 3 chunks with max_rules_per_call=2
    # A whole-member fact FAKEMOD's own fixture otherwise has none of --
    # without this, there'd be nothing for the dedup fix to actually
    # deduplicate, and this test would pass even with the pre-fix
    # duplicated-content behavior.
    insert(
        conn, "call_edge", caller_id=1, callee_name="OTHERMOD", call_kind="CALLNAT",
        line_no=1, resolved=False,
    )
    conn.commit()

    caller = _MemberCacheAwareFakeCaller()
    result = generate_module_doc(
        conn, "FAKEMOD", _tmp_out_path(), caller, "writing rules text", "template text",
        max_rules_per_call=2,
    )
    assert result.chunked is True
    chunk_prompts = [p for p in caller.prompts_seen if "# Fact brief:" in p]
    assert chunk_prompts, "expected at least one per-chunk prompt"
    for prompt in chunk_prompts:
        # "## Outbound calls" is a whole-member section shared_prefix
        # already covers -- it must appear at most once in the whole
        # prompt (via shared_prefix), never a second time via this chunk's
        # own trimmed module_brief() text.
        assert prompt.count("## Outbound calls") == 1
        # "## Candidate business rules" is the one section every chunk's
        # own module_brief() text must still carry (it's the whole reason a
        # chunk exists) -- shared_prefix never renders it.
        assert prompt.count("## Candidate business rules") == 1


def _tmp_out_path():
    return Path(tempfile.mkdtemp()) / "FAKEMOD.md"


class _PlainFakeCaller(_MemberCacheAwareFakeCaller):
    """Same __call__ behavior as _MemberCacheAwareFakeCaller, but with no
    set_member_cache_prefixes hook at all (matches ClaudeCLICaller and the
    fake-echo test caller) -- so a chunked run against it exercises the
    caching-capability gate (issue #214, Copilot review on PR #215)."""

    set_member_cache_prefixes = None


def test_generate_module_doc_chunked_does_not_prepend_shared_prefix_for_a_non_cache_capable_caller():
    """A caller without set_member_cache_prefixes (ClaudeCLICaller, the
    fake-echo test caller) must never receive shared_prefix prepended into
    its own per-chunk brief -- with no cache breakpoint to make it a
    saving, that prepend would be a pure duplicate-context/token-cost
    regression, and for a caller that actually generates real narrative
    text, a genuine (uncached) change to model input -- not the caching-
    only change issue #214 is scoped to (Copilot review on PR #215)."""
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)  # -> 3 chunks with max_rules_per_call=2

    caller = _PlainFakeCaller()
    facts = build_member_facts(conn, "FAKEMOD")
    assert isinstance(facts, MemberFacts)
    shared_prefix = member_shared_prefix(facts, NULL_REDACTOR)

    result = generate_module_doc(
        conn, "FAKEMOD", _tmp_out_path(), caller, "writing rules text", "template text",
        max_rules_per_call=2,
    )
    assert result.chunked is True
    chunk_prompts = [p for p in caller.prompts_seen if "# Fact brief:" in p]
    assert chunk_prompts, "expected at least one per-chunk prompt"
    for prompt in chunk_prompts:
        assert shared_prefix not in prompt


def test_plan_batch_chunk_hash_matches_a_real_run_with_a_non_cache_capable_caller():
    """plan_batch's default (member_cache_capable=False) must match a real
    generate_module_doc call against a caller with no member-level caching
    hook -- the common case (--provider claude-code, or a caller nobody
    told about member-level caching)."""
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)

    out_dir_first = _tmp_out_path().parent
    first = generate_module_doc(
        conn, "FAKEMOD", out_dir_first / "FAKEMOD.md", _PlainFakeCaller(),
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert first.ok

    from mfdoc.batch import _output_subdir, plan_batch

    out_dir = Path(tempfile.mkdtemp())
    subdir = _output_subdir(conn, "FAKEMOD")
    out_path = out_dir / subdir / "FAKEMOD.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Reuse the same chunk files/state a real (non-cache-capable) run just
    # produced, at the path plan_batch itself expects.
    for src in Path(str(first.path)).parent.glob("FAKEMOD.chunk*.md"):
        (out_path.parent / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    state_key = f"{subdir.as_posix()}/FAKEMOD"
    state = {state_key: {"ok": True, "chunks": first.chunk_state}}
    state_path = out_dir / "state.json"
    import json as _json

    state_path.write_text(_json.dumps(state), encoding="utf-8")

    plan = plan_batch(conn, ["FAKEMOD"], out_dir, state_path=state_path, max_rules_per_call=2)
    assert plan.members[0].status == "chunked"
    assert plan.members[0].chunks_reusable == 3, (
        "every chunk should be reusable -- plan_batch's default "
        "member_cache_capable=False must hash the same string a real run "
        "against a non-cache-capable caller just produced"
    )


def _seed_fakemod_single_oversized_routine(conn, count: int):
    """Like _seed_fakemod_rules, but every rule falls inside one routine
    spanning the whole member -- routine_aware_chunk_ranges never splits a
    routine even when it's bigger than chunk_size (see
    test_chunk_ranges_never_splits_a_routine_even_when_oversized), so a
    member seeded this way still takes the chunked path (its rule count
    exceeds max_rules_per_call) but collapses to exactly one chunk."""
    from mfdoc.db import insert

    _seed_fakemod_rules(conn, count)
    insert(
        conn, "routine", member_id=1, name="BIGROUTINE", kind="natural_subroutine",
        start_line=1, end_line=count,
    )
    conn.commit()


def test_generate_module_doc_chunked_does_not_use_member_cache_for_a_single_chunk_member():
    """Copilot review, second pass on PR #215: a member whose routine-aware
    chunking collapses to exactly one chunk has no second call left to ever
    read a member-level cache entry -- registering/prepending shared_prefix
    there is a pure cache-write cost with no matching read, not a saving.
    Must behave like a non-cache-capable caller for this one member, even
    though the caller itself supports set_member_cache_prefixes."""
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_single_oversized_routine(conn, 5)  # exceeds max_rules_per_call=2, but one chunk

    caller = _MemberCacheAwareFakeCaller()
    result = generate_module_doc(
        conn, "FAKEMOD", _tmp_out_path(), caller, "writing rules text", "template text",
        max_rules_per_call=2,
    )
    assert result.chunked is True
    assert caller.member_registered == [], "no second chunk exists to ever read this cache entry"

    facts = build_member_facts(conn, "FAKEMOD")
    assert isinstance(facts, MemberFacts)
    shared_prefix = member_shared_prefix(facts, NULL_REDACTOR)
    chunk_prompts = [p for p in caller.prompts_seen if "# Fact brief:" in p]
    assert chunk_prompts, "expected at least one per-chunk prompt"
    for prompt in chunk_prompts:
        assert shared_prefix not in prompt


def test_plan_batch_does_not_hash_a_member_prefix_for_a_single_chunk_member():
    """Mirror of the above for plan_batch's preview: even with
    member_cache_capable=True, a member that collapses to one chunk must
    hash the same as a real run against a cache-capable caller (chunks_
    reusable stays aligned with what that caller was actually given)."""
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_single_oversized_routine(conn, 5)

    first = generate_module_doc(
        conn, "FAKEMOD", _tmp_out_path(), _MemberCacheAwareFakeCaller(),
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert first.ok
    assert first.chunk_state is not None and len(first.chunk_state) - 1 == 1  # 1 chunk + "_narrative"

    from mfdoc.batch import _output_subdir, plan_batch

    out_dir = Path(tempfile.mkdtemp())
    subdir = _output_subdir(conn, "FAKEMOD")
    out_path = out_dir / subdir / "FAKEMOD.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for src in Path(str(first.path)).parent.glob("FAKEMOD.chunk*.md"):
        (out_path.parent / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    state_key = f"{subdir.as_posix()}/FAKEMOD"
    state = {state_key: {"ok": True, "chunks": first.chunk_state}}
    state_path = out_dir / "state.json"
    import json as _json

    state_path.write_text(_json.dumps(state), encoding="utf-8")

    plan = plan_batch(
        conn, ["FAKEMOD"], out_dir, state_path=state_path, max_rules_per_call=2,
        member_cache_capable=True,
    )
    assert plan.members[0].chunk_count == 1
    assert plan.members[0].chunks_reusable == 1


def test_plan_batch_chunk_hash_matches_a_real_run_with_a_cache_capable_caller():
    """The mirror image: member_cache_capable=True must match a real
    generate_module_doc call against a caller that *does* expose
    set_member_cache_prefixes -- and must disagree with the plain-caller
    state from the sibling test above (proving the flag actually changes
    the computed hash, not just a no-op parameter)."""
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_rules(conn, 5)

    first = generate_module_doc(
        conn, "FAKEMOD", _tmp_out_path(), _MemberCacheAwareFakeCaller(),
        "writing rules text", "template text", max_rules_per_call=2,
    )
    assert first.ok

    from mfdoc.batch import _output_subdir, plan_batch

    out_dir = Path(tempfile.mkdtemp())
    subdir = _output_subdir(conn, "FAKEMOD")
    out_path = out_dir / subdir / "FAKEMOD.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for src in Path(str(first.path)).parent.glob("FAKEMOD.chunk*.md"):
        (out_path.parent / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    state_key = f"{subdir.as_posix()}/FAKEMOD"
    state = {state_key: {"ok": True, "chunks": first.chunk_state}}
    state_path = out_dir / "state.json"
    import json as _json

    state_path.write_text(_json.dumps(state), encoding="utf-8")

    # member_cache_capable=False (the default) must now see every chunk as
    # needing a re-render -- its hash no longer matches a state file that
    # was actually produced with the member-level prefix prepended.
    plan_mismatched = plan_batch(conn, ["FAKEMOD"], out_dir, state_path=state_path, max_rules_per_call=2)
    assert plan_mismatched.members[0].chunks_reusable == 0

    plan_matched = plan_batch(
        conn, ["FAKEMOD"], out_dir, state_path=state_path, max_rules_per_call=2,
        member_cache_capable=True,
    )
    assert plan_matched.members[0].chunks_reusable == 3
