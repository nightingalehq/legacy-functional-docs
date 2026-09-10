"""Issue #168: testbatch.py's build_test_prompt_parts/build_test_prompt_cache_prefix
helpers, and run_test_batch's wiring of them into any caller that opts in via
set_cache_prefixes -- the same pattern issue #159 built for batch.py's
build_prompt/build_reconciliation_prompt, extended here to the sibling
test-generation narrate path. No real network call or `anthropic` package is
needed here -- these guard the pure-Python prompt-splitting logic in
testbatch.py itself; test_anthropic_caller.py/test_vertex_caller.py guard the
callers that actually consume it.
"""

from __future__ import annotations

from mfdoc.testbatch import (
    _apply_test_cache_prefix,
    build_test_prompt,
    build_test_prompt_cache_prefix,
    build_test_prompt_parts,
)


def test_build_test_prompt_is_exactly_its_own_parts_joined():
    """Single source of truth: build_test_prompt must never diverge from
    "\\n\\n---\\n\\n".join(build_test_prompt_parts(...))."""
    brief, rules, template = "a test brief", "some writing rules", "a template"
    assert build_test_prompt(brief, rules, template, "python", "pytest") == "\n\n---\n\n".join(
        build_test_prompt_parts(brief, rules, template, "python", "pytest")
    )
    assert build_test_prompt(brief, rules, template, "python", "pytest", "retry note") == "\n\n---\n\n".join(
        build_test_prompt_parts(brief, rules, template, "python", "pytest", "retry note")
    )


def test_build_test_prompt_cache_prefix_is_a_true_prefix_of_build_test_prompt():
    """The whole point: AnthropicCaller/VertexCaller must be able to find
    this prefix with a plain `str.startswith`, no re-parsing."""
    rules, template = "some writing rules", "a template"
    prefix = build_test_prompt_cache_prefix(rules, template, "python", "pytest")
    prompt = build_test_prompt("a test brief", rules, template, "python", "pytest")
    assert prompt.startswith(prefix)
    # And the prefix stops exactly where the test brief starts.
    assert prompt[len(prefix):].startswith("# Test brief\n\n")


def test_build_test_prompt_cache_prefix_is_identical_across_different_briefs_and_retries():
    """The entire caching win depends on this prefix being byte-identical
    across every chunk/member/retry sharing one writing_rules/template/
    language/framework -- only the brief (and retry note) may vary."""
    rules, template = "some writing rules", "a template"
    prefix = build_test_prompt_cache_prefix(rules, template, "python", "pytest")
    prompt_one_member = build_test_prompt("brief for member one", rules, template, "python", "pytest")
    prompt_another_member = build_test_prompt("a totally different brief", rules, template, "python", "pytest")
    prompt_on_retry = build_test_prompt(
        "brief for member one", rules, template, "python", "pytest", "fix your citations"
    )
    assert prompt_one_member.startswith(prefix)
    assert prompt_another_member.startswith(prefix)
    assert prompt_on_retry.startswith(prefix)


def test_build_test_prompt_cache_prefix_changes_when_writing_rules_or_template_change():
    """A prefix computed for one project's writing_rules/template must not
    be mistaken for a match against a different project's -- the retained
    behavior here is just plain string equality/startswith, no cross-project
    leakage risk, but worth pinning down explicitly."""
    prefix_a = build_test_prompt_cache_prefix("rules A", "template A", "python", "pytest")
    prefix_b = build_test_prompt_cache_prefix("rules B", "template B", "python", "pytest")
    assert prefix_a != prefix_b


def test_build_test_prompt_cache_prefix_changes_when_language_or_framework_change():
    """Unlike batch.py's build_prompt (whose instructions text never varies),
    build_test_prompt's instructions section is templated on
    language/framework -- so a `--matrix` run's different targets must not
    collide on the same cache prefix even with identical writing_rules/
    template."""
    rules, template = "some writing rules", "a template"
    prefix_python = build_test_prompt_cache_prefix(rules, template, "python", "pytest")
    prefix_java = build_test_prompt_cache_prefix(rules, template, "java", "junit")
    assert prefix_python != prefix_java


class _CacheAwareFakeCaller:
    """Stands in for AnthropicCaller/VertexCaller's opt-in surface without
    depending on either module -- only `set_cache_prefixes` matters here."""

    def __init__(self):
        self.registered: list[str] | None = None

    def set_cache_prefixes(self, prefixes) -> None:
        self.registered = list(prefixes)

    def __call__(self, prompt):  # pragma: no cover - not exercised by these tests
        raise NotImplementedError


def test_apply_test_cache_prefix_registers_the_prefix_on_an_opted_in_caller():
    caller = _CacheAwareFakeCaller()
    _apply_test_cache_prefix(caller, "some writing rules", "a template", "python", "pytest")
    assert caller.registered == [
        build_test_prompt_cache_prefix("some writing rules", "a template", "python", "pytest"),
    ]


def test_apply_test_cache_prefix_is_a_no_op_for_a_caller_without_the_hook():
    """ClaudeCLICaller and the fake-echo test caller have no
    `set_cache_prefixes` method at all -- this must not raise, and must not
    add one either (that would silently change what `hasattr` sees on a
    later call elsewhere)."""

    def plain_caller(prompt):  # pragma: no cover - never called
        raise NotImplementedError

    _apply_test_cache_prefix(plain_caller, "rules", "template", "python", "pytest")
    assert not hasattr(plain_caller, "set_cache_prefixes")
