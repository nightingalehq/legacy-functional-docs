"""Rule-theme classification: assigns each `rule_candidate` a business
theme for the thematic rules-register rollup (structural.py).

Three-layer fallback, run in this order, never as three independent
passes: (1) a project-defined keyword/regex taxonomy (deterministic,
always runs first, see classify_rules_deterministic); (2) an optional
LLM pass over whatever the taxonomy didn't match (classify_rules_llm,
Task 3); (3) a structural fallback -- the rule's own member's library
-- for anything still unclassified once the first two layers have run.
Every rule ends up classified; nothing is silently dropped.
"""

from __future__ import annotations

import re
from typing import Callable

from .batch import ModelCaller
from .redact import NULL_REDACTOR, Redactor


def classify_rules_deterministic(conn, taxonomy: dict[str, list[str]]) -> dict:
    """Classify every rule_candidate not already in rule_theme: keyword
    match against `taxonomy` first, else a structural fallback (the
    rule's member's library, or 'uncategorized' if library is NULL).

    Upserts on rule_candidate_id -- calling this again after a taxonomy
    edit reclassifies rows whose current source is 'structural' (never
    already keyword- or llm-classified) rather than duplicating them.
    """
    patterns = {
        theme: [re.compile(p, re.IGNORECASE) for p in patterns]
        for theme, patterns in taxonomy.items()
    }
    rows = conn.execute(
        """
        SELECT rc.id, rc.condition, rc.literals, m.library
          FROM rule_candidate rc
          JOIN member m ON m.id = rc.member_id
         WHERE NOT EXISTS (
             SELECT 1 FROM rule_theme rt
              WHERE rt.rule_candidate_id = rc.id AND rt.source IN ('keyword', 'llm')
         )
        """
    ).fetchall()

    counts = {"keyword": 0, "structural": 0}
    for row in rows:
        haystack = f"{row['condition'] or ''} {row['literals'] or ''}"
        theme = None
        for name, regexes in patterns.items():
            if any(rx.search(haystack) for rx in regexes):
                theme = name
                break
        if theme is not None:
            source = "keyword"
            counts["keyword"] += 1
        else:
            theme = row["library"] or "uncategorized"
            source = "structural"
            counts["structural"] += 1
        conn.execute(
            "INSERT INTO rule_theme (rule_candidate_id, theme, source) VALUES (?, ?, ?) "
            "ON CONFLICT(rule_candidate_id) DO UPDATE SET theme=excluded.theme, source=excluded.source",
            (row["id"], theme, source),
        )
    conn.commit()
    return counts


_REFUSAL_MARKERS = ("cannot", "can't", "unable", "don't know", "do not know", "i'm not sure", "not sure")
_MAX_THEME_WORDS = 6

# Rows are committed in batches of this size rather than only once at the
# end -- if `caller` raises partway through a large run (a real, expected
# failure mode: network error, rate limit, ...), every row already
# classified before the raise stays committed instead of being lost.
_COMMIT_BATCH_SIZE = 20

# One serial model call per row is slow and easy to lose track of on a
# real project's rule count -- print a running progress line every N rows
# rather than going silent until the whole pass finishes.
_PROGRESS_INTERVAL = 10


def _looks_like_a_refusal_or_non_answer(theme: str) -> bool:
    """A genuine theme is a short label (eligibility, posting,
    validation, ...), not a sentence -- a refusal like "I cannot
    determine a theme" or "I don't know" must not be stored verbatim as
    if it were one."""
    if any(marker in theme for marker in _REFUSAL_MARKERS):
        return True
    if len(theme.split()) > _MAX_THEME_WORDS:
        return True
    return False


