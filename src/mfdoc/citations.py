"""Citation formatting helpers for fact briefs and structural renderers.

Shared utilities for creating stable rule identifiers and source-line citations
across the documentation pipeline.
"""

from __future__ import annotations

from typing import Iterable, Iterator


def numbered_rule_candidates(rows: Iterable) -> Iterator[tuple[int, object]]:
    """Pair each already-fetched `rule_candidate` row with its 1-based
    `BR-nnn` ordinal (see `_rule_id`) -- the single place this assignment
    happens, so every consumer of it (`brief.module_brief`'s "Candidate
    business rules"/copycode-rules sections and `rules_register`,
    `structural.thematic_rules_register`, `testplan.build_member_test_cases`'
    scenario naming, `validate.module_completeness_problems`'
    missing-citation reporting, `batch`'s chunk-boundary routine mapping)
    gets the identical `n` for the identical row without independently
    reimplementing `enumerate(rows, start=1)`.

    `rows` must already be exactly one member's `rule_candidate` rows, in
    the order `_rule_id`'s ordinal is defined against -- source-line order
    (`ORDER BY line_no`; see `brief.fetch_rule_candidate_rows`). This
    function does no querying or reordering of its own: it is deliberately
    just the "assign ordinals to already-fetched rows" step, so a caller
    that already holds those rows for some other reason (rendering a
    section, a bulk multi-member fetch for `rules_register`/
    `thematic_rules_register`, ...) never needs a second query just to get
    the ordinal.
    """
    return enumerate(rows, start=1)


def _rule_id(member_name: str, n: int) -> str:
    """A stable handle for one rule candidate, e.g. `MMP0100:BR-003`.

    Qualified with the member name so it is unique across the whole system,
    not just within one module's doc -- an unqualified `BR-003` would mean a
    different rule in every module that has one. Numbered in the order
    rules appear in that member's own brief, which is itself ordered by
    source line, so for unchanged source, re-running the pipeline produces
    the same IDs. This is a positional scheme, not a content hash:
    inserting a new rule earlier in the source shifts every later ID in
    that module, the same trade-off any sequential numbering makes. See
    reference/writing-rules.md."""
    return f"{member_name}:BR-{n:03d}"


def _cite(name: str, line: int | None, end: int | None = None) -> str:
    """Format a source-line citation reference for a member.

    Returns a citation string in the form [[name]], [[name:line]], or
    [[name:line-end]] for ranges. Citations are resolved by the validate stage
    against the fact store."""
    if line is None:
        return f"[[{name}]]"
    if end and end != line:
        return f"[[{name}:{line}-{end}]]"
    return f"[[{name}:{line}]]"
