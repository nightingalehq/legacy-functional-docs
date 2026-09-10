"""Issue #159: build_prompt/build_reconciliation_prompt's stable-prefix
helpers, and run_batch's wiring of them into any caller that opts in via
set_cache_prefixes. No real network call or `anthropic` package is needed
here -- these guard the pure-Python prompt-splitting logic in batch.py
itself; test_anthropic_caller.py/test_vertex_caller.py guard the callers
that actually consume it.
"""

from __future__ import annotations

from mfdoc.batch import (
    _apply_cache_prefixes,
    build_prompt,
    build_prompt_cache_prefix,
    build_prompt_parts,
    build_reconciliation_prompt,
    build_reconciliation_prompt_cache_prefix,
    build_reconciliation_prompt_parts,
)


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
