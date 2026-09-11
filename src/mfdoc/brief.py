"""Stage 3 input — fact briefs.

The narrative pass never reads raw source directly for its assertions. It reads a
brief generated from the fact store, in which every line already carries a
citation. This is the mechanism that makes "no uncited claims" enforceable rather
than aspirational: if a fact is not in the brief, there is nothing to cite, and
the writing instructions require the claim to be dropped or marked `unresolved`.

Source excerpts are included for the *rule candidates only*, because paraphrasing
a business rule without seeing its exact condition is where invention creeps in.
"""

from __future__ import annotations

import bisect
import json
import re
import statistics
from dataclasses import dataclass

from .citations import _cite, _rule_id, numbered_rule_candidates
from .db import GAP_SEVERITY_ORDER_SQL
from .redact import NULL_REDACTOR, Redactor
from .sme_notes import Notes, notes_for


def _sme_notes_section(redact: Redactor, sme_notes: Notes | None, name: str | None) -> list[str]:
    """Lines for the advisory-only "SME notes" section shared by
    module_brief/entity_brief/executive_brief/test_case_brief, or `[]` when
    there's nothing to say for `name` (no `sme_notes` given, or no general/
    named section matches it).

    Deliberately its own heading, at the *end* of the brief, in plain prose
    -- never `[[MEMBER:LINE]]`-cited like everything else here, and never
    mixed into a section a model might read as sourced fact. This is the
    one thing a human types free-hand into `sme-notes.md` without it being
    checked against the fact store, so it must stay visually and
    structurally distinct from every cited section above it: interpretation
    and emphasis only, never itself a citable source, never an override of
    what the fact store says. See `reference/writing-rules.md`'s "SME
    notes" rule for the corresponding instruction to the narrative pass."""
    if not sme_notes:
        return []
    text = notes_for(sme_notes, name)
    if not text:
        return []
    return [
        "## SME notes (human-provided context, not verified against source)",
        "",
        "These notes were typed by a human SME, not derived from source. They "
        "may help with interpretation and emphasis, but they are never a "
        "citable source -- every business-rule claim in the generated "
        "document must still carry its own line citation from the sections "
        "above, exactly as if this section didn't exist.",
        "",
        redact(text),
        "",
    ]


def _copycode_rule_candidates(conn, mid: int, _seen: set | None = None) -> list[tuple[int, str, list]]:
    """Transitively collect rule_candidate rows from copycode this member
    includes -- directly, or via copycode that itself includes further
    copycode. Only follows resolved INCLUDE edges into members whose
    object_type is 'copycode'; a DEFINE DATA USING an LDA/PDA, or an INCLUDE
    of a map or screen, pulls in variables or interaction points, not
    business rules attributed the same way, so those are left alone here."""
    seen = _seen if _seen is not None else set()
    out: list[tuple[int, str, list]] = []
    targets = conn.execute(
        """
        SELECT DISTINCT m.id, m.name FROM call_edge ce
          JOIN member m ON m.id = ce.callee_id
         WHERE ce.caller_id=? AND ce.call_kind='INCLUDE' AND ce.resolved=1
           AND m.object_type='copycode'
         ORDER BY m.name
        """,
        (mid,),
    ).fetchall()
    for t in targets:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        rules = conn.execute(
            "SELECT * FROM rule_candidate WHERE member_id=? ORDER BY line_no", (t["id"],)
        ).fetchall()
        if rules:
            out.append((t["id"], t["name"], rules))
        out.extend(_copycode_rule_candidates(conn, t["id"], seen))
    return out


def _branch_data_access(acc_rows: list, acc_line_nos: list[int], name: str,
                         start_line: int, end_line: int) -> str | None:
    """Compact citation list of every data_access row (from `acc_rows` --
    this member's full `data_access` result set, already fetched once by
    `build_member_facts`/`module_brief`; see issue #183) strictly between
    `start_line` and `end_line` (inclusive of end_line, exclusive of
    start_line -- the construct's own line) -- attached to an IF/ELSE
    rule's bullet so a GET/UPDATE/DELETE inside that specific branch is
    impossible to miss when writing the branch up, rather than only
    appearing, uncorrelated, in the brief's separate "Data access"
    section. None when nothing falls in range.

    Looks up the matching slice via `bisect` on `acc_line_nos` (the same
    `acc_rows`, already ordered by `line_no` -- module_brief's own "Data
    access" query -- with just its `line_no` column pulled out once,
    ahead of the per-rule loop, purely so `bisect` has something to search)
    rather than a full linear scan of every data_access row per rule: with
    R branch rules and A data_access rows, a per-rule linear scan is
    O(R*A); this is O(R*log A) to find each slice's bounds plus O(k) to
    read the k rows actually in range -- avoiding both a fresh
    `conn.execute` per rule (the original issue #183 finding) and an
    equivalent-cost Python scan standing in for it."""
    lo = bisect.bisect_right(acc_line_nos, start_line)
    hi = bisect.bisect_right(acc_line_nos, end_line)
    rows = acc_rows[lo:hi]
    if not rows:
        return None
    return "; ".join(
        f"`{r['verb']}` on `{r['entity_name'] or 'UNKNOWN'}` {_cite(name, r['line_no'])}" for r in rows
    )