def classify_rules_llm(
    conn,
    caller: ModelCaller,
    redact: Redactor = NULL_REDACTOR,
    taxonomy: dict[str, list[str]] | None = None,
    limit: int | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """Ask the model for a one-word theme for every rule_candidate still
    classified 'structural' (the keyword taxonomy didn't match it).
    Upserts source='llm' for each; a rule the model can't confidently
    theme is left at its existing structural label -- never guessed past
    what the model actually returned.

    "Can't confidently theme" is enforced two ways, not just on empty
    text: if `taxonomy` is given, only a theme whose lowercase form
    matches one of `taxonomy`'s own keys (case-insensitively) is
    accepted -- anything else stays 'structural', so a constrained
    project can't have its taxonomy silently bypassed by free-form LLM
    text. If `taxonomy` is omitted (free-form theming is intended), any
    non-empty short theme is still accepted as before, but a
    refusal/non-answer (e.g. "I cannot determine a theme", or anything
    longer than a short label) is rejected rather than stored verbatim as
    if it were a genuine theme.

    When a taxonomy is given and the model's response matches one of its
    keys case-insensitively, the taxonomy's own key string (exact
    original casing) is stored, not the model's (lowercased) text --
    otherwise a capitalized taxonomy key (e.g. "Posting") would split
    into two theme groups: one from classify_rules_deterministic's
    verbatim key and one from this function's lowercased text. The match
    against taxonomy keys is done on the model's *full*, untruncated
    response -- truncating to 40 characters first (as the free-form
    stored-value path still does, since there's no fixed-length key to
    match there) would make a taxonomy key longer than 40 characters
    unmatchable.

    `limit`, if given, caps how many eligible rows are sent to the model
    in this call -- useful for bounding a first run against an unknown
    project's rule count. Every `_PROGRESS_INTERVAL`th row (and the last
    row) invokes `progress_callback(i, total)` if one is given -- this
    module is library code, not the CLI, so it never prints directly
    (see batch.py/structural.py for the same convention); `cmd_classify_
    rules` in cli.py passes a callback that does the actual printing.
    Omitting the callback produces no output at all, just the return
    value below. The returned dict reports `input_tokens`/`output_tokens`
    accumulated from each call's `ModelResponse` (previously discarded)
    alongside the reclassified count, so a caller can report usage/cost
    the same way `mfdoc batch` does.
    """
    taxonomy_lookup = {theme.lower(): theme for theme in taxonomy} if taxonomy else None
    rows = conn.execute(
        """
        SELECT rc.id, rc.condition, rc.literals, m.name AS member_name
          FROM rule_candidate rc
          JOIN member m ON m.id = rc.member_id
          JOIN rule_theme rt ON rt.rule_candidate_id = rc.id
         WHERE rt.source = 'structural'
        """
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]

    reclassified = 0
    input_tokens = 0
    output_tokens = 0
    total = len(rows)
    for i, row in enumerate(rows, start=1):
        condition = redact(row["condition"]) or ""
        literals = redact(row["literals"]) or ""
        prompt = (
            "Reply with exactly one short lowercase business-theme word "
            f"(e.g. eligibility, posting, validation) for this rule from "
            f"module {row['member_name']}: condition={condition!r} literals={literals!r}"
        )
        response = caller(prompt)
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        lines = response.text.strip().lower().splitlines()
        if not lines:
            continue
        full_theme = lines[0]
        if not full_theme:
            continue
        if taxonomy_lookup is not None:
            if full_theme not in taxonomy_lookup:
                continue
            theme = taxonomy_lookup[full_theme]
        else:
            theme = full_theme[:40]
            if _looks_like_a_refusal_or_non_answer(theme):
                continue
        conn.execute(
            "UPDATE rule_theme SET theme=?, source='llm' WHERE rule_candidate_id=?",
            (theme, row["id"]),
        )
        reclassified += 1
        if i % _COMMIT_BATCH_SIZE == 0:
            conn.commit()
        if progress_callback is not None and (i % _PROGRESS_INTERVAL == 0 or i == total):
            progress_callback(i, total)
    conn.commit()
    return {
        "reclassified": reclassified,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
