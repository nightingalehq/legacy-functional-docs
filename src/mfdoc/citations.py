"""Citation formatting helpers for fact briefs and structural renderers.

Shared utilities for creating stable rule identifiers and source-line citations
across the documentation pipeline.
"""

from __future__ import annotations


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