def fetch_routines(conn, member_id: int) -> list[dict]:
    """This member's routine rows (db.py's `routine` table -- Natural's
    DEFINE SUBROUTINE/END-SUBROUTINE, Mantis's ENTRY name/EXIT), ordered by
    start_line. The grouping every routine-aware consumer builds from:
    module_brief's per-routine rule headings and batch.py's chunk-boundary
    computation (chunk on a routine boundary, never split one routine's
    rules across two chunks)."""
    rows = conn.execute(
        "SELECT * FROM routine WHERE member_id=? ORDER BY start_line", (member_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# construct names (first token) that open a block with a body -- an IF/
# ELSE/DECIDE/FOR/REPEAT/WHILE/ON ERROR/AT-event's own rule_candidate row
# can legitimately have `end_line IS NULL` and still enclose later lines
# (see natural.py's `_match_rules`: only IF/ELSE ever get `end_line` filled
# in via their own END-IF -- DECIDE/FOR/REPEAT/ON ERROR/AT-EVENT open a
# real block too but never get their own end_line resolved at all today).
# A statement-shaped rule_candidate (MOVE, COMPUTE, ADD, ASSIGN, VALUE,
# WHEN, ESCAPE, REJECT IF, NONE/ANY/ALL, ...) is a single line with no body
# of its own and *always* has `end_line IS NULL` -- treating that as
# "extends indefinitely" the same way an unresolved block extent does
# misattributes any later call to the last statement seen, instead of its
# real enclosing IF/WHILE/etc. (issue #151 follow-up).
_BLOCK_CONSTRUCT_FIRST_WORDS = {"IF", "ELSE", "DECIDE", "FOR", "REPEAT", "WHILE", "ON", "AT", "CASE"}


def _opens_a_block(construct: str | None) -> bool:
    if not construct:
        return False
    return construct.split()[0].upper() in _BLOCK_CONSTRUCT_FIRST_WORDS


# A generic, dialect-agnostic "this line closes a block" shape, covering
# both text-based dialects this repo currently supports: Natural's
# `END-<CONSTRUCT>` and Mantis's bare `END`. Used only as a heuristic bound
# on how far an unresolved block extent (`_opens_a_block` true, `end_line`
# still NULL) can be trusted to reach -- DECIDE/FOR/REPEAT/ON ERROR/AT-EVENT
# never get their own `end_line` backfilled today (only IF/ELSE do, via
# `natural.py`'s `if_rule_ids`/`else_rule_ids`), so without this check a
# call *after* such a block's real end would still read as enclosed by it
# (Copilot review on PR #151).
#
# Deliberately narrower than "any END-<WORD> line": natural.py's own
# `_END_TO_OPENERS` is the authority on which END-forms actually pop an
# open_blocks entry -- END-FIND/END-READ/END-HISTOGRAM/END-WORK/END-ALL/
# END-SUBROUTINE/END-BEFORE/END-PROCESS never do (FIND/READ/HISTOGRAM don't
# even push onto open_blocks at all -- see that dict's own comment), so
# matching them here would let a data-access loop's own closing line get
# mistaken for closing an unrelated, still-open DECIDE/FOR/etc. around it.
# This list is exactly `_END_TO_OPENERS`'s keys, spelled out as the actual
# END-<CONSTRUCT> keyword each one closes.
_BLOCK_CLOSE_LINE_RE = re.compile(
    r"^\s*(END-(IF|DECIDE|FOR|REPEAT|ERROR|BREAK|ENDDATA|START|TOPPAGE|NOREC)|END)\s*$",
    re.IGNORECASE,
)


def _has_intervening_close(conn, member_id: int, rule_rows: list, opened_line: int,
                            line_no: int) -> bool:
    """Whether the block opened at `opened_line` has already closed by
    `line_no` -- *not* whether any `END*`-shaped line appears in between.
    A bare "any END-* line in range" check (an earlier version of this
    function) misattributes a *nested* block's own close (an inner IF's
    `END-IF` inside an outer, unresolved-extent DECIDE) as closing the
    outer block too, dropping real guard-scope context for everything after
    that inner close but still inside the outer one (Copilot review on PR
    #151).

    Mirrors the extractor's own `open_blocks` stack instead: walk every
    block-opening `rule_candidate` row strictly between `opened_line` and
    `line_no` as a nested "push", and every generic close-shaped source
    line (`_BLOCK_CLOSE_LINE_RE`) as a "pop" -- interleaved by line number,
    the same order the extractor itself sees them in. A pop while `depth>0`
    closes some nested block, not this one; a pop at `depth==0` is the
    first close that isn't accounted for by a nested opener already in
    range, so it closes *this* block."""
    nested_opens = [
        rc["line_no"] for rc in rule_rows
        if opened_line < rc["line_no"] <= line_no and _opens_a_block(rc["construct"])
    ]
    # Narrow to plausible close lines in SQL rather than fetching every
    # line in range and filtering all of it in Python -- an avoidable N*M
    # scan otherwise, since this runs once per unresolved-extent candidate
    # per preceding call. Tabs are normalised to spaces before the LIKE
    # check so a tab-indented "END..." line still matches the same way
    # `_BLOCK_CLOSE_LINE_RE`'s `\s*` does; the regex below still does the
    # exact match, this is only a pre-filter.
    close_rows = conn.execute(
        "SELECT line_no, text FROM source_line WHERE member_id=? AND line_no>? AND line_no<=?"
        " AND UPPER(TRIM(REPLACE(text, CHAR(9), ' '))) LIKE 'END%'"
        " ORDER BY line_no",
        (member_id, opened_line, line_no),
    ).fetchall()
    closes = [r["line_no"] for r in close_rows if _BLOCK_CLOSE_LINE_RE.match(r["text"] or "")]
    events = sorted([(ln, 0) for ln in nested_opens] + [(ln, 1) for ln in closes])
    depth = 0
    for _, is_close in events:
        if not is_close:
            depth += 1
        elif depth > 0:
            depth -= 1
        else:
            return True
    return False


def _enclosing_condition(conn, member_id: int, rule_rows: list, line_no: int) -> dict | None:
    """The innermost `rule_candidate` row (already fetched, any order) whose
    block encloses `line_no` -- `rc.line_no <= line_no` and either
    `rc.end_line >= line_no`, or `rc.end_line` is unresolved *and* `rc`
    actually opens a block (`_opens_a_block`) *and* no line between the
    opener and `line_no` looks like a block close (`_has_intervening_close`)
    -- kept as a candidate the same way `routine_for_line` treats an
    unresolved routine end: still evidence the block continues at least
    that far, absent evidence it already closed. A statement-shaped row
    (`_opens_a_block` false) never encloses anything, regardless of its own
    `end_line` -- it has no body to enclose with. Among matches, the one
    with the greatest `line_no` is innermost (nesting is strictly
    line-ordered here, the same assumption `routine_for_line` and
    `_branch_data_access` already make), so a call inside a nested IF
    reports that IF's own condition, not an outer one. Returns None when no
    row encloses `line_no` -- not proof the call is unconditional, only that
    this brief found no enclosing block for it (see `_caller_guard_chain`'s
    own wording for that distinction)."""
    match = None
    for rc in rule_rows:
        if rc["line_no"] > line_no:
            continue
        if rc["end_line"] is not None:
            if rc["end_line"] < line_no:
                continue
        else:
            if not _opens_a_block(rc["construct"]):
                continue
            if _has_intervening_close(conn, member_id, rule_rows, rc["line_no"], line_no):
                continue
        if match is None or rc["line_no"] > match["line_no"]:
            match = rc
    return match


def _caller_guard_chain_facts(conn, caller_id: int, call_line_no: int) -> list[dict]:
    """Raw (unredacted) facts for `_render_guard_chain`: for a callee
    reachable from exactly one call site, the caller's own `call_edge` rows
    strictly before that call (already ordered by `line_no`, per
    `call_edge`'s own citation ordering elsewhere in this module) -- the
    validation/confirmation/mode-gate calls the caller performs before ever
    reaching this one, which module_brief's own "Inbound callers" section
    previously dropped entirely by citing only the call line itself (issue
    #151). Each preceding call is paired with its innermost enclosing
    `rule_candidate` condition, when one exists, via `_enclosing_condition`
    -- read-only synthesis over facts already in the store, no new
    extraction. Returns `[]` when there is nothing preceding this call in
    its own caller.

    Deliberately returns raw rows, not rendered bullet lines: `condition`
    is raw source text that must go through the *caller's own* `redact` at
    render time (see `_render_guard_chain`), the same way every other
    condition in this module is redacted only when rendered, never when
    fetched -- `MemberFacts.guard_lines` (issue #183 review feedback) is
    built from this function's output plus whichever `redact` a given
    `module_brief()` call is actually using, rather than baking one
    `redact` in at fact-gathering time and risking a mismatch if a caller
    ever reuses a `MemberFacts` with a different `redact` than the one it
    was built with."""
    preceding = conn.execute(
        "SELECT * FROM call_edge WHERE caller_id=? AND line_no<? ORDER BY line_no",
        (caller_id, call_line_no),
    ).fetchall()
    if not preceding:
        return []
    rule_rows = conn.execute(
        "SELECT * FROM rule_candidate WHERE member_id=? ORDER BY line_no", (caller_id,)
    ).fetchall()
    facts = []
    for call in preceding:
        cond = _enclosing_condition(conn, caller_id, rule_rows, call["line_no"])
        facts.append({
            "callee": call["callee_name"] or "UNKNOWN",
            "call_kind": call["call_kind"],
            "call_line_no": call["line_no"],
            "condition": cond["condition"] if cond else None,
            "cond_line_no": cond["line_no"] if cond else None,
            "cond_construct": cond["construct"] if cond else None,
        })
    return facts


def _render_guard_chain(guard_facts: list[dict], caller_name: str, redact: Redactor) -> list[str]:
    """Render `_caller_guard_chain_facts`' raw rows into bullet lines,
    applying `redact` here -- at render time, using whichever `redact` this
    particular `module_brief()` call is using -- rather than at fact-gather
    time (see `_caller_guard_chain_facts`'s docstring for why)."""
    lines = []
    for f in guard_facts:
        callee = f["callee"]
        cite = _cite(caller_name, f["call_line_no"])
        if f["condition"]:
            lines.append(
                f"- when `{redact(f['condition'])}` holds {_cite(caller_name, f['cond_line_no'])}, "
                f"calls `{callee}` (`{f['call_kind']}`) {cite}"
            )
        elif f["cond_line_no"] is not None:
            # `_enclosing_condition` found a real enclosing block (a DECIDE
            # FOR CONDITION, an ELSE, ...) but that construct's own
            # `condition` text wasn't captured -- the call is still
            # control-flow scoped, just not by a condition this brief can
            # quote. Saying "unconditionally" here would be wrong, not
            # merely uninformative (Copilot review on PR #151).
            lines.append(
                f"- calls `{callee}` (`{f['call_kind']}`) {cite}, scoped inside "
                f"`{f['cond_construct']}` {_cite(caller_name, f['cond_line_no'])} "
                "(guard condition not captured)"
            )
        else:
            # No enclosing rule_candidate block found at all. That's
            # evidence this call sits in the caller's main line of
            # execution, not proof of it -- a dialect scanner gap or a
            # control-flow shape `_opens_a_block` doesn't recognise could
            # still be scoping it. Report the call itself and let the
            # absence of a guard line speak for it, rather than asserting
            # "unconditionally", which claims more than this brief can back
            # (Copilot review on PR #151).
            lines.append(f"- calls `{callee}` (`{f['call_kind']}`) {cite}")
    return lines


def routine_for_line(routines: list[dict], line_no: int) -> dict | None:
    """Which of `routines` (as returned by fetch_routines, ordered by
    start_line) contains `line_no`, or None when the line belongs to the
    member's main body -- outside every routine. A routine whose end_line
    is None (no matching END-SUBROUTINE/EXIT found -- see each dialect's
    _scan_routines) is treated as extending up to just before the next
    routine's start (or indefinitely, for the last one): an unresolved end
    is still evidence the routine's body continues at least that far, and
    treating it as zero-width would wrongly disown every fact inside it."""
    for i, r in enumerate(routines):
        end = r["end_line"]
        if end is None:
            end = routines[i + 1]["start_line"] - 1 if i + 1 < len(routines) else None
        if r["start_line"] <= line_no and (end is None or line_no <= end):
            return r
    return None


def routine_aware_chunk_ranges(rule_line_nos: list[int], routines: list[dict],
                                chunk_size: int) -> list[tuple[int, int]]:
    """1-based, inclusive `(start, end)` rule-ordinal ranges over
    `rule_line_nos` (already in source order, one entry per rule
    candidate), packing whole routines into each chunk rather than cutting
    every `chunk_size` rules regardless of structure.

    Consecutive rules sharing the same enclosing routine (via
    routine_for_line) form a contiguous run, since rules are already in
    line order and routines don't overlap or repeat -- these are kept
    whole no matter their size: a single routine with more rules than
    `chunk_size` becomes an oversized chunk by itself rather than being
    split, since keeping one routine's nested IF/DECIDE structure together
    for the model to narrate coherently matters more than an exact token
    budget. A run of rules belonging to *no* routine (the member's main
    body) has no such nested structure worth protecting, so it's the one
    thing still split into `chunk_size`-sized pieces before packing --
    without this, a member with no internal routines at all (every rule in
    the main body) would never chunk, defeating the point for exactly the
    members large enough to need it most.

    Runs (routine ones whole, main-body ones pre-split) are then packed
    greedily: keep adding the next run to the current chunk while doing so
    wouldn't exceed `chunk_size`; otherwise start a new chunk with it."""
    n = len(rule_line_nos)
    if n == 0:
        return []
    keys = [(routine_for_line(routines, ln) or {}).get("name") for ln in rule_line_nos]
    runs: list[tuple[int, int, str | None]] = []
    run_start = 0
    for idx in range(1, n + 1):
        if idx == n or keys[idx] != keys[run_start]:
            runs.append((run_start + 1, idx, keys[run_start]))  # 1-based, inclusive
            run_start = idx

    units: list[tuple[int, int]] = []
    for r_start, r_end, key in runs:
        if key is None:
            pos = r_start
            while pos <= r_end:
                piece_end = min(pos + chunk_size - 1, r_end)
                units.append((pos, piece_end))
                pos = piece_end + 1
        else:
            units.append((r_start, r_end))

    ranges: list[tuple[int, int]] = []
    cur: tuple[int, int] | None = None
    for u_start, u_end in units:
        if cur is None:
            cur = (u_start, u_end)
        elif (cur[1] - cur[0] + 1) + (u_end - u_start + 1) <= chunk_size:
            cur = (cur[0], u_end)
        else:
            ranges.append(cur)
            cur = (u_start, u_end)
    if cur is not None:
        ranges.append(cur)
    return ranges


def chunk_density_metrics(line_nos: list[int | None], ranges: list[tuple[int, int]],
                           depths: list[int | None] | None = None) -> list[dict]:
    """Per-chunk source-density metrics, computed from facts already at
    hand at chunk-boundary time -- cheap, and shared by batch.py's and
    testbatch.py's chunked-rendering paths so a rule-dense chunk's failure
    diagnostics look the same no matter which harness produced it (issue
    #105). Routine-aware chunking packs by rule count within routine
    # boundaries, but that count is blind to how content-dense a chunk's
    # *source* actually is: two chunks can carry the same rule count
    while one's source is far harder for the model to narrate correctly
    (more source lines, more nested branches, per rule) -- these metrics
    make that difference visible instead of only showing up, after the
    fact, as a chunk that just happens to fail its retries too.

    `line_nos`/`ranges` follow exactly routine_aware_chunk_ranges's own
    convention: `line_nos` is one entry per item in source order (a
    rule_candidate's line_no for batch.py; a test_case's originating
    rule's line_no -- or a sentinel below 0 when it belongs to no rule/
    routine -- for testbatch.py), and each `(start, end)` in `ranges` is a
    1-based, inclusive ordinal slice into it, the same slices
    routine_aware_chunk_ranges itself returned. `depths` (optional, same
    length/order as `line_nos`) supplies a nesting-depth proxy when the
    caller has one at hand (rule_candidate.depth) -- omitted entirely by
    callers that don't (test_case rows carry no depth of their own).

    A chunk with fewer than 2 resolvable (>= 0) line numbers in its own
    slice can't have a source span computed (nothing to subtract) -- its
    `line_span` comes back `None` rather than a misleading 0 or 1.
    `lines_per_item` additionally requires *every* item in the chunk to
    have a resolvable line number: a `line_span` computed from only some
    of a chunk's items, then divided by the chunk's full `item_count`,
    would produce a `lines_per_item` that doesn't correspond to the items
    used to compute the span and can understate density in the outlier
    check -- so a mix of resolvable and unresolvable (sentinel) line
    numbers still yields a `line_span` where possible, but `lines_per_item`
    comes back `None`.

    Each returned dict: `item_count`, `line_span` (inclusive source lines
    spanned by this chunk's own items, or `None`), `lines_per_item` (`None`
    unless every item in the chunk has a resolvable line number), `avg_depth`
    (mean of this chunk's non-None depths, or `None` when `depths` wasn't
    given or none of this chunk's rows have one)."""
    metrics: list[dict] = []
    for start, end in ranges:
        raw_lines = line_nos[start - 1:end]
        slice_lines = [ln for ln in raw_lines if ln is not None and ln >= 0]
        item_count = end - start + 1
        all_resolvable = len(slice_lines) == len(raw_lines)
        if len(slice_lines) >= 2:
            line_span = max(slice_lines) - min(slice_lines) + 1
            lines_per_item = line_span / item_count if all_resolvable else None
        else:
            line_span = None
            lines_per_item = None
        avg_depth = None
        if depths is not None:
            slice_depths = [d for d in depths[start - 1:end] if d is not None]
            if slice_depths:
                avg_depth = sum(slice_depths) / len(slice_depths)
        metrics.append({
            "item_count": item_count,
            "line_span": line_span,
            "lines_per_item": lines_per_item,
            "avg_depth": avg_depth,
        })
    return metrics


def flag_density_outliers(metrics: list[dict], factor: float = 1.5) -> list[dict]:
    """A new list (input `metrics` untouched), each entry the corresponding
    input dict plus `outlier` (bool) and `outlier_reasons` (list[str]):
    flags a chunk whose `lines_per_item` or `avg_depth` is at least
    `factor` times this *run's* median for that metric across its other
    chunks -- "well above the run's median" (issue #105's own phrasing),
    not a fixed absolute threshold, since what counts as dense varies by
    codebase and dialect. Needs at least 2 chunks with a usable value for
    a given metric to have a median to compare against at all; a
    `lines_per_item` median of 0 is left unflagged for that metric (a
    multiple of 0 is meaningless, and a 0 `lines_per_item` shouldn't occur
    in practice). `avg_depth` is different: it's 0-based (top-level depth
    is often 0), so a run whose other chunks are all flat (median 0) would
    never flag a genuinely nested chunk under the usual "factor x median"
    rule -- a chunk with `avg_depth > 0` against a 0 median is flagged
    directly instead."""
    def _usable(key):
        return [(i, m[key]) for i, m in enumerate(metrics) if m.get(key) is not None]

    def _median_excluding(usable, idx):
        # Median of the OTHER chunks' values only -- excluding this chunk's
        # own entry by position, not by value, so two chunks that happen to
        # share a value don't cancel each other out of each other's medians.
        # A candidate chunk's own (often extreme) value must never pull the
        # median it's being compared against toward itself, or a run with
        # multiple dense chunks (e.g. [1, 100, 100] lines/item) can dilute
        # the median enough that none of them clear `factor` (issue #105
        # review feedback).
        others = [v for i, v in usable if i != idx]
        return statistics.median(others) if others else None

    lpi_usable = _usable("lines_per_item")
    depth_usable = _usable("avg_depth")

    out: list[dict] = []
    for i, m in enumerate(metrics):
        reasons = []
        lpi = m.get("lines_per_item")
        if lpi is not None:
            lpi_median = _median_excluding(lpi_usable, i)
            # A 0 (or absent) median is left unflagged: a multiple of 0 is
            # meaningless, and there's nothing to divide by.
            if lpi_median and lpi >= factor * lpi_median:
                reasons.append(
                    f"lines/item {lpi:.1f} vs run median {lpi_median:.1f} ({lpi / lpi_median:.1f}x)"
                )
        depth = m.get("avg_depth")
        if depth is not None:
            depth_median = _median_excluding(depth_usable, i)
            if depth_median is not None:
                if depth_median == 0:
                    # depth is 0-based (top-level depth is often 0), so a run
                    # where every other chunk is flat has a median of 0 --
                    # leaving that unflagged (as a 0 lines_per_item median is)
                    # would mean a genuinely nested chunk never gets flagged
                    # against an all-flat baseline. Flag it directly instead
                    # of dividing by a 0 median.
                    if depth > 0:
                        reasons.append(
                            f"avg nesting depth {depth:.1f} vs run median 0.0 "
                            f"(run's other chunks are flat)"
                        )
                elif depth >= factor * depth_median:
                    reasons.append(
                        f"avg nesting depth {depth:.1f} vs run median {depth_median:.1f} "
                        f"({depth / depth_median:.1f}x)"
                    )
        out.append({**m, "outlier": bool(reasons), "outlier_reasons": reasons})
    return out


def format_density_note(metrics_entry: dict) -> str:
    """One-line rendering of a chunk_density_metrics/flag_density_outliers
    entry, meant to be appended to a failed chunk's own diagnostics (see
    batch.py's/testbatch.py's chunked-rendering failure paths) -- e.g.
    "density: 8 item(s), 42.5 lines/item, avg depth 3.4 -- OUTLIER
    (lines/item 2.3x vs run median 18.1)". So a human reading a retry
    report can tell "was this chunk just unlucky, or is it actually
    harder" immediately, rather than reverse-engineering it from a pattern
    of repeated failures across a whole run (issue #105). Never raises on
    a partially-populated entry (no `line_span` when too few line numbers
    were resolvable, no `avg_depth` when the caller passed none) -- it
    prints what's known instead of failing the diagnostic that exists
    specifically to help debug a failure."""
    bits = [f"{metrics_entry['item_count']} item(s)"]
    if metrics_entry.get("lines_per_item") is not None:
        bits.append(f"{metrics_entry['lines_per_item']:.1f} lines/item")
    if metrics_entry.get("avg_depth") is not None:
        bits.append(f"avg depth {metrics_entry['avg_depth']:.1f}")
    note = "density: " + ", ".join(bits)
    if metrics_entry.get("outlier_reasons"):
        note += " -- OUTLIER (" + "; ".join(metrics_entry["outlier_reasons"]) + ")"
    return note


def fetch_rule_candidate_rows(conn, member_name: str):
    """(rows, ambiguous_libs) for member_name's own rule_candidate rows, in
    the exact order/selection module_brief() numbers them from -- factored
    out so batch.py's chunked render path can decide whether (and how) to
    chunk *before* building a brief, using the same row set module_brief
    would number from. Deliberately excludes copycode-inherited rules
    (`_copycode_rule_candidates`): those are rendered under their own
    heading regardless of chunking, on the assumption that an included
    copycode's own rule set is small relative to the member using it --
    see module_brief's `rule_range` parameter."""
    from .db import resolve_member_by_name

    matches, ambiguous_libs = resolve_member_by_name(conn, member_name)
    if ambiguous_libs:
        return [], ambiguous_libs
    if not matches:
        return [], []
    mid = matches[0]["id"]
    rows = conn.execute(
        "SELECT * FROM rule_candidate WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()
    return rows, []


_SCREEN_SCOPES = {"screen"}
_GLOBAL_SCOPES = {"global", "independent", "mantis_shared"}


def _variable_kind(row, screen_field_names: set[str]) -> str:
    """Human-readable label for a `variable` row's scope, distinguishing a
    screen/MAP field from a plain program variable (issue #141). Callers
    already exclude scope in ('parameter','entry','view','mantis_interface')
    before calling this -- those are labelled by their own section heading
    instead (or, for `mantis_interface`'s call-target-binding rows -- see
    `graph.resolve_interface_literal_calls` -- not rendered as a field at
    all, since they aren't one), as are Natural's synthetic `USING <name>`
    data-area-include rows (see module_brief's "Data areas included"
    section)."""
    scope = row["scope"] or ""
    if scope in _SCREEN_SCOPES:
        return "screen field"
    if row["name"].upper() in screen_field_names:
        return "screen field (bound via MAP)"
    if scope in _GLOBAL_SCOPES:
        return "program variable (global)"
    return "program variable"


def _natural_screen_field_names(conn, mid) -> set[str]:
    """Field names declared on a Natural map (.nsm) this member INPUTs/
    DISPLAYs `USING MAP` -- resolved by joining the call_edge INCLUDE row
    `natural._match_interaction` already records for `USING MAP` against
    the target map member's own `MAP_FIELD` interaction rows (recorded by
    `natural._match_map_body`). Both facts already exist in the fact store;
    this is a rendering-time cross-reference, not a new extraction pass --
    a Natural program's screen fields are otherwise declared as ordinary
    DEFINE DATA LOCAL variables, indistinguishable from a value that only
    ever lives in program memory (issue #141)."""
    rows = conn.execute(
        """
        SELECT DISTINCT i.target FROM call_edge ce
        JOIN interaction i ON i.member_id = ce.callee_id
        WHERE ce.caller_id=? AND ce.call_kind='INCLUDE' AND ce.args='USING MAP'
          AND ce.callee_id IS NOT NULL AND i.kind='MAP_FIELD' AND i.target IS NOT NULL
        """,
        (mid,),
    ).fetchall()
    return {r["target"].upper() for r in rows if r["target"]}


@dataclass
class MemberFacts:
    """Every whole-member fact `module_brief` needs to render everything
    except the "Candidate business rules" section -- interface, data
    access, calls, inbound callers, gaps, and the rest, none of which
    depends on `rule_range` (see `module_brief`'s own docstring). Built once
    by `build_member_facts()` and reused across every one of a chunked
    member's per-chunk `module_brief()` calls (issue #183), instead of each
    chunk re-running the same ~15 fact-store queries and only ever changing
    which slice of `rules` it renders.

    Fields hold raw rows (unredacted, unformatted) exactly as `module_brief`
    used to fetch them inline -- redaction and markdown formatting still
    happen in `module_brief` itself, at render time, so a `MemberFacts`
    built once and reused across chunks (or reused with a *different*
    `redact` than whatever call first built it -- see issue #183 review
    feedback) renders identically to a fresh per-chunk fetch would have.
    This includes `guard_facts` (the "preceding calls" facts for a callee
    reachable from exactly one call site): unlike an earlier version of
    this class, it is *not* pre-rendered with any particular `redact` --
    `module_brief` renders it via `_render_guard_chain(guard_facts,
    caller_name, redact)`, using its own caller's `redact`, every time."""
    mid: int
    name: str
    m: object
    line_count: int
    hdr: list
    params: list
    views: list
    screen_field_names: set
    other_vars: list
    data_area_includes: list
    routines: list
    acc: list
    unused: list
    tx: list
    calls: list
    inbound: list
    guard_facts: list
    inter: list
    msgs: list
    rules: list
    copycode_rules: list
    gaps: list


def build_member_facts(conn, member_name: str) -> "MemberFacts | str":
    """Fetch every whole-member fact `module_brief` needs, once, so a
    chunked member's many `module_brief()` calls (one per chunk -- see
    `_generate_module_doc_chunked`) can share a single fact-gather instead
    of each repeating it (issue #183).

    Returns a `MemberFacts` on success, or a rendered "ambiguous"/"no such
    member" markdown string -- the exact same early-return shape
    `module_brief` itself used to produce for the identical lookup failure.
    A caller that gets a `str` back should treat it as a complete brief and
    never call `module_brief()` with it.

    Takes no `redact` -- every field, `guard_facts` included, is raw,
    unredacted data (see `MemberFacts`'s docstring); redaction only ever
    happens in `module_brief` at render time, using whichever `redact` that
    particular call was given. This is what makes reusing one `MemberFacts`
    across a member's chunks safe even if a caller (hypothetically) reused
    it with a different `redact` per chunk -- there is no redact-dependent
    state baked into the facts for that to go stale against."""
    from .db import resolve_member_by_name

    matches, ambiguous_libs = resolve_member_by_name(conn, member_name)
    if ambiguous_libs:
        libs = ", ".join(ambiguous_libs)
        return (
            f"# {member_name}\n\nMember name is ambiguous across libraries ({libs}). "
            f"Re-run with a library-qualified name.\n"
        )
    if not matches:
        return f"# {member_name}\n\nNo such member in the index.\n"
    m = matches[0]
    mid, name = m["id"], m["name"]

    line_count = conn.execute(
        "SELECT COUNT(*) FROM source_line WHERE member_id=?", (mid,)
    ).fetchone()[0]

    all_lines = conn.execute(
        "SELECT line_no, text, is_comment FROM source_line WHERE member_id=? ORDER BY line_no",
        (mid,),
    ).fetchall()
    hdr = []
    for r in all_lines:
        if not r["is_comment"]:
            if hdr:
                break
            if r["text"].strip():
                break
            continue
        body_text = r["text"].strip().lstrip("*/%! ").rstrip("*/ ").strip()
        if len(body_text) > 3:
            hdr.append({"line_no": r["line_no"], "text": body_text})

    params = conn.execute(
        "SELECT * FROM variable WHERE member_id=? AND scope IN ('parameter','entry') "
        "AND name NOT LIKE 'USING %' ORDER BY line_no",
        (mid,),
    ).fetchall()

    views = conn.execute(
        "SELECT * FROM variable WHERE member_id=? AND scope='view' ORDER BY line_no", (mid,)
    ).fetchall()

    screen_field_names = (
        _natural_screen_field_names(conn, mid) if m["dialect"] == "natural" else set()
    )
    all_other_vars = conn.execute(
        "SELECT * FROM variable WHERE member_id=? AND scope NOT IN "
        "('parameter','entry','view','mantis_interface') ORDER BY line_no",
        (mid,),
    ).fetchall()
    data_area_includes = conn.execute(
        "SELECT * FROM variable WHERE member_id=? AND name LIKE 'USING %' ORDER BY line_no",
        (mid,),
    ).fetchall()
    other_vars = [r for r in all_other_vars if not r["name"].startswith("USING ")]

    routines = fetch_routines(conn, mid)

    acc = conn.execute(
        "SELECT * FROM data_access WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()

    from .graph import unused_entity_fields_for_member

    unused = unused_entity_fields_for_member(conn, mid)

    tx = conn.execute(
        "SELECT * FROM transaction_marker WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()

    calls = conn.execute(
        "SELECT * FROM call_edge WHERE caller_id=? ORDER BY line_no", (mid,)
    ).fetchall()

    inbound = conn.execute(
        """
        SELECT c.id AS caller_id, c.name AS caller, ce.call_kind, ce.line_no
          FROM call_edge ce JOIN member c ON c.id = ce.caller_id
         WHERE UPPER(ce.callee_name)=UPPER(?) ORDER BY c.name, ce.line_no
        """,
        (name,),
    ).fetchall()
    guard_facts: list[dict] = []
    if len(inbound) == 1:
        only = inbound[0]
        guard_facts = _caller_guard_chain_facts(conn, only["caller_id"], only["line_no"])

    inter = conn.execute(
        "SELECT * FROM interaction WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()

    msgs = conn.execute(
        "SELECT * FROM message_ref WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()

    rules = conn.execute(
        "SELECT * FROM rule_candidate WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()

    copycode_rules = _copycode_rule_candidates(conn, mid)

    gaps = conn.execute(
        f"SELECT * FROM gap WHERE member_id=? ORDER BY {GAP_SEVERITY_ORDER_SQL}, line_no", (mid,)
    ).fetchall()

    return MemberFacts(
        mid=mid, name=name, m=m, line_count=line_count, hdr=hdr, params=params, views=views,
        screen_field_names=screen_field_names, other_vars=other_vars,
        data_area_includes=data_area_includes, routines=routines, acc=acc, unused=unused,
        tx=tx, calls=calls, inbound=inbound, guard_facts=guard_facts, inter=inter, msgs=msgs,
        rules=rules, copycode_rules=copycode_rules, gaps=gaps,
    )


def module_brief(conn, member_name: str, excerpt_rules: bool = True,
                  redact: Redactor = NULL_REDACTOR, lexicon: dict[str, str] | None = None,
                  rule_range: tuple[int, int] | None = None,
                  chunk_info: tuple[int, int] | None = None,
                  chunk_map: dict[str, int] | None = None,
                  sme_notes: Notes | None = None,
                  facts: "MemberFacts | str | None" = None) -> str:
    """`rule_range` (1-based, inclusive, over this member's own rule_candidate
    rows in the same order they're numbered in) restricts the "Candidate
    business rules" section to that slice -- everything else in the brief
    (interface, data access, calls, copycode rules, gaps, ...) is unaffected,
    since a chunk still needs the whole module's context to narrate its
    slice of rules correctly. IDs keep their absolute position (`n` counts
    from 1 over the *full* rule set, not the chunk), so a rule's `BR-nnn`
    stays identical to what a single, unchunked brief would have assigned it.

    `chunk_info` is `(this_chunk, chunk_count)` when `rule_range` is set, used
    only to phrase the "this is a partial brief" note -- see batch.py's
    chunked module-doc path for what calls this with both set.

    `chunk_map` (routine name, upper-cased -> 1-based chunk index) is also
    only meaningful when `rule_range` is set. Without it, a chunk whose own
    rule range dispatches to a routine documented in a *different* chunk
    (e.g. a PF-key branch that PERFORMs a subroutine whose own rules fall
    outside this chunk's range) has no way to say anything more specific
    than "covered elsewhere" -- the writing-rules instruction not to
    "skip ahead" into another chunk's rule range leaves it nothing concrete
    to point at. Since `_generate_module_doc_chunked` computes every chunk's
    rule range up front, before any chunk is narrated, the full routine ->
    chunk mapping is already known the whole time and costs nothing to hand
    over: the "Internal routines" section below annotates each routine with
    its chunk number when given, so the model can write "documented in
    chunk 15" instead of leaving a dangling forward reference for nothing to
    ever resolve.

    `facts`, when given, is a `MemberFacts` (or the "ambiguous"/"not found"
    `str` `build_member_facts` returns for that lookup failure) already
    built for this same `member_name` -- lets a chunked member's per-chunk
    calls reuse one member-level fact-gather instead of re-querying it on
    every chunk (issue #183). When omitted (the default), this function
    builds it itself via `build_member_facts(conn, member_name)`, exactly
    as it always fetched these facts before this parameter existed -- so
    every call site other than the chunked-render loop is unaffected.
    Every field of a given `facts` (raw, unredacted data -- see
    `MemberFacts`'s own docstring) is redacted here, with *this* call's own
    `redact`, so reusing one `MemberFacts` across chunks (or even, in
    principle, across calls using different `redact` policies) never
    reads stale or wrongly-redacted text."""
    if facts is None:
        facts = build_member_facts(conn, member_name)
    if isinstance(facts, str):
        return facts
    mid, name, m = facts.mid, facts.name, facts.m
    out: list[str] = []
    add = out.append

    add(f"# Fact brief: {name}")
    add("")
    add(f"- system: {m['system'] or 'unknown'}")
    add(f"- dialect: {m['dialect']}")
    add(f"- object_type: {m['object_type'] or 'unknown'}")
    add(f"- library: {m['library'] or 'unknown'}")
    if m["dialect"] == "natural":
        add(f"- natural_mode: {m['mode'] or 'unknown'}")
    add(f"- line_count: {facts.line_count}")
    if rule_range:
        start, end = rule_range
        this_chunk, chunk_count = chunk_info or (1, 1)
        add(
            f"- **PARTIAL BRIEF -- chunk {this_chunk} of {chunk_count}**: the "
            f"\"Candidate business rules\" section below covers only "
            f"{name}:BR-{start:03d} through {name}:BR-{end:03d} of this "
            "member's full rule set. Write the complete document template "
            "(every section) for this chunk, but only for that rule range -- "
            "do not invent, skip ahead to, or apologise for rules outside "
            "it; the other chunks cover them independently. When this chunk's "
            "own rules dispatch to a routine documented in another chunk (see "
            "the \"Internal routines\" list below for its chunk number), name "
            "that chunk instead of a vague \"covered elsewhere\"."
        )
    add("")
    vocab_insert_at = len(out)

    # --- header comments often carry the only surviving prose description
    # Only the leading contiguous comment block, and only lines with real content.
    # Rule-of-thumb separators (`*`, `****`) and lone `*` spacers add noise that
    # crowds out the two or three lines that actually say what the module is for.
    hdr = facts.hdr
    if hdr:
        add("## Header comments (unverified author prose — treat as claims, not facts)")
        for r in hdr:
            add(f"- {_cite(name, r['line_no'])} `{redact(r['text'][:160])}`")
        add("")

    # --- interfaces
    params = facts.params
    if params:
        add("## Interface (parameters)")
        for r in params:
            spec = f" ({r['format'] or ''}{r['length'] or ''})" if (r["format"] or r["length"]) else ""
            add(f"- {_cite(name, r['line_no'])} level {r['level'] or '-'} `{r['name']}`{spec}")
        add("")

    views = facts.views
    if views:
        add("## Data views declared")
        for r in views:
            add(f"- {_cite(name, r['line_no'])} view `{r['name']}` over `{r['view_of']}`")
        add("")

    # --- program variables and screen/MAP fields (issue #141): every other
    # variable.scope value -- working storage (local/mantis_local/global/
    # independent/mantis_shared), a Mantis SCREEN-bound field (scope='screen'),
    # or a Natural local variable that's actually the target of a `USING MAP`
    # (resolved below via _natural_screen_field_names, cross-referencing facts
    # already recorded -- no new extraction pass). Rendered separately from
    # "Interface (parameters)"/"Data views declared" above and tagged with an
    # explicit kind so a reader (and the narrating LLM filling in the
    # template's Inputs/Data-used tables) can tell a screen field the operator
    # sees apart from a value that only ever lives in program memory.
    #
    # Natural's DEFINE DATA <scope> USING <LDA/PDA/GDA> also records a
    # synthetic `variable` row named `USING <NAME>` (natural.py, alongside
    # the matching call_edge/INCLUDE row) so the include is visible even
    # when the data area itself isn't in the fact store. That's a data-area
    # include, not a program variable -- keep it out of the kind-labelled
    # list below (issue #141 follow-up) and surface it in its own small
    # subsection instead of silently dropping it.
    screen_field_names = facts.screen_field_names
    other_vars = facts.other_vars
    data_area_includes = facts.data_area_includes
    if other_vars:
        add("## Program variables and screen/MAP fields")
        add(
            "Kind distinguishes a screen/MAP-bound field (a value the operator "
            "sees or enters on a screen) from a plain program variable "
            "(working storage -- exists only in memory while the program "
            "runs). Don't conflate the two just because a field name alone "
            "doesn't make the distinction obvious."
        )
        for r in other_vars:
            kind = _variable_kind(r, screen_field_names)
            spec = f" ({r['format'] or ''}{r['length'] or ''})" if (r["format"] or r["length"]) else ""
            bound = f" bound to `{r['view_of']}`" if r["view_of"] else ""
            add(f"- {_cite(name, r['line_no'])} {kind} `{r['name']}`{spec}{bound}")
        add("")
    if data_area_includes:
        add("## Data areas included")
        add(
            "A `DEFINE DATA ... USING` data area (LDA/PDA/GDA) this member "
            "includes -- its own fields live in that data area's own member, "
            "not here; this is only the include itself, not a program "
            "variable."
        )
        for r in data_area_includes:
            add(f"- {_cite(name, r['line_no'])} data area include `{r['name'][len('USING '):]}`")
        add("")

    # --- internal routines (Natural DEFINE SUBROUTINE / Mantis ENTRY) --
    # the structural grouping the "Candidate business rules" section below
    # tags each rule with, so the narrator can (and should) write one
    # subsection per routine rather than a flat list -- see writing-rules.md.
    routines = facts.routines
    if routines:
        add("## Internal routines")
        add(
            "Every rule below is tagged with the routine it falls in, when "
            "it falls in one. Structure the \"Business rules\" (and, where "
            "it helps, \"Processing sequence\") section of the generated "
            "document around these routines rather than a flat list -- a "
            "reader trying to find everything one routine does should not "
            "have to read the whole document."
        )
        if chunk_map:
            add(
                "This document is one chunk of several covering this member. "
                "Where a routine below is annotated **[documented in chunk N]**, "
                "that is a different chunk than this one -- if a rule in *this* "
                "chunk's range dispatches to it (e.g. a branch that PERFORMs/"
                "CALLs it) without itself explaining what it does, say so "
                "concretely (\"documented in chunk N\"), not with a vague "
                "\"covered elsewhere\"/\"covered by a later chunk\"."
            )
        current_chunk = chunk_info[0] if chunk_info else None
        for r in routines:
            span = _cite(name, r["start_line"], r["end_line"]) if r["end_line"] else \
                f"{_cite(name, r['start_line'])} **[no matching end found -- extent unresolved]**"
            chunk_note = ""
            if chunk_map is not None:
                idx = chunk_map.get(r["name"].upper())
                # Only worth pointing out when it's a *different* chunk than
                # this one -- a routine documented in the chunk currently
                # being written needs no forward reference to itself.
                if idx and idx != current_chunk:
                    chunk_note = f" **[documented in chunk {idx}]**"
            add(f"- `{r['name']}` ({r['kind']}) {span}{chunk_note}")
        add("")

    # --- data access
    acc = facts.acc
    # Pulled out once, ahead of the "Candidate business rules" loop below,
    # purely so _branch_data_access's bisect lookups (issue #183 review
    # feedback) have a plain line_no list to search rather than re-deriving
    # one (or falling back to an O(rules * data_access) scan) on every rule.
    acc_line_nos = [r["line_no"] for r in acc]
    if acc:
        add("## Data access (verified from source statements)")
        for r in acc:
            key = f" key/where: `{redact(r['key_expr'])}`" if r["key_expr"] else ""
            desc = f" descriptor: `{r['descriptor']}`" if r["descriptor"] else ""
            flag = "" if r["confidence"] == "verified" else f" **[{r['confidence']}]**"
            source = ""
            if r["key_source_line"] is not None:
                source = (
                    f" -- **key built at** {_cite(name, r['key_source_line'])}: "
                    f"`{redact(r['key_source_expr'])}`"
                )
            # Found-body extent (issue #148): for FIND/READ/HISTOGRAM, the
            # line range up to this verb's own END-FIND/END-READ/
            # END-HISTOGRAM, once resolved -- an explicit fact that the
            # statements in that range run only for a record this verb
            # actually read/matched, so the narrative stage doesn't have
            # to guess a found/not-found shape from line adjacency alone
            # (and risk documenting the inverse of it, as happened before
            # this fix existed). Absent (NULL) when no matching END- was
            # found, or for a verb with no END- form at all (GET, SELECT,
            # STORE, UPDATE, DELETE, READ WORK FILE) -- never guessed.
            extent = (
                f" -- **found-body extent** {_cite(name, r['line_no'], r['end_line'])}: "
                "statements in this range run only when this verb reads/matches a "
                "record; no implicit not-found branch unless the source shows one"
                if r["end_line"] else ""
            )
            add(f"- {_cite(name, r['line_no'])} `{r['verb']}` ({r['crud']}) on "
                f"`{r['entity_name'] or 'UNKNOWN'}`{desc}{key}{flag}{source}{extent}")
        add("")

    # --- unreferenced fields on every screen/table this member touches --
    # completeness in the other direction from "Data access" above: not
    # just what the module reads/writes, but what it *never* touches on a
    # store it otherwise uses at all. A screen's field inventory (or a
    # Supra/Adabas table's) is complete on its own terms, so a field that
    # never turns up in this member's own source is a real finding, not a
    # scanner gap -- also recorded as a `gap` row (gap_kind='unused_field')
    # by `mfdoc derive`, so it reaches the gap register too.
    unused = facts.unused
    if unused:
        add("## Unreferenced fields on entities this module touches")
        add(
            "Present on the corresponding screen/table but never found, as a whole "
            "word, anywhere in this member's own source -- worth naming explicitly "
            "as unused (or flagging as a possible scanner gap) rather than omitting "
            "silently, the same way a field that *is* used gets documented."
        )
        by_entity: dict[str, list[dict]] = {}
        for f in unused:
            by_entity.setdefault(f["entity_name"], []).append(f)
        for entity_name, fields in by_entity.items():
            kind = fields[0]["entity_kind"]
            field_list = ", ".join(f"`{f['field_name']}`" for f in fields)
            add(f"- `{entity_name}` ({kind}): {field_list}")
        add("")

    # --- transaction markers
    tx = facts.tx
    if tx:
        add("## Transaction boundaries")
        for r in tx:
            add(f"- {_cite(name, r['line_no'])} `{r['marker']}`"
                + (f" restart data: `{redact(r['et_data'])}`" if r["et_data"] else ""))
        add("")

    # --- calls
    calls = facts.calls
    if calls:
        add("## Outbound calls")
        for r in calls:
            if r["dynamic"]:
                tag = " **[dynamic target — callee set unknown]**"
            elif r["call_kind"] == "PERFORM_INTERNAL":
                tag = " *(internal subroutine in this member)*"
            elif r["resolved"]:
                tag = ""
            else:
                tag = " **[source not supplied]**"
            add(f"- {_cite(name, r['line_no'])} `{r['call_kind']}` -> `{r['callee_name']}`{tag}"
                + (f" args: `{redact(r['args'])}`" if r["args"] else ""))
        add("")

    inbound = facts.inbound
    if inbound:
        add("## Inbound callers")
        for r in inbound:
            add(f"- {_cite(r['caller'], r['line_no'])} `{r['call_kind']}` from `{r['caller']}`")
        # Reachable from exactly one call site (issue #151): the "How it is
        # invoked" section only had the call line itself to cite, never the
        # caller's own preceding guard/validation sequence that decides
        # whether and when that call happens. With more than one call site,
        # there is no single guard chain to point to -- each caller may gate
        # the call under a different condition, or none -- so this is
        # deliberately scoped to the single-caller case only.
        if len(inbound) == 1:
            only = inbound[0]
            guard_lines = _render_guard_chain(facts.guard_facts, only["caller"], redact)
            if guard_lines:
                add("")
                add(
                    f"This is the only known call site for `{name}`. Before reaching it, "
                    f"`{only['caller']}` performs, in order:"
                )
                out.extend(guard_lines)
        add("")

    # --- interactions
    inter = facts.inter
    if inter:
        add("## User interaction points")
        for r in inter:
            add(f"- {_cite(name, r['line_no'])} `{r['kind']}`"
                + (f" target `{r['target']}`" if r["target"] else "")
                + (f" `{redact((r['fields'] or '')[:90])}`" if r["fields"] else ""))
        add("")

    msgs = facts.msgs
    if msgs:
        add("## Messages and error handling")
        for r in msgs:
            add(f"- {_cite(name, r['line_no'])} `{r['kind']}`"
                + (f" number `{r['number']}`" if r["number"] else "")
                + (f" text: \"{redact((r['text'] or '')[:120])}\"" if r["text"] else ""))
        add("")

    # --- rule candidates, with exact conditions
    rules = facts.rules
    if rules:
        add("## Candidate business rules (exact conditions — paraphrase, never invent)")
        add(
            "Each carries a stable `BR-nnn` ID -- carry it verbatim into the "
            "generated document immediately after the rule's own citation. It "
            "is derived from source position, not written by you, so it stays "
            "the same across a re-run of unchanged source."
        )
        if rule_range:
            start, end = rule_range
            add(
                f"Only rules {start}-{end} of {len(rules)} are listed here -- "
                "this is intentional, not a truncated brief; see the "
                "PARTIAL BRIEF note above."
            )
        for n, r in numbered_rule_candidates(rules):
            if rule_range and not (rule_range[0] <= n <= rule_range[1]):
                continue
            bits = [f"**{_rule_id(name, n)}** {_cite(name, r['line_no'])} depth {r['depth']} `{r['construct']}`"]
            routine = routine_for_line(routines, r["line_no"])
            if routine:
                bits.append(f"routine: `{routine['name']}`")
            if r["condition"]:
                bits.append(f"condition: `{redact(r['condition'])}`")
            if r["literals"]:
                bits.append(f"literals: `{redact(r['literals'])}`")
            # --- IF/ELSE branch extent and what's inside it -- see
            # db.py's rule_candidate.end_line/pair_line_no comment. Told
            # apart explicitly so the generated document has no excuse to
            # describe only the branch that reads as interesting (usually
            # the validation/error one) and silently drop the other's
            # effects -- exactly the failure this exists to prevent.
            if r["construct"] == "IF" and r["end_line"]:
                else_line = next(
                    (rr["line_no"] for rr in rules if rr["pair_line_no"] == r["line_no"]), None
                )
                body_end = (else_line - 1) if else_line else r["end_line"]
                bits.append(f"true-branch extent {_cite(name, r['line_no'], body_end)}")
                if else_line:
                    bits.append(
                        f"has a paired ELSE at {_cite(name, else_line)} -- "
                        "document what happens on BOTH branches, not just this one"
                    )
                access_summary = _branch_data_access(acc, acc_line_nos, name, r["line_no"], body_end)
                if access_summary:
                    bits.append(f"data access on the true branch: {access_summary}")
            elif r["construct"] == "ELSE" and r["pair_line_no"]:
                bits.append(f"pairs with the IF at {_cite(name, r['pair_line_no'])}")
                if r["end_line"]:
                    bits.append(f"else-branch extent {_cite(name, r['line_no'], r['end_line'])}")
                    access_summary = _branch_data_access(acc, acc_line_nos, name, r["line_no"], r["end_line"])
                    if access_summary:
                        bits.append(f"data access on this branch: {access_summary}")
            add("- " + " — ".join(bits))
        add("")

    # --- rules inherited from included copycode, cited against the copycode
    # itself. Without this, a rule defined only in a copycode member is
    # attributed solely to that member's own brief, and never appears when
    # briefing the module that actually includes and runs it -- a module doc
    # can look complete and still miss a validation rule it depends on.
    for cc_id, cc_name, cc_rules in facts.copycode_rules:
        add(f"## Business rules from included copycode `{cc_name}`")
        for n, r in numbered_rule_candidates(cc_rules):
            # IDs are qualified with the copycode's own name and numbered
            # from its own row order -- the same ID a direct brief of
            # cc_name would show, since the rule "lives" there regardless
            # of which including module's brief surfaces it.
            bits = [f"**{_rule_id(cc_name, n)}** {_cite(cc_name, r['line_no'])} depth {r['depth']} `{r['construct']}`"]
            if r["condition"]:
                bits.append(f"condition: `{redact(r['condition'])}`")
            if r["literals"]:
                bits.append(f"literals: `{redact(r['literals'])}`")
            add("- " + " — ".join(bits))
        add("")

    gaps = facts.gaps
    if gaps:
        add("## Known gaps for this module")
        for r in gaps:
            loc = _cite(name, r["line_no"]) if r["line_no"] else _cite(name, None)
            add(f"- [{r['severity']}] {loc} {r['gap_kind']}: {r['detail']}")
        add("")

    # --- vocabulary, filtered to terms this member's own facts actually
    # mention. options.narrative.lexicon is human-supplied per engagement
    # (see project.yml), so surfacing it here doesn't invent anything; it
    # just makes it reach mfdoc batch's headless prompts too, not only a
    # human who happens to have project.yml open alongside a chat session.
    if lexicon:
        haystack = "\n".join(out)
        hits = [(k, v) for k, v in lexicon.items() if k in haystack]
        if hits:
            vocab = [
                "## Business vocabulary (from `options.narrative.lexicon` in "
                "project.yml — use these terms verbatim; do not invent synonyms)",
                "",
            ]
            for k, v in hits:
                vocab.append(f"- `{k}` -> {redact(v)}")
            vocab.append("")
            out[vocab_insert_at:vocab_insert_at] = vocab

    out.extend(_sme_notes_section(redact, sme_notes, name))

    return "\n".join(out) + "\n"


def entity_brief(conn, entity_name: str, redact: Redactor = NULL_REDACTOR,
                  lexicon: dict[str, str] | None = None,
                  sme_notes: Notes | None = None) -> str:
    e = conn.execute(
        "SELECT * FROM entity WHERE UPPER(name)=UPPER(?) LIMIT 1", (entity_name,)
    ).fetchone()
    if not e:
        return f"# {entity_name}\n\nNot in index.\n"
    out = [f"# Fact brief: data store {e['name']}", ""]
    out.append(f"- kind: {e['kind']}")
    out.append(f"- physical: {e['physical_ref'] or 'unknown'}")
    definer = None
    if e["defined_in"]:
        definer = conn.execute("SELECT name FROM member WHERE id=?", (e["defined_in"],)).fetchone()["name"]
        out.append(f"- definition source: {_cite(definer, e['defined_line'])}")
    else:
        out.append("- definition source: **none supplied — field semantics unverifiable**")
    out.append("")
    vocab_insert_at = len(out)

    fields = conn.execute(
        "SELECT * FROM entity_field WHERE entity_id=? ORDER BY IFNULL(defined_line,0), id", (e["id"],)
    ).fetchall()
    if fields:
        out.append("## Fields")
        out.append("")
        out.append("| level | name | short | format | length | occurs | descriptor | options | citation |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for f in fields:
            cite = _cite(definer, f["defined_line"]) if definer else ""
            out.append(
                f"| {f['level'] or ''} | `{f['name']}` | {f['short_name'] or ''} | "
                f"{f['format'] or ''} | {f['length'] or ''} | {f['occurrences'] or ''} | "
                f"{f['descriptor_kind'] or ''} | {f['options'] or ''} | {cite} |"
            )
        out.append("")

    links = conn.execute(
        """
        SELECT el.*, a.name AS from_name, b.name AS to_name, m.name AS via
          FROM entity_link el
          JOIN entity a ON a.id = el.from_entity
          JOIN entity b ON b.id = el.to_entity
          LEFT JOIN member m ON m.id = el.via_member
         WHERE el.from_entity=? OR el.to_entity=?
        """,
        (e["id"], e["id"]),
    ).fetchall()
    if links:
        out.append("## Relationships")
        for l in links:
            cite = _cite(l["via"], l["via_line"]) if l["via"] else ""
            out.append(f"- {cite} `{l['from_name']}` --{l['link_kind']}"
                       f"{'(' + redact(l['link_name']) + ')' if l['link_name'] else ''}--> `{l['to_name']}`")
        out.append("")

    users = conn.execute(
        """
        SELECT m.name AS module, da.crud, da.verb, da.line_no, da.key_expr
          FROM data_access da JOIN member m ON m.id = da.member_id
         WHERE UPPER(da.entity_name)=UPPER(?) ORDER BY m.name, da.line_no
        """,
        (e["name"],),
    ).fetchall()
    if users:
        out.append("## Accessed by")
        for u in users:
            out.append(f"- {_cite(u['module'], u['line_no'])} `{u['module']}` `{u['verb']}` ({u['crud']})"
                       + (f" via `{redact(u['key_expr'][:80])}`" if u["key_expr"] else ""))
        out.append("")
    else:
        out.append("## Accessed by\n\n- No application access found in the ingested source. "
                   "Either the consuming code was not supplied or the store is obsolete.\n")

    if lexicon:
        haystack = "\n".join(out)
        hits = [(k, v) for k, v in lexicon.items() if k in haystack]
        if hits:
            vocab = [
                "## Business vocabulary (from `options.narrative.lexicon` in "
                "project.yml — use these terms verbatim; do not invent synonyms)",
                "",
            ]
            for k, v in hits:
                vocab.append(f"- `{k}` -> {redact(v)}")
            vocab.append("")
            out[vocab_insert_at:vocab_insert_at] = vocab

    out.extend(_sme_notes_section(redact, sme_notes, e["name"]))

    return "\n".join(out) + "\n"


def system_brief(conn, redact: Redactor = NULL_REDACTOR) -> str:
    out = ["# Fact brief: system overview", ""]
    cov = {r["name"]: r["value"] for r in conn.execute(
        "SELECT name, value FROM metric WHERE scope='global'").fetchall()}
    out.append("## Index coverage")
    for k, v in sorted(cov.items()):
        out.append(f"- {k}: {v}")
    out.append("")

    out.append("## Members by dialect and type")
    out.append("")
    out.append("| dialect | object_type | count |")
    out.append("|---|---|---|")
    for r in conn.execute(
        "SELECT dialect, IFNULL(object_type,'unknown') t, COUNT(*) n FROM member GROUP BY dialect, t ORDER BY dialect, t"
    ).fetchall():
        out.append(f"| {r['dialect']} | {r['t']} | {r['n']} |")
    out.append("")

    out.append("## Entry points (JCL steps and CICS transactions)")
    for r in conn.execute(
        """
        SELECT m.name AS src, js.step_name, js.program, js.line_no
          FROM job_step js JOIN member m ON m.id = js.member_id
         WHERE js.program IS NOT NULL ORDER BY m.name, js.line_no
        """
    ).fetchall():
        out.append(f"- {_cite(r['src'], r['line_no'])} job `{r['src']}` step `{r['step_name']}` "
                   f"runs `{r['program']}`")
    for r in conn.execute(
        """
        SELECT m.name AS src, cr.resource_name, cr.attributes, cr.line_no
          FROM cics_resource cr JOIN member m ON m.id = cr.member_id
         WHERE cr.resource_type='TRANSACTION' ORDER BY cr.resource_name
        """
    ).fetchall():
        out.append(f"- {_cite(r['src'], r['line_no'])} CICS transaction `{r['resource_name']}` "
                   f"({redact((r['attributes'] or '')[:100])})")
    out.append("")

    out.append("## CRUD matrix (module x data store)")
    out.append("")
    out.append("| module | data store | operations | verbs | first line |")
    out.append("|---|---|---|---|---|")
    for r in conn.execute(
        """
        SELECT m.name AS module, da.entity_name AS entity,
               GROUP_CONCAT(DISTINCT da.crud) crud, GROUP_CONCAT(DISTINCT da.verb) verbs,
               MIN(da.line_no) ln
          FROM data_access da JOIN member m ON m.id = da.member_id
         WHERE da.entity_name IS NOT NULL
         GROUP BY m.name, da.entity_name ORDER BY m.name, da.entity_name
        """
    ).fetchall():
        out.append(f"| `{r['module']}` | `{r['entity']}` | {r['crud']} | {r['verbs']} | "
                   f"{_cite(r['module'], r['ln'])} |")
    out.append("")

    out.append("## Highest-severity gaps")
    for r in conn.execute(
        """
        SELECT g.gap_kind, g.detail, g.severity, IFNULL(m.name,'-') mem, g.line_no
          FROM gap g LEFT JOIN member m ON m.id = g.member_id
         WHERE g.severity='high' ORDER BY g.gap_kind LIMIT 200
        """
    ).fetchall():
        loc = _cite(r["mem"], r["line_no"]) if r["mem"] != "-" else ""
        out.append(f"- {r['gap_kind']} {loc} {r['detail']}")
    out.append("")
    return "\n".join(out) + "\n"


def executive_brief(conn, member_name: str, redact: Redactor = NULL_REDACTOR, top_n: int = 10,
                     sme_notes: Notes | None = None) -> str:
    """Cited-facts brief for one program's executive summary: purpose/
    entry data, top rules by theme, I/O, external dependents, risk score.
    Same contract as module_brief/system_brief -- the narrative pass may
    only assert what this brief hands it, and every claim must carry a
    [[MEMBER:line]] citation back to source.

    Resolves `member_name` the same way module_brief/entity_brief do -- via
    db.resolve_member_by_name -- and returns the same kind of graceful
    "no such member"/"ambiguous across libraries" markdown they return,
    rather than raising, for the identical reason: a bare name is only
    unique together with library+dialect (see the `UNIQUE(name, library,
    dialect)` constraint in db.py), so blending facts from two distinct
    members that happen to share a name under one member's identity would
    be a citation-integrity bug, not just an edge case."""
    from . import structural  # local: avoids a circular import at load time (structural imports from this module)
    from .db import resolve_member_by_name

    matches, ambiguous_libs = resolve_member_by_name(conn, member_name)
    if ambiguous_libs:
        libs = ", ".join(ambiguous_libs)
        return (
            f"# Executive brief: {member_name}\n\nMember name is ambiguous across libraries ({libs}). "
            f"Re-run with a library-qualified name.\n"
        )
    if not matches:
        return f"# Executive brief: {member_name}\n\nNo such member in the index.\n"
    member = matches[0]

    out = [f"# Executive brief: {member['name']}", "",
           f"- library: `{member['library'] or 'unknown'}`",
           f"- object_type: `{member['object_type'] or 'unknown'}`", ""]

    out.append("## Top rules")
    rows = conn.execute(
        """
        SELECT rc.id, rc.line_no, rc.condition, COALESCE(rt.theme, 'uncategorized') AS theme
          FROM rule_candidate rc
          LEFT JOIN rule_theme rt ON rt.rule_candidate_id = rc.id
         WHERE rc.member_id = ?
         ORDER BY rc.depth DESC, rc.line_no
         LIMIT ?
        """,
        (member["id"], top_n),
    ).fetchall()
    if not rows:
        out.append("- no rule candidates recorded for this member")
    for r in rows:
        cond = redact(r["condition"]) if r["condition"] else "(no condition text)"
        out.append(f"- [{r['theme']}] {cond} {_cite(member['name'], r['line_no'])}")
    out.append("")

    out.append("## I/O")
    crud_rows = [row for row in structural.graph.crud_matrix(conn) if row["module"] == member["name"]]
    if not crud_rows:
        out.append("- no data access recorded for this member")
    for row in crud_rows:
        out.append(f"- `{row['entity']}`: {row['crud']} {_cite(member['name'], row['first_line'])}")
    out.append("")

    out.append("## Entry point")
    # How this member gets invoked in the first place -- batch (JCL EXEC PGM=,
    # or a Natural program named on a CMSYNIN stack under a JCL step) vs.
    # online (a CICS transaction DEFINE'd with PROGRAM(this member)). All three
    # shapes land in call_edge as call_kind='EXEC_PGM' rows (see
    # dialects/environment.py's extract_jcl/_parse_natural_stack/
    # extract_cics_csd) with only the *caller* member's own dialect
    # distinguishing a JCL job step from a CICS transaction definition --
    # there is no separate "entry point" table to join against, and matching
    # literally against job_step.program or cics_resource.resource_name would
    # miss the Natural-batch-stack shape entirely (its program name never
    # appears in job_step.program, only stacked in a CMSYNIN body under a
    # different step_program). Querying call_edge by callee + caller dialect
    # catches all three shapes uniformly.
    entry_rows = conn.execute(
        """
        SELECT m.name AS caller, m.dialect AS caller_dialect, ce.line_no, ce.args
          FROM call_edge ce JOIN member m ON m.id = ce.caller_id
         WHERE UPPER(ce.callee_name) = UPPER(?) AND ce.call_kind = 'EXEC_PGM'
         ORDER BY m.name, ce.line_no
        """,
        (member["name"],),
    ).fetchall()
    batch_rows = [r for r in entry_rows if r["caller_dialect"] == "jcl"]
    cics_rows = [r for r in entry_rows if r["caller_dialect"] == "cics_csd"]
    for row in batch_rows:
        detail = f" ({row['args']})" if row["args"] else ""
        out.append(
            f"- batch entry: invoked from JCL member `{row['caller']}`{detail} "
            f"{_cite(row['caller'], row['line_no'])}"
        )
    for row in cics_rows:
        txn_match = re.search(r"CICS transaction (\S+)", row["args"] or "")
        txn = txn_match.group(1) if txn_match else "unknown"
        out.append(
            f"- online entry: CICS transaction `{txn}` defined in `{row['caller']}` "
            f"{_cite(row['caller'], row['line_no'])}"
        )
    if not batch_rows and not cics_rows:
        out.append("- no entry-point fact was found for this member")
    out.append("")

    out.append("## External dependents")
    # build_call_graph()'s per-call dict deliberately carries no line_no (see
    # Tasks 6/7/8, which depend on its exact current shape) -- so this queries
    # call_edge directly for the citation instead of reusing that structure.
    # Matched by callee_name (not callee_id): graph.resolve() sets callee_id
    # via an unqualified UPPER(name) lookup with its own LIMIT 1, so comparing
    # names here mirrors exactly which edges build_call_graph would consider a
    # match for this member, resolved or not. This member's own name is
    # already confirmed unique across the whole member table by the
    # resolve_member_by_name call above, so no other member could also match
    # this same callee_name. The CALLERS are not similarly guaranteed unique,
    # though -- two distinct caller members can share a bare name across
    # libraries (member uniqueness is (name, library, dialect)) -- so this
    # groups by the caller's member id, not its bare name, and disambiguates
    # any name collision with a library label rather than collapsing two
    # distinct callers into one row and losing one's citation.
    caller_rows = conn.execute(
        """
        SELECT m.id AS caller_id, m.name AS caller, m.library AS caller_library,
               MIN(ce.line_no) AS first_line
          FROM call_edge ce JOIN member m ON m.id = ce.caller_id
         WHERE UPPER(ce.callee_name) = UPPER(?)
         GROUP BY m.id
         ORDER BY m.name, m.library
        """,
        (member["name"],),
    ).fetchall()
    if not caller_rows:
        out.append("- no known callers recorded for this member")
    name_counts: dict[str, int] = {}
    for row in caller_rows:
        name_counts[row["caller"]] = name_counts.get(row["caller"], 0) + 1
    for row in caller_rows:
        label = row["caller"]
        if name_counts[label] > 1:
            label = f"{row['caller']} ({row['caller_library'] or 'unknown'})"
        out.append(f"- called by `{label}` {_cite(row['caller'], row['first_line'])}")
    out.append("")

    out.append("## Risk")
    # Looked up via structural._complexity_rows()'s structured data by this
    # member's own id, not by re-parsing complexity_heatmap()'s rendered
    # markdown -- a string match against `| \`{name}\`` would silently stop
    # matching (and this section would then wrongly assert "no rule
    # candidates recorded") the moment the heatmap's column layout changes,
    # even though the underlying data never went away.
    complexity_rows = structural._complexity_rows(conn)
    match = None
    for row in complexity_rows:
        if row["ambiguous"]:
            if member["id"] in row["member_ids"]:
                match = row
                break
        elif row["member_id"] == member["id"]:
            match = row
            break
    if match is None:
        out.append("- no rule candidates recorded for this member")
    elif match["ambiguous"]:
        # Defence-in-depth only: unreachable via this function's own
        # resolve_member_by_name guard above (which already refuses any name
        # matching more than one member row before we ever get here), but
        # _complexity_rows()'s own ambiguity check is scoped only to members
        # with rule_candidate rows, a narrower condition than
        # resolve_member_by_name's -- kept in case that ever diverges.
        out.append(
            f"- risk score unavailable: `{member['name']}` is ambiguous across "
            "libraries -- re-run against a library-qualified export"
        )
    else:
        out.append(
            f"- risk_score: {match['risk_score']} (rule_count {match['rule_count']}, "
            f"max_depth {match['max_depth']}, in_degree {match['in_degree']}, "
            f"out_degree {match['out_degree']})"
        )
    out.append("")

    out.extend(_sme_notes_section(redact, sme_notes, member["name"]))

    return "\n".join(out) + "\n"


def rules_register(conn, redact: Redactor = NULL_REDACTOR) -> str:
    """A flat, system-wide index of every `MEMBER:BR-nnn` rule ID, generated
    straight from the fact store so it can never drift from what the module
    docs themselves carry (see #10/4.8 for where the ID scheme comes from).

    Scoped to the same members `mfdoc batch` treats as batchable — this is
    where `_rule_id`'s numbering lives, so a rule listed here has exactly the
    ID a direct brief of its own member would show, whether or not anything
    currently includes that member as copycode.

    Deliberately not run through the narrative pass: there is no judgement
    call here, only extraction, so a deterministic report (like `mfdoc
    coverage`) is a better fit than a model-authored doc_type. Regenerating
    against unchanged source reproduces this string byte-for-byte — no
    timestamp is embedded, on purpose, since one would defeat that guarantee
    without adding any real information (the index's own `generated_at` on
    the `ingest_run` row already records when the source was last read).

    Carries minimal `doc_type: register` front matter -- just enough for
    `mfdoc validate` to recognise this as a deterministic index rather than
    a narrative doc and skip the review/confidence fields that don't apply
    to it, without requiring a `generated_at` that would break the
    byte-identical guarantee above.
    """
    from .batch import select_batch_members  # local: avoids a circular import at load time

    out = ["---", 'title: "System-wide rules register"', "doc_type: register", "---", "",
           "# System-wide rules register", "", (
        "Every candidate business rule found across the index, keyed by its "
        "stable `MEMBER:BR-nnn` ID. Look one up here when it's referenced in "
        "conversation or a review comment without already knowing which "
        "module doc it lives in. Regenerate with `mfdoc rules-register` "
        "after any source change; do not hand-edit."
    ), ""]
    out.append("| BR-ID | member | line | depth | construct | condition | literals |")
    out.append("|---|---|---|---|---|---|---|")

    # `member.name` is only unique together with library+dialect (see the
    # `UNIQUE(name, library, dialect)` constraint in db.py) -- two batchable
    # members can share a bare name across libraries. select_batch_members
    # can then hand back that name more than once, so de-dupe before
    # resolving it, and treat a name that still maps to >1 row as ambiguous
    # rather than silently picking one (the same refusal module_brief makes
    # for the identical case) -- guessing would double-count one member's
    # rules under a colliding BR-ID while dropping the other's entirely.
    names = list(dict.fromkeys(select_batch_members(conn)))
    placeholders = ",".join("?" * len(names))
    member_rows = (
        conn.execute(
            f"SELECT id, name, library FROM member WHERE name IN ({placeholders})", names
        ).fetchall()
        if names
        else []
    )
    rows_by_name: dict[str, list] = {}
    for row in member_rows:
        rows_by_name.setdefault(row["name"], []).append(row)

    # One batched fetch for every resolved (unambiguous) member's rule
    # candidates instead of a query per member -- this function, unlike
    # module_brief/entity_brief, iterates the whole batchable-member list,
    # so a per-member round trip scales with system size.
    resolved_ids = [
        rows_by_name[name][0]["id"] for name in names if len(rows_by_name.get(name, [])) == 1
    ]
    id_placeholders = ",".join("?" * len(resolved_ids))
    rule_rows = (
        conn.execute(
            f"SELECT * FROM rule_candidate WHERE member_id IN ({id_placeholders}) "
            "ORDER BY member_id, line_no",
            resolved_ids,
        ).fetchall()
        if resolved_ids
        else []
    )
    rules_by_member_id: dict[int, list] = {}
    for r in rule_rows:
        rules_by_member_id.setdefault(r["member_id"], []).append(r)

    total = 0
    modules_included = 0
    ambiguous_names: list[str] = []
    ambiguous_ids: list[int] = []
    for member_name in names:
        matches = rows_by_name.get(member_name, [])
        if len(matches) != 1:
            ambiguous_names.append(member_name)
            ambiguous_ids.extend(m["id"] for m in matches)
            libs = ", ".join(sorted({m["library"] or "unknown" for m in matches})) or "none found"
            out.append(
                f"| — | `{member_name}` | — | — | ambiguous | name is ambiguous across "
                f"libraries ({libs}) -- re-run `mfdoc brief --module {member_name}` "
                "per library | — |"
            )
            continue
        modules_included += 1
        rules = rules_by_member_id.get(matches[0]["id"], [])
        for n, r in numbered_rule_candidates(rules):
            total += 1
            # A literal `|` in source-derived condition/literal text would
            # otherwise be read as an extra column delimiter and corrupt the
            # row -- escape it the way module_brief's bullet-list rendering
            # of the same fields never needed to.
            cond = redact(r["condition"]).replace("|", "\\|") if r["condition"] else ""
            lits = redact(r["literals"]).replace("|", "\\|") if r["literals"] else ""
            out.append(
                f"| **{_rule_id(member_name, n)}** | `{member_name}` | "
                f"{_cite(member_name, r['line_no'])} | {r['depth']} | `{r['construct']}` | "
                f"`{cond}` | `{lits}` |"
            )
    out.append("")

    if ambiguous_names:
        # Same disclosure `structural.thematic_rules_register()` makes for its
        # own ambiguous-name exclusion -- a rule count silently missing from
        # `total` (because an ambiguous member's rules can't be assigned a
        # `MEMBER:BR-nnn` ID without guessing which library it belongs to)
        # would otherwise read as if the system simply had no more rules,
        # rather than as an explicit, counted exclusion. The excluded
        # candidates aren't rendered as rows here (unlike the resolved ones
        # above) -- only their count -- since there's no single member to
        # attribute a `line`/`construct`/`condition` to; the ambiguous rows
        # already appended above name which modules they belong to.
        ambiguous_placeholders = ",".join("?" * len(ambiguous_ids))
        excluded_total = (
            conn.execute(
                f"SELECT COUNT(*) AS n FROM rule_candidate WHERE member_id IN ({ambiguous_placeholders})",
                ambiguous_ids,
            ).fetchone()["n"]
            if ambiguous_ids
            else 0
        )
        out.append(
            f"Total: {total} rule candidate(s) across {modules_included} batchable "
            f"module(s); {excluded_total} rule candidate(s) belonging to "
            f"{len(ambiguous_names)} ambiguous-named module(s) are listed under "
            '"ambiguous" above and excluded from this count.'
        )
    else:
        out.append(f"Total: {total} rule candidate(s) across {modules_included} batchable module(s).")
    out.append("")
    return "\n".join(out) + "\n"


def _screen_label_candidates(conn, screen_name: str) -> list[dict]:
    """Literal on-screen text recorded against `screen_name` itself --
    candidate PF-key labels (e.g. "PF3=Exit"), cited but *not* matched to a
    specific dispatch trigger value: that correlation (which label goes
    with which PF-key) is judgement, left for the narrative pass, not
    guessed here.

    Two shapes, since a Natural map and a Mantis screen record their own
    definition differently (see db.py/dialects/screen.py):

    - Natural: the map is its own `member` row (`object_type='map'`) with
      `interaction` rows of kind `MAP_TEXT` for each literal text/prompt
      found in the map body (natural.py's `_match_map_body`).
    - Mantis: the screen is a `mantis_map` `entity` row (dialects/screen.py),
      whose `HEADING`-format `entity_field` rows are the literal captions on
      the layout (see `FIELD_TYPES`'s own comment on why a `HEADING`'s
      "name" is the literal text itself, not a variable).

    A bare member name is only unique together with library+dialect (see
    db.py's `UNIQUE(name, library, dialect)`), so a Natural map name
    matching more than one member is ambiguous across libraries -- picking
    one with `LIMIT 1` could silently attribute another library's labels
    to this screen. Omit labels entirely in that case rather than guess
    which map they belong to (the `entity` table's own `UNIQUE(name, kind)`
    means the Mantis path below has no equivalent ambiguity).
    """
    map_members = conn.execute(
        "SELECT id, name FROM member WHERE UPPER(name)=UPPER(?) AND object_type='map'",
        (screen_name,),
    ).fetchall()
    if len(map_members) > 1:
        return []
    if map_members:
        map_member = map_members[0]
        rows = conn.execute(
            "SELECT line_no, fields FROM interaction WHERE member_id=? AND kind='MAP_TEXT' "
            "ORDER BY line_no",
            (map_member["id"],),
        ).fetchall()
        return [
            {"cite": _cite(map_member["name"], r["line_no"]), "text": r["fields"]}
            for r in rows if r["fields"]
        ]

    entity = conn.execute(
        "SELECT id, defined_in, defined_line FROM entity WHERE UPPER(name)=UPPER(?) "
        "AND kind='mantis_map' LIMIT 1",
        (screen_name,),
    ).fetchone()
    if entity and entity["defined_in"]:
        definer = conn.execute(
            "SELECT name FROM member WHERE id=?", (entity["defined_in"],)
        ).fetchone()
        if definer:
            rows = conn.execute(
                "SELECT defined_line, name FROM entity_field WHERE entity_id=? AND format='HEADING' "
                "ORDER BY IFNULL(defined_line,0)",
                (entity["id"],),
            ).fetchall()
            return [
                {"cite": _cite(definer["name"], r["defined_line"]), "text": r["name"]}
                for r in rows
            ]
    return []


def interface_matrix_brief(
    conn, redact: Redactor = NULL_REDACTOR, dispatch_field=None, mode_field=None,
) -> str:
    """Fact brief for the screen-and-key interface matrix document type
    (mode x panel x map x PF-label x routine x outcome, issue #91): per
    screen/map, which module(s) display it, the dispatch-trigger-value
    branches those modules dispatch on -- PF-key (or configured dispatch
    field) branches, and, when `options.overview.mode_field_pattern` is
    configured, mode/panel-keyed dispatch branches too (issue #129) -- and
    any literal label text recorded on the screen itself.

    Whole-system scope, no member argument -- like `system_brief` and
    `structural.dispatch_map`, not `module_brief`: this brief gathers each
    screen's display references and the PF-key branches found in the same
    modules that display it, so the matrix stays grounded in the fact
    store's actual screen-to-module relationships rather than inventing a
    cross-member correlation that is not recorded explicitly.

    Data-model decisions made here, since the fact store has no single
    table shaped like the target document:

    - **"reachable from"** is every module whose own source displays this
      screen (an `interaction` row -- Natural `INPUT`, Mantis
      `CONVERSE`/`SHOW` -- with `target` naming it), not a dedicated "mode"
      field: nothing in the fact store records an application mode (e.g.
      add/change/inquire) as such. Multiple modules reaching one screen are
      surfaced as multiple candidate modes to confirm with an SME, not
      asserted to be distinct modes.
    - **PF-key branches** reuse `structural.dispatch_edges_for_member`
      verbatim (the same derivation `mfdoc dispatch-map` already uses) --
      trigger value, routine(s) called, fields set -- scoped to the
      module(s) that display this particular screen, not every
      dispatching module system-wide.
    - **Mode/panel dispatch branches** (issue #129) reuse the exact same
      `dispatch_edges_for_member` derivation a second time, keyed on
      `mode_field` instead of `dispatch_field`, when a project supplies
      `options.overview.mode_field_pattern` (no built-in default -- see
      `conditions.mode_field_from_options`). Some dialects/coding styles
      dispatch a screen's PF-key meanings through a central mode/panel/
      transaction-code-keyed block rather than (or in addition to) a
      distinct subroutine call per PF-key branch; without also indexing
      that field, an inline mode/panel branch that sets a field or acts
      directly -- no `PERFORM`/`CALL` of its own -- is invisible to this
      brief even though it's exactly the kind of PF-key-driven navigation
      the matrix exists to surface. Each row is tagged with a `mechanism`
      column (`PF-key dispatch` vs. `mode/panel dispatch`) so a reader
      isn't left assuming both came from the same uniform mechanism when
      the two fact sources actually disagree.
    - **"outcome"** (exit / navigate / error / ...) is deliberately not
      classified here: the routine name and call kind are handed over
      cited, and the narrative pass is trusted to characterise the outcome
      from them (or mark it `unresolved`) -- inventing a canonical
      "PF3 always means exit" mapping from naming conventions alone would
      be exactly the kind of plausible-but-unverified assertion this tool
      exists to avoid.
    - **PF-key labels** are handed over as candidate literal text
      (`_screen_label_candidates`), not pre-matched to a trigger value --
      matching, say, a screen's `PF3=Exit` caption to the branch on
      `*PF-KEY = 'PF3'` is a one-line correlation a human or the narrative
      pass can make immediately from the two cited facts, and doing it
      here would risk a wrong match going uncorrected (no source line
      actually pairs a label with its key value together).

    A screen with no dispatch edges in the modules that display it is
    omitted outright (nothing for the matrix to add over what
    `mfdoc dispatch-map` already shows); a screen never displayed anywhere
    can't be reached in the first place, so it can't appear in "reachable
    from" either.
    """
    from . import structural  # local: avoids a circular import at load time (structural imports from this module)
    from .conditions import DISPATCH_FIELD

    if dispatch_field is None:
        dispatch_field = DISPATCH_FIELD

    out = ["# Fact brief: screen-and-key interface matrix", ""]
    out.append(
        "One section per screen/map with at least one dispatch branch "
        "(default: Natural's `*PF-KEY`; configurable via "
        "`options.overview.dispatch_field_pattern`) recorded against a "
        "module that displays it. \"Reachable from\" lists every module "
        "whose own source displays this screen -- the closest fact-store "
        "equivalent to an application \"mode\" (e.g. add/change/inquire), "
        "since no mode field is recorded as such; treat more than one "
        "reachable-from module as candidate modes to confirm with an SME, "
        "not as confirmed distinct modes. \"Candidate PF-key labels\" are "
        "literal text found on the screen itself -- matching a specific "
        "label to a specific PF-key value is a judgement call to make when "
        "writing the document, citing both; do not invent a match the "
        "source doesn't evidence. \"Outcome\" (exit/navigate/error/...) is "
        "not classified here either -- characterise it from the cited "
        "routine/call kind when writing, or mark it `unresolved`. Each "
        "branch row's \"mechanism\" column says whether it came from the "
        "PF-key dispatch field itself or, when `options.overview."
        "mode_field_pattern` is configured, a separate mode/panel dispatch "
        "field -- the two are not guaranteed to agree on the same set of "
        "actions per screen and must not be read as duplicates of each other."
    )
    out.append("")

    def _cell(text: str) -> str:
        # A literal `|` in source-derived text would otherwise be read as an
        # extra column delimiter and corrupt the row -- same escaping
        # rules_register's own table rendering already applies to
        # source-derived condition/literal text -- and redact() runs before
        # that escaping so a redaction placeholder can never itself
        # introduce an unescaped `|`.
        return redact(text).replace("|", "\\|")

    screens = conn.execute(
        "SELECT DISTINCT target FROM interaction "
        "WHERE target IS NOT NULL AND kind IN ('INPUT','CONVERSE','SHOW') "
        "ORDER BY target"
    ).fetchall()

    # Cached across every screen a member displays -- dispatch_edges_for_member
    # scans that member's whole rule_candidate set, so a member appearing as
    # a display point for more than one screen would otherwise repeat that
    # scan once per screen for no new information. Keyed by (member_id,
    # mechanism) since mode_field, when configured, is a second independent
    # scan of the same member against a different field pattern.
    edges_cache: dict[tuple[int, str], list] = {}

    def _dispatch_edges(member_id: int, field, mechanism: str) -> list:
        key = (member_id, mechanism)
        if key not in edges_cache:
            edges = structural.dispatch_edges_for_member(conn, member_id, dispatch_field=field)
            edges_cache[key] = [dict(e, mechanism=mechanism) for e in edges]
        return edges_cache[key]

    any_rows = False
    for s in screens:
        screen_name = s["target"]
        displaying = conn.execute(
            """
            SELECT DISTINCT m.id, m.name FROM interaction i JOIN member m ON m.id = i.member_id
             WHERE UPPER(i.target)=UPPER(?) AND i.kind IN ('INPUT','CONVERSE','SHOW')
             ORDER BY m.name
            """,
            (screen_name,),
        ).fetchall()
        if not displaying:
            continue

        # Keyed by member id, not name -- member names are only unique
        # together with library+dialect (see db.py's `UNIQUE(name, library,
        # dialect)`), so two distinct members displaying the same screen
        # could otherwise share a dict key and silently lose one's edges.
        edges_by_module: dict[int, tuple[str, list]] = {}
        for m in displaying:
            edges = list(_dispatch_edges(m["id"], dispatch_field, "PF-key dispatch"))
            if mode_field is not None:
                edges += _dispatch_edges(m["id"], mode_field, "mode/panel dispatch")
            if edges:
                edges_by_module[m["id"]] = (m["name"], edges)
        if not edges_by_module:
            continue
        any_rows = True

        out.append(f"## Screen/map `{screen_name}`")
        out.append("")
        out.append("### Reachable from")
        for m in displaying:
            row = conn.execute(
                "SELECT line_no FROM interaction WHERE member_id=? AND UPPER(target)=UPPER(?) "
                "AND kind IN ('INPUT','CONVERSE','SHOW') ORDER BY line_no LIMIT 1",
                (m["id"], screen_name),
            ).fetchone()
            out.append(f"- `{m['name']}` {_cite(m['name'], row['line_no'] if row else None)}")
        out.append("")

        labels = _screen_label_candidates(conn, screen_name)
        if labels:
            out.append(
                "### Candidate PF-key labels (literal text on the screen -- "
                "confirm which PF-key each belongs to before writing it into the matrix)"
            )
            for lab in labels:
                out.append(f"- {lab['cite']} `{redact(lab['text'])}`")
            out.append("")

        out.append("### PF-key / mode/panel dispatch branches, by module")
        out.append("")
        out.append("| module | trigger value | mechanism | branch | calls | fields set |")
        out.append("|---|---|---|---|---|---|")
        for _mid, (module_name, edges) in edges_by_module.items():
            for e in edges:
                calls = ", ".join(
                    f"`{_cell(c['callee_name'])}` ({c['call_kind']}) {_cite(module_name, c['line_no'])}"
                    for c in e["calls"]
                ) or "—"
                assigns = ", ".join(
                    f"`{_cell(a['field'])}` = `{_cell(a['literal'])}` {_cite(module_name, a['line_no'])}"
                    for a in e["assigns"]
                ) or "—"
                span = _cite(module_name, e["line_no"], e["end_line"])
                out.append(
                    f"| `{_cell(module_name)}` | `{_cell(e['trigger_value'])}` | {e['mechanism']} | "
                    f"{span} | {calls} | {assigns} |"
                )
        out.append("")

    if not any_rows:
        out.append(
            "No screen with both a display reference and a dispatch branch "
            "in one of its display modules was found. Either no screen in "
            "this index is dispatched on via the configured dispatch field, "
            "or the relevant display modules were not supplied in the index."
        )
        out.append("")

    return "\n".join(out) + "\n"


def json_index(conn) -> str:
    """Machine-readable dump for downstream tooling."""
    payload = {}
    for table in ("member", "entity", "entity_field", "entity_link", "data_access",
                  "call_edge", "transaction_marker", "interaction", "rule_candidate",
                  "message_ref", "job_step", "job_dd", "cics_resource", "gap", "metric"):
        # Iterate the cursor directly rather than .fetchall() -- this is a
        # full dump of every fact-store table (the largest ones, call_edge
        # and rule_candidate, scale with the whole ingested codebase), each
        # consumed exactly once right here, same reasoning as
        # graph.connected_components().
        payload[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
    return json.dumps(payload, indent=2)
