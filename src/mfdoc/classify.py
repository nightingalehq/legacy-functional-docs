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

# A built-in fallback taxonomy used only when a project hasn't declared its
# own `options.overview.themes.taxonomy` (see `taxonomy_from_options`) --
# the same replace-not-merge convention `conditions.OUTCOME_FIELD`/
# `outcome_field_from_options` and `conditions.DISPATCH_FIELD`/
# `dispatch_field_from_options` already establish for a built-in default
# that a project can fully override. These are generalizable business-rule
# vocabulary and structural shapes -- common validation wording, an
# outcome/return-code or status/flag field being set or compared, a
# message field being populated, a "no rows found" gap phrase mfdoc itself
# generates, or a generic arithmetic verb -- not a guess at any specific
# client's field names (issue #172). A project whose codebase uses
# different conventions still supplies its own complete taxonomy to
# replace this one, per `options.overview.themes.taxonomy`'s own doc
# comment in project.yml.
#
# Identifier-shaped patterns (RETURN-CODE, STATUS, MSG, ...) use a custom
# `(?<![A-Za-z0-9])...(?![A-Za-z0-9])` boundary instead of `\b` on purpose:
# `\b` treats underscore as a word character, so it would fail to isolate
# `STATUS` inside an underscore-separated name like `SCHED_STATUS` (seen in
# this repo's own Mantis-style fixtures) even though it correctly isolates
# the hyphen-separated `ORDER-STATUS` Natural convention -- the lookaround
# form treats hyphen and underscore the same way, matching both naming
# styles without picking one dialect's convention over the other.
DEFAULT_TAXONOMY: dict[str, list[str]] = {
    "validation": [
        r"\b(required|invalid|missing|mandatory|not\s+valid|must\s+not|could\s+not)\b",
        r"\bnot\s+found\b",
        # a comparison against a blank/space literal -- a common
        # required-field check shape regardless of field name
        r"(?:=|<>)\s*['\"]\s*['\"]",
    ],
    "error-handling": [
        r"(?<![A-Za-z0-9])(?:RETURN[-_]?CODE|RESPONSE[-_]?CODE|RESP[-_]?CODE|"
        r"ERROR[-_]?CODE|RET[-_]?CODE)(?![A-Za-z0-9])",
        r"(?<![A-Za-z0-9])RC(?![A-Za-z0-9])",
    ],
    "status": [
        r"(?<![A-Za-z0-9])(?:STATUS|STAT|FLAG)(?![A-Za-z0-9])",
    ],
    "messaging": [
        r"(?<![A-Za-z0-9])(?:MSG|MESSAGE)(?![A-Za-z0-9])",
    ],
    "data-access": [
        # the "no records found for preceding database loop" phrasing
        # mfdoc's own extraction records for an empty database loop --
        # generic tool-generated gap text, not client content
        r"\bno\s+records?\s+found\b",
    ],
    "calculation": [
        r"\b(?:ADD|SUBTRACT|MULTIPLY|DIVIDE|COMPUTE)\b",
    ],
}


def taxonomy_from_options(options: dict | None) -> dict[str, list[str]]:
    """The taxonomy to use for `classify_rules_deterministic`, from
    `options.overview.themes.taxonomy` in project.yml, or the built-in
    `DEFAULT_TAXONOMY` if unset/empty.

    Mirrors `conditions.outcome_field_from_options`/
    `dispatch_field_from_options` exactly: a declared taxonomy is used
    exactly as given, never merged with the built-in one -- a project
    whose business vocabulary doesn't match `DEFAULT_TAXONOMY`'s themes
    supplies its own complete taxonomy rather than getting default themes
    mixed in alongside its own.
    """
    themes = ((options or {}).get("overview") or {}).get("themes") or {}
    declared = themes.get("taxonomy")
    if declared:
        return declared
    return DEFAULT_TAXONOMY


def classify_rules_deterministic(conn, taxonomy: dict[str, list[str]]) -> dict:
    """Classify every rule_candidate not already keyword- or llm-classified
    (this includes rows currently classified 'structural', so a rerun after
    a taxonomy edit can promote them): keyword match against `taxonomy`
    first, else a structural fallback (the rule's member's library, or
    'uncategorized' if library is NULL).

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

# One progress line every N rows rather than going silent until the whole
# pass finishes -- progress is still reported per-row (see
# classify_rules_llm's docstring) even though rows are now sent to the
# model in groups (_DEFAULT_LLM_BATCH_SIZE below), since a caller cares
# about "how far through the rule set are we", not "how many HTTP calls
# has this made".
_PROGRESS_INTERVAL = 10

# How many rule_candidate rows are asked about in a single model call.
# One call per row (the original design) is cheap in tokens but pays a
# fixed per-call latency overhead every single time -- on a real
# project's rule count that dominates wall-clock time even though the
# tokens involved are trivial (see issue #167). Batching trades a larger,
# still-cheap prompt for far fewer calls. Sized to the same order as
# batch.py's DEFAULT_MAX_RULES_PER_CALL for consistency, not because the
# two have any other relationship.
_DEFAULT_LLM_BATCH_SIZE = 25


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


def _build_batch_prompt(items: list[tuple[int, str, str, str]]) -> str:
    """Build one prompt asking for a theme for every row in `items` at
    once, instead of issuing one call per row. Each item is
    `(rc_id, member_name, condition, literals)` -- already redacted by
    the caller.

    The expected response format is one line per rule, `"<id>: <theme>"`,
    where `<id>` is the row's own `rule_candidate.id` (not its position in
    the batch) -- so a response can be matched back to the right row even
    if the model reorders, skips, or only partially answers, and so a
    stray/garbled line can never be misattributed to the wrong row.
    """
    lines = [
        "For each numbered rule below, reply with exactly one short "
        "lowercase business-theme word (e.g. eligibility, posting, "
        "validation) -- one line per rule, in the exact format "
        '"<id>: <theme>" where <id> is the number shown before each '
        "rule below. Reply with nothing else -- no preamble, no blank "
        "lines, no commentary.",
        "",
    ]
    for rc_id, member_name, condition, literals in items:
        lines.append(
            f"{rc_id}: module={member_name} condition={condition!r} literals={literals!r}"
        )
    return "\n".join(lines)


_BATCH_RESPONSE_LINE = re.compile(r"^\s*(\d+)\s*[:.\-]\s*(.+?)\s*$")


def _parse_batch_response(text: str, expected_ids: list[int]) -> dict[int, str | None]:
    """Parse a batched response back into `{rc_id: raw_theme_text}`.

    Every id in `expected_ids` is present in the returned dict; an id
    with no matching line in `text` (missing, garbled, or the model used
    a different id) maps to `None` -- the caller leaves that row's
    classification untouched (still 'structural'), exactly as an
    individual failed/empty call does today, rather than guessing or
    letting one bad line disturb any other row in the same batch. A line
    naming an id outside this batch is ignored rather than accepted,
    since it can't be attributed to any row this call was actually asked
    about.
    """
    expected = set(expected_ids)
    result: dict[int, str | None] = dict.fromkeys(expected_ids, None)
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        match = _BATCH_RESPONSE_LINE.match(line)
        if not match:
            continue
        rc_id = int(match.group(1))
        if rc_id not in expected:
            continue
        result[rc_id] = match.group(2).strip().lower()
    return result


def classify_rules_llm(
    conn,
    caller: ModelCaller,
    redact: Redactor = NULL_REDACTOR,
    taxonomy: dict[str, list[str]] | None = None,
    limit: int | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    batch_size: int | None = None,
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
    project's rule count. The underlying query is `ORDER BY rc.member_id,
    rc.line_no` (matching the ordering convention used elsewhere for
    rule_candidate, e.g. structural.py's rule sequence-numbering query)
    specifically so `limit`'s row selection is deterministic and
    reproducible run-to-run -- an unordered SELECT sliced by `[:limit]`
    would otherwise cap an implementation-defined subset that could
    differ between runs with no source change.

    Rows are sent to the model in groups of `batch_size` (default
    `_DEFAULT_LLM_BATCH_SIZE`) rather than one call per row -- one call
    now asks for `batch_size` themes at once (see `_build_batch_prompt`),
    cutting call count by roughly that factor, which is what actually
    dominates wall-clock time on a real project's rule count (see issue
    #167; the per-row prompt is cheap in tokens but each call still pays
    a fixed latency overhead). Every check that used to apply to the
    single response for a row -- taxonomy matching, refusal/non-answer
    rejection, "can't confidently theme stays structural" -- still
    applies per-row against a batched response (`_parse_batch_response`),
    not just to a single top-line answer: a missing, garbled, or
    wrongly-id'd line for one row in a batch leaves only that row
    untouched (still 'structural') and never disturbs any other row in
    the same batch.

    Progress and commit granularity stay per-row, not per-batch: every
    `_PROGRESS_INTERVAL`th row (and the last row) invokes
    `progress_callback(i, total)` if one is given -- this module is
    library code, not the CLI, so it never prints directly (see
    batch.py/structural.py for the same convention); `cmd_classify_rules`
    in cli.py passes a callback that does the actual printing. Omitting
    the callback produces no output at all, just the return value below.
    The returned dict reports `input_tokens`/`output_tokens` accumulated
    from each call's `ModelResponse` (previously discarded) alongside the
    reclassified count, so a caller can report usage/cost the same way
    `mfdoc batch` does, plus `unparsed` -- how many rows had no usable
    line in their batch's response, the batched equivalent of an
    individual failed/empty call.
    """
    taxonomy_lookup = {theme.lower(): theme for theme in taxonomy} if taxonomy else None
    size = batch_size if batch_size is not None else _DEFAULT_LLM_BATCH_SIZE
    query = """
        SELECT rc.id, rc.condition, rc.literals, m.name AS member_name
          FROM rule_candidate rc
          JOIN member m ON m.id = rc.member_id
          JOIN rule_theme rt ON rt.rule_candidate_id = rc.id
         WHERE rt.source = 'structural'
         ORDER BY rc.member_id, rc.line_no
    """
    if limit is not None:
        # Pushed into SQL rather than fetching every eligible row and
        # slicing in Python -- a large project's full structural-sourced
        # set shouldn't be loaded into memory just to honor a small cap.
        rows = conn.execute(query + " LIMIT ?", (limit,)).fetchall()
    else:
        rows = conn.execute(query).fetchall()

    reclassified = 0
    unparsed = 0
    input_tokens = 0
    output_tokens = 0
    total = len(rows)
    i = 0
    for batch_start in range(0, total, size):
        batch_rows = rows[batch_start : batch_start + size]
        items = [
            (row["id"], row["member_name"], redact(row["condition"]) or "", redact(row["literals"]) or "")
            for row in batch_rows
        ]
        prompt = _build_batch_prompt(items)
        response = caller(prompt)
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        parsed = _parse_batch_response(response.text, [row["id"] for row in batch_rows])

        for row in batch_rows:
            i += 1
            full_theme = parsed.get(row["id"])
            if not full_theme:
                unparsed += 1
            else:
                if taxonomy_lookup is not None:
                    theme = taxonomy_lookup.get(full_theme)
                    accepted = theme is not None
                elif _looks_like_a_refusal_or_non_answer(full_theme):
                    accepted = False
                else:
                    theme = full_theme[:40]
                    accepted = True
                if accepted:
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
        "unparsed": unparsed,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
