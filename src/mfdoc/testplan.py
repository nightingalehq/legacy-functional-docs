"""Test-plan derive — turns rule_candidate/variable/data_access facts into
test_case rows.

Same discipline as graph.py: nothing here reads source prose or invents an
expected outcome. A test case's `then_json` carries the exact cited source
lines that execute inside a branch, not a guessed assertion value -- turning
that excerpt into a concrete assertion is the narrate stage's job (test-gen/
test-batch), the same way brief.py hands rule_candidate text to the model
rather than paraphrasing it during derive.

Eligible units are whatever `batch.select_batch_members` already considers
batchable (Natural/Mantis program|subprogram|subroutine|copycode) -- the
same "callable unit" definition the narrative batch stage uses, so a member
that can get a generated doc can also get a generated test plan.
"""

from __future__ import annotations

import hashlib
import json

from .citations import _cite, _rule_id, numbered_rule_candidates
from .db import group_members_by_name, insert, resolve_member_by_name

# rule_candidate construct *prefixes* treated as branch/decision points worth
# a scenario each. natural.py records one row per statement, not one per
# block, and annotates some headers with descriptive suffixes (e.g. "IF NO
# RECORDS FOUND", "DECIDE FOR FIRST CONDITION") rather than the bare keyword
# db.py's schema comment lists -- matching by prefix catches those variants.
# WHILE/FOR/REPEAT/LOOP are iteration constructs, not decision points -- a
# generated "does this loop run" test would either be trivial or would have
# to guess loop-trip-count behaviour the facts don't state, so they're left
# to the testability advisory (task 2) rather than turned into scenarios here.
# A bare "DECIDE ON"/"DECIDE FOR..." header itself is excluded (no condition
# of its own -- its WHEN rows are the actual branches).
# ELSE is included even though it carries no condition of its own: it is the
# IF's negative path and deserves its own scenario, and -- just as important
# -- it must count as a branch boundary so the preceding IF-true branch's
# body reconstruction (_branch_body_lines) stops at ELSE instead of running
# on into the else-clause's own consequence.
BRANCH_CONSTRUCT_PREFIXES = ("IF", "ELSE", "WHEN", "CASE", "ON ERROR", "AT BREAK")


def _is_branch_row(r) -> bool:
    c = r["construct"]
    if c.startswith("DECIDE"):
        return False
    return c.startswith(BRANCH_CONSTRUCT_PREFIXES)


def _is_header_row(r) -> bool:
    """Any row that opens a branch of its own -- used to find where a
    branch's body ends: at the next sibling-or-shallower header, not merely
    the next row at the same depth (a WHEN's own consequence statements sit
    at the *same* depth as the WHEN itself, not deeper, so "depth" alone
    can't tell a body statement from a sibling branch)."""
    return _is_branch_row(r) or r["construct"].startswith("DECIDE")


def _parameters(conn, mid: int) -> list[dict]:
    rows = conn.execute(
        "SELECT name, format, length, line_no FROM variable "
        "WHERE member_id=? AND scope IN ('parameter','entry') ORDER BY line_no",
        (mid,),
    ).fetchall()
    return [{"name": r["name"], "format": r["format"], "length": r["length"]} for r in rows]


def _mock_targets(conn, mid: int) -> dict:
    """Entities and callees a unit test of this member would need to stub,
    named from data_access/call_edge facts -- never guessed. Internal
    subroutines (PERFORM_INTERNAL) aren't listed: they run in-process, there
    is nothing external to mock."""
    entities = [
        r["entity_name"] for r in conn.execute(
            "SELECT DISTINCT entity_name FROM data_access "
            "WHERE member_id=? AND entity_name IS NOT NULL ORDER BY entity_name",
            (mid,),
        ).fetchall()
    ]
    # PERFORM_INTERNAL runs in-process; INCLUDE pulls in a data area/copycode's
    # declarations, not callable behaviour -- neither is something a unit
    # test would need to mock (see testadvisor._outbound_calls_by_member).
    callees = [
        r["callee_name"] for r in conn.execute(
            "SELECT DISTINCT callee_name FROM call_edge "
            "WHERE caller_id=? AND call_kind NOT IN ('PERFORM_INTERNAL','INCLUDE') "
            "ORDER BY callee_name",
            (mid,),
        ).fetchall()
    ]
    return {"entities": entities, "callees": callees}


def _branch_body_lines(rules: list, idx: int) -> list[int]:
    """Line numbers of the statements a branch at `rules[idx]` controls.

    natural.py doesn't record an explicit end_line (there is no block-close
    token in the source in every case, e.g. `DECIDE FOR ... WHEN ...` has no
    per-WHEN closer), so the body is reconstructed from depth + row order
    instead: a body statement is either strictly deeper than the branch
    (an IF's consequence), or at the *same* depth and not itself a header
    (a WHEN's own consequence, which natural.py records as a sibling, not a
    child). Reconstruction stops at the first row that is shallower, or at
    the same depth but itself a header -- i.e. the next sibling branch.

    IF is the one construct where that same-depth rule doesn't apply to its
    *terminator*: natural.py's ELSE doesn't bump depth again (it's recorded
    at the IF's own body depth, `header depth + 1`, not at the IF header's
    depth -- see `natural._match_rules`'s RE_ELSE branch), unlike a WHEN/
    CASE sibling which shares its header's depth exactly. Left unhandled,
    an IF's true-branch reconstruction would run straight through ELSE and
    into the else-clause's own consequence. So for an IF header specifically,
    also stop at an ELSE row one level deeper than the header -- its own
    depth, not the header's.
    """
    header = rules[idx]
    depth = header["depth"] or 0
    is_if = header["construct"] == "IF"
    body: list[int] = []
    for r in rules[idx + 1:]:
        rdepth = r["depth"] or 0
        if rdepth < depth:
            break
        if rdepth == depth and _is_header_row(r):
            break
        if is_if and r["construct"] == "ELSE" and rdepth == depth + 1:
            break
        body.append(r["line_no"])
    return body


def _branch_excerpt(conn, mid: int, name: str, header_line: int, body_lines: list[int]) -> dict:
    """Exact source text for a branch's body, as evidence -- not an
    assertion. An empty `body_lines` (a branch with no reconstructable
    consequence, e.g. a bare `WHEN NONE`) is reported honestly as an empty
    excerpt rather than falling back to guessing at the header line's own
    text."""
    if not body_lines:
        return {"citation": _cite(name, header_line), "source_excerpt": []}
    first, last = body_lines[0], body_lines[-1]
    rows = conn.execute(
        "SELECT line_no, text FROM source_line WHERE member_id=? AND line_no BETWEEN ? AND ? "
        "ORDER BY line_no",
        (mid, first, last),
    ).fetchall()
    return {
        "citation": _cite(name, first, last if last != first else None),
        "source_excerpt": [r["text"] for r in rows],
    }


def member_rule_fingerprint(conn, member_name: str) -> str | None:
    """A fingerprint of exactly the input that determines this member's
    `BR-nnn` numbering and which rows contribute a scenario at all:
    `rule_candidate`'s own `(id, line_no, construct)` for every row,
    ordered by `line_no` -- the same ordering `build_member_test_cases`
    feeds through `numbered_rule_candidates()` (which assigns an ordinal
    to *every* row positionally, branch or not) before filtering with
    `_is_branch_row` to decide which of those ordinals actually become a
    `BR-nnn` scenario. `None` if `member_name` doesn't resolve to exactly
    one member (unknown, or ambiguous across libraries) -- a caller with no
    real member to fingerprint, not an error this function should raise.

    Deliberately over `rule_candidate`, not `test_case`: a `derive` rebuild
    can insert, remove, or reorder `rule_candidate` rows for a member
    without `mfdoc test-plan` having re-run yet to reflect that in
    `test_case`. Comparing this fingerprint (recorded at sidecar-write time
    by `testbatch.write_test_doc_with_sidecar`, in the document's own front
    matter) against a freshly computed one is what lets `validate.
    validate_test_doc` detect a genuine positional shift directly (issue
    #195's staleness guard) instead of inferring it from whether an old
    sidecar's `BR-nnn` ids happen to still resolve against current
    `test_case` rows -- an id-overlap check alone cannot tell a real shift
    apart from one where the old id set, by coincidence or because the
    insertion/removal happened entirely *after* the sidecar's own range,
    remains a literal subset of the current one.

    `ORDER BY line_no, id`, deliberately matching `brief.
    fetch_rule_candidate_rows`/`build_member_test_cases`'s own query
    verbatim (all three `SELECT * FROM rule_candidate WHERE member_id=?
    ORDER BY line_no, id`) -- same-line rows are explicitly supported
    (natural.py can record more than one rule_candidate per source line),
    and this fingerprint exists to describe *the same ordering*
    `numbered_rule_candidates()` actually numbers from, not a different,
    independently-invented one. `id` (the row's own insertion-order
    primary key) is the explicit tie-break already used elsewhere for
    this exact table (`graph.py`/`structural.py`'s own `rule_candidate`
    queries) -- bare `ORDER BY line_no` leaves same-line rows' relative
    order to SQLite's unspecified tie behaviour, which a later query-plan
    or index change could alter with no fact actually changing, silently
    reordering every tied id and marking every existing sidecar's
    fingerprint stale for no real reason (Copilot review). Naming `id`
    here, not leaving it implicit, is what makes it *this* function's own
    explicit contract instead of an accident of whatever plan SQLite picks
    today.

    `construct` is hashed alongside `(id, line_no)` too (Copilot review):
    `build_member_test_cases` only turns *branch* rows (`_is_branch_row`,
    which reads `construct`) into `BR-nnn` scenarios -- ordinals are
    assigned to every row *before* that filter runs, so a `derive`/
    dialect-scanner change that reclassifies an existing row's `construct`
    (e.g. between a branch construct and `DECIDE ON`, which
    `_is_branch_row` excludes) does *not* shift any other row's ordinal;
    it only adds or removes *that one row's own* scenario from the set
    `test_case` should have, leaving every other id exactly where it was.
    That's still a real change this fingerprint must catch: without
    `construct`, the row's own `(id, line_no)` pair is unaffected by a
    construct-only reclassification, so the fingerprint would stay
    unchanged and let a sidecar/manifest that still includes (or still
    lacks) that one now-stale scenario keep looking authoritative
    indefinitely -- the same effect `member_test_case_aligned_with_rule_
    candidate`'s own exact-equality check exists to catch on the write
    side."""
    rows, ambiguous = resolve_member_by_name(conn, member_name)
    if ambiguous or not rows:
        return None
    mid = rows[0]["id"]
    rc_rows = conn.execute(
        "SELECT id, line_no, construct FROM rule_candidate WHERE member_id=? ORDER BY line_no, id", (mid,)
    ).fetchall()
    joined = "|".join(f"{r['id']}:{r['line_no']}:{r['construct']}" for r in rc_rows)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def doc_rule_fingerprint(conn, member_names: list[str]) -> str | None:
    """`member_rule_fingerprint`, combined across every member a generated
    test document's `sources` front matter names (normally exactly one,
    but not guaranteed) -- `None` if *any* of them doesn't resolve (same
    "don't guess" contract as the single-member version), since a partial
    fingerprint would be worse than none: a caller falling back to a
    weaker staleness signal for a document it can't fully fingerprint is
    safer than this function silently fingerprinting only part of it."""
    parts = []
    for name in sorted(member_names, key=str.upper):
        fp = member_rule_fingerprint(conn, name)
        if fp is None:
            return None
        parts.append(f"{name.upper()}={fp}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def member_test_case_aligned_with_rule_candidate(conn, member_name: str) -> bool:
    """Whether `member_name`'s current `rule_candidate_id`-linked `test_case`
    rows exactly match its *current* `rule_candidate` rows -- False
    whenever a `derive`/`classify-rules` rebuild has inserted, removed, or
    reordered a `rule_candidate` row for this member since `mfdoc
    test-plan` last ran, and `test_case` hasn't caught up yet (a
    currently-unresolvable member counts as misaligned too: nothing to
    compare against).

    Exists to guard `testbatch.write_test_doc_with_sidecar`'s fingerprint
    stamp (Copilot review on issue #195's fix): that fingerprint is a pure
    function of `rule_candidate`, computed *at write time* -- but the
    document just rendered (and the sidecar this stamps it onto) was built
    from whatever `test_case` rows `test_case_brief` fed the model, which
    can predate a `rule_candidate` change test-plan hasn't caught up to
    yet. Stamping the *current* rule_candidate fingerprint onto that
    still-old-numbered content bakes in a fingerprint that describes a
    corpus state the sidecar doesn't actually reflect; once `mfdoc
    test-plan` does catch up and a later render produces a manifest with
    the new/renumbered ids, that stamped fingerprint (unchanged, since
    rule_candidate itself hasn't moved again) still matches the current
    recomputation, making the stale sidecar look current and the new
    manifest's ids get rejected -- the same class of false positive issue
    #195 exists to close, reintroduced at the write side instead of the
    read side.

    Exact equality of the `{rule_candidate_id: scenario_name}` mapping on
    both sides, not a one-directional subset check (Copilot review on an
    earlier version of this function, which only checked "every expected
    id has a matching test_case row" and missed the opposite direction):
    a `derive` rebuild can *remove* a `rule_candidate` row before
    `test-plan` re-runs just as easily as it can insert one -- `test_case`
    then still carries a `unit` row linked to that now-gone
    `rule_candidate_id`, which the model's `test_case_brief`-driven
    render still cites. Stamping a fingerprint computed from the smaller,
    already-shrunk `rule_candidate` set in that state has the identical
    consequence as the missing-id case: once `test-plan` removes that
    stale `test_case` row too, the fingerprint (unchanged, since
    `rule_candidate` itself doesn't move again) still matches, and the
    new, now-shorter manifest gets rejected against a sidecar that still
    cites the long-gone id. Equality also subsumes the reorder case a
    prior round added `rule_candidate_id` linkage for: a name-set match
    with a differing `rule_candidate_id` for the same key already fails
    the dict comparison.

    Only `unit`-kind, `rule_candidate_id`-linked `test_case` rows are
    compared -- the only rows `build_member_test_cases` derives
    one-for-one from a branch `rule_candidate` row (`_is_branch_row`), via
    the same `numbered_rule_candidates` ordinal every other BR-numbering
    consumer uses. An overlay-sourced `unit` row with no such link
    (`rule_candidate_id IS NULL`) is excluded from `current` entirely --
    it carries no positional relationship to `rule_candidate` to compare
    against, and would only add noise (a false mismatch on every check)
    here."""
    rows, ambiguous = resolve_member_by_name(conn, member_name)
    if ambiguous or not rows:
        return False
    mid = rows[0]["id"]
    # `rows[0]["name"]`, not the raw `member_name` argument -- scenario_name
    # is built from the member's own canonical (stored-case) name
    # (`build_member_test_cases`'s own `name` param, always `m["name"]`
    # from the resolved row), and `_rule_id` does no case normalization of
    # its own -- comparing against a differently-cased `member_name` here
    # would report every scenario as "misaligned" even when it isn't.
    canonical_name = rows[0]["name"]
    rc_rows = conn.execute(
        "SELECT * FROM rule_candidate WHERE member_id=? ORDER BY line_no, id", (mid,)
    ).fetchall()
    expected = {
        r["id"]: _rule_id(canonical_name, n)
        for n, r in numbered_rule_candidates(rc_rows) if _is_branch_row(r)
    }
    current_rows = conn.execute(
        "SELECT scenario_name, rule_candidate_id FROM test_case "
        "WHERE member_id=? AND kind='unit' AND rule_candidate_id IS NOT NULL", (mid,)
    ).fetchall()
    # A dict comprehension keyed by rule_candidate_id would silently keep
    # only the *last* row for a given id and never notice two `unit` rows
    # sharing one (Copilot review): nothing in the schema enforces that
    # link's uniqueness (unlike rule_theme's own `UNIQUE(rule_candidate_id)`
    # -- see db.py), so a duplicate is a real, if unusual, misalignment
    # `build_member_test_cases`'s one-row-per-branch-row contract never
    # produces on its own -- collapsing it here instead of detecting it
    # would let `write_test_doc_with_sidecar` stamp a fingerprint onto
    # content this function never actually confirmed is one-for-one.
    current: dict[int, str] = {}
    for row in current_rows:
        rc_id = row["rule_candidate_id"]
        if rc_id in current:
            return False
        current[rc_id] = row["scenario_name"]
    return expected == current


def build_member_test_cases(conn, mid: int, name: str, overlay: dict | None = None) -> list[dict]:
    """Deterministically derive test_case rows for one member. Returns the
    rows inserted (as dicts) for callers that want to report on this run
    without re-querying.

    `overlay` is test-overlay.yml's loaded content (see testoverlay.py); a
    scenario's status only ever comes from an overlay entry a human has
    promoted past `draft` -- everything else defaults to
    `characterization`, never a guess at "this looks like a bug"."""
    from .testoverlay import overlay_status_for

    overlay = overlay or {}
    params = _parameters(conn, mid)
    mocks = _mock_targets(conn, mid)
    given = {"parameters": params, "mocks": mocks}

    rules = conn.execute(
        "SELECT * FROM rule_candidate WHERE member_id=? ORDER BY line_no, id", (mid,)
    ).fetchall()

    inserted: list[dict] = []
    # Numbered over *every* rule_candidate row via numbered_rule_candidates()
    # -- the same ordinal assignment brief.py's `## Candidate business
    # rules` enumeration uses -- so a scenario's BR-nnn id is the same id a
    # reviewer sees in the module doc/rules register, not a second,
    # disagreeing numbering scheme for the same rule.
    for n, r in numbered_rule_candidates(rules):
        idx = n - 1
        if not _is_branch_row(r):
            continue
        when = {
            "construct": r["construct"],
            "condition": r["condition"],
            "citation": _cite(name, r["line_no"]),
        }
        body_lines = _branch_body_lines(rules, idx)
        then = _branch_excerpt(conn, mid, name, r["line_no"], body_lines)
        citation = when["citation"].strip("[]")
        scenario_name = f"{_rule_id(name, n)}"
        row = dict(
            member_id=mid,
            kind="unit",
            rule_candidate_id=r["id"],
            scenario_name=scenario_name,
            given_json=json.dumps(given),
            when_json=json.dumps(when),
            then_json=json.dumps(then),
            status=overlay_status_for(overlay, scenario_name),
            citation=citation,
            confidence=r["confidence"],
        )
        row["id"] = insert(conn, "test_case", **{k: v for k, v in row.items() if k != "id"})
        inserted.append(row)
    return inserted


def test_plan_register(conn, redact=None) -> str:
    """A flat, system-wide index of every derived test_case, mirroring
    brief.rules_register's shape and its "regenerate, don't hand-edit"
    contract -- this is a deterministic report, not a narrative document.
    Carries the same minimal `doc_type: register` front matter for the same
    reason (see brief.rules_register)."""
    from .redact import NULL_REDACTOR
    redact = redact or NULL_REDACTOR

    out = ["---", 'title: "System-wide test-plan register"', "doc_type: register", "---", "",
           "# System-wide test-plan register", "", (
        "Every scenario `mfdoc test-plan` derived from the fact store, keyed "
        "by the same `MEMBER:BR-nnn` id its source rule carries in the "
        "module doc and rules register. Regenerate with `mfdoc test-plan` "
        "after any source change; do not hand-edit. `status` defaults to "
        "`characterization` until a human promotes an entry via "
        "`test-overlay.yml`."
    ), ""]
    out.append("| scenario | member | kind | status | construct | condition | citation |")
    out.append("|---|---|---|---|---|---|---|")
    rows = conn.execute(
        """
        SELECT tc.scenario_name, m.name AS member, tc.kind, tc.status,
               tc.when_json, tc.citation
          FROM test_case tc JOIN member m ON m.id = tc.member_id
         ORDER BY m.name, tc.id
        """
    ).fetchall()
    for r in rows:
        when = json.loads(r["when_json"])
        out.append(
            f"| `{r['scenario_name']}` | `{r['member']}` | {r['kind']} | {r['status']} | "
            f"`{when.get('construct','')}` | `{redact(when.get('condition')) or ''}` | "
            f"[[{r['citation']}]] |"
        )
    out.append("")
    return "\n".join(out) + "\n"


def fetch_test_case_rows(conn, member_name: str):
    """(system, rows, ambiguous_libs) for member_name -- the same
    resolve+query test_case_brief has always done, factored out so
    testbatch.py's chunked render path (see DEFAULT_MAX_SCENARIOS_PER_CALL)
    can decide whether to chunk *before* asking for a brief, using the exact
    same row set/order test_case_brief itself would build one from.

    Same refusal brief.module_brief makes for the identical case: a bare
    name is only unique together with library+dialect, so a second member
    sharing this name would otherwise get its scenarios silently merged
    into one brief under colliding BR-nnn ids -- `ambiguous_libs` is
    non-empty (and `rows` empty) in that case, for the caller to report."""
    matches, ambiguous_libs = resolve_member_by_name(conn, member_name, columns="library, system")
    if ambiguous_libs:
        return None, [], ambiguous_libs
    system = matches[0]["system"] if matches else None
    # LEFT JOIN rule_candidate for its line_no -- lets a caller group
    # scenarios by the routine (see brief.routine_for_line) their
    # originating rule falls in, the same way module docs group business
    # rules. A test_case with no rule_candidate_id (none currently derive
    # that way, but the column is nullable) still comes back, just with a
    # NULL rule_line_no.
    #
    # Ordered by rc.line_no (source order), not tc.id (insertion order):
    # testbatch.py's routine-aware chunking (brief.routine_aware_chunk_ranges)
    # assumes consecutive rows sharing a routine are contiguous, which only
    # holds if rows come back in the same source-line order rule_candidate
    # rows do -- insertion order happening to match that today is not a
    # guarantee build_member_test_cases makes going forward. `rc.line_no IS
    # NULL` sorts rows with no rule_candidate link (line_no NULL) after
    # every row that has one, rather than SQLite's default of NULL-first.
    rows = conn.execute(
        """
        SELECT tc.*, rc.line_no AS rule_line_no
          FROM test_case tc
          JOIN member m ON m.id = tc.member_id
          LEFT JOIN rule_candidate rc ON rc.id = tc.rule_candidate_id AND rc.member_id = tc.member_id
         WHERE UPPER(m.name)=UPPER(?)
         ORDER BY rc.line_no IS NULL, rc.line_no, tc.id
        """,
        (member_name,),
    ).fetchall()
    return system, rows, []


def _brief_header(member_name: str, system: str | None, rows, title_suffix: str = "") -> list[str]:
    """The Parameters/Dependencies section every brief for this member
    shares, regardless of which (or how many) of its scenarios are being
    rendered this call -- derived from any one row's `given_json`, since
    that's the member's own interface, not something that varies per
    scenario."""
    out = [f"# Test brief: {member_name}{title_suffix}", "", f"- system: {system or 'unknown'}", ""]
    given0 = json.loads(rows[0]["given_json"])
    if given0["parameters"]:
        out.append("## Parameters (this member's own interface)")
        for p in given0["parameters"]:
            spec = f" ({p['format'] or ''}{p['length'] or ''})" if (p["format"] or p["length"]) else ""
            out.append(f"- `{p['name']}`{spec}")
        out.append("")
    if given0["mocks"]["entities"] or given0["mocks"]["callees"]:
        out.append("## Dependencies to mock (see `mfdoc test-advisory` for named seams)")
        for e in given0["mocks"]["entities"]:
            out.append(f"- entity: `{e}`")
        for c in given0["mocks"]["callees"]:
            out.append(f"- callee: `{c}`")
        out.append("")
    return out


def _brief_scenarios(rows, redact, routines: list[dict] | None = None) -> list[str]:
    """The per-scenario `### {scenario_name} ...` sections -- the part of
    a test brief that *does* vary between a full-member brief and one
    chunk's brief, so both test_case_brief and test_case_brief_chunk render
    it from whatever `rows` they're given rather than always the member's
    full set. `routines` (see brief.fetch_routines), when given, tags each
    scenario with the routine its originating rule falls in -- the same
    grouping module docs use, so generated tests can be organised (and, in
    testbatch.py, chunked) along the same lines rather than an arbitrary
    scenario count."""
    from .brief import routine_for_line

    out = []
    for r in rows:
        when = json.loads(r["when_json"])
        then = json.loads(r["then_json"])
        out.append(f"### {r['scenario_name']} ({r['status']}) [[{r['citation']}]]")
        if routines and r["rule_line_no"] is not None:
            routine = routine_for_line(routines, r["rule_line_no"])
            if routine:
                out.append(f"- routine: `{routine['name']}`")
        out.append(f"- construct: `{when['construct']}`")
        if when.get("condition"):
            out.append(f"- condition: `{redact(when['condition'])}`")
        out.append(f"- branch citation: {when['citation']}")
        if then["source_excerpt"]:
            out.append(f"- observed consequence ({then['citation']}), verbatim source:")
            for line in then["source_excerpt"]:
                out.append(f"  `{redact(line)}`")
        else:
            out.append(
                "- observed consequence: none reconstructable from source facts -- "
                "do not invent one; write the scenario up to the branch decision only, "
                "or mark it `unresolved`."
            )
        out.append("")
    return out


def test_case_brief(conn, member_name: str, redact=None, sme_notes=None) -> str:
    """The only input the render stage (test-gen/test-batch) sees for one
    member -- plain text, every scenario already cited, mirroring
    brief.module_brief's role for narrative docs. Includes the member's own
    parameter contract once (shared by every scenario) plus a section per
    test_case row.

    `sme_notes` (see `sme_notes.py`), when given, appends the same
    advisory-only "SME notes" section module_brief/entity_brief/
    executive_brief do -- see `brief._sme_notes_section`.
    """
    from .brief import _sme_notes_section
    from .redact import NULL_REDACTOR
    redact = redact or NULL_REDACTOR

    system, rows, ambiguous_libs = fetch_test_case_rows(conn, member_name)
    if ambiguous_libs:
        libs = ", ".join(ambiguous_libs)
        return (
            f"# Test brief: {member_name}\n\nMember name is ambiguous across libraries "
            f"({libs}). Re-run with a library-qualified name.\n"
        )
    if not rows:
        return f"# Test brief: {member_name}\n\nNo derived test_case rows for this member. Run `mfdoc test-plan` first.\n"

    from .brief import fetch_routines
    routines = fetch_routines(conn, rows[0]["member_id"])

    out = _brief_header(member_name, system, rows)
    out.append("## Scenarios")
    out.append("")
    out.extend(_brief_scenarios(rows, redact, routines))
    out.extend(_sme_notes_section(redact, sme_notes, member_name))
    return "\n".join(out) + "\n"


def test_case_brief_chunk(member_name: str, system: str | None, rows, chunk_index: int,
                           chunk_count: int, redact=None, routines: list[dict] | None = None,
                           sme_notes=None) -> str:
    """A test brief covering only `rows` -- one slice of this member's full
    test_case set -- for testbatch.py's chunked render path. Same
    Parameters/Dependencies header every chunk of this member shares (any
    row's `given_json` gives it, they're all the same member) plus only
    this chunk's `## Scenarios` section, so a chunk's prompt is
    proportional to the chunk, not the whole member. `system`/`rows` are
    passed in rather than looked up again, since the caller (testbatch.py's
    chunk-or-not decision) already called fetch_test_case_rows once for
    this member; `routines` likewise, since chunking itself needs the same
    list (see brief.routine_aware_chunk_ranges) before any brief is built.

    `sme_notes`, when given, is appended to *every* chunk (not just the
    last) -- the note is member-scoped, not scenario-scoped, so each chunk
    still needs it in view of the model narrating that chunk independently."""
    from .brief import _sme_notes_section
    from .redact import NULL_REDACTOR
    redact = redact or NULL_REDACTOR

    out = _brief_header(member_name, system, rows, title_suffix=f" (chunk {chunk_index}/{chunk_count})")
    out.append(f"## Scenarios (this chunk only -- {len(rows)} of the member's full set)")
    out.append("")
    out.extend(_brief_scenarios(rows, redact, routines))
    out.extend(_sme_notes_section(redact, sme_notes, member_name))
    return "\n".join(out) + "\n"


def run_all(conn, member_name: str | None = None, overlay_path=None) -> dict:
    """Rebuild test_case rows from a clean slate for the requested scope.

    Unscoped (member_name=None) rebuilds every batchable member's plan, the
    same all-or-nothing rebuild graph.run_all() does for its derived gaps --
    test_case rows are a deterministic function of already-derived facts
    plus whatever `overlay_path` (test-overlay.yml) currently has promoted,
    so there's no reason to carry a prior run's rows forward instead of
    recomputing them.

    A bare member name is only unique together with library+dialect (see
    the `UNIQUE(name, library, dialect)` constraint in db.py) -- two
    batchable members can share a name across libraries. Skip a name that
    resolves to more than one member rather than guessing which library's
    facts apply, the same refusal brief.module_brief/brief.rules_register/
    testadvisor.run_all make for the identical case -- guessing would merge
    two unrelated members' scenarios under colliding `MEMBER:BR-nnn` ids.
    An ambiguous name is reported back in the result's `ambiguous` list
    rather than silently dropped, and -- since it was never rebuilt --
    its existing test_case rows are left untouched rather than deleted.
    """
    from .batch import BATCHABLE_DIALECTS, BATCHABLE_OBJECT_TYPES
    from .testoverlay import load_overlay

    overlay = load_overlay(overlay_path) if overlay_path else {}

    ambiguous: list[str] = []
    if member_name:
        rows, ambiguous_libs = resolve_member_by_name(
            conn, member_name, columns="id, name",
            dialect_in=BATCHABLE_DIALECTS, object_type_in=BATCHABLE_OBJECT_TYPES,
        )
        if ambiguous_libs:
            ambiguous.append(member_name)
            members = []
        else:
            members = rows
            if rows:
                conn.execute("DELETE FROM test_case WHERE member_id=?", (rows[0]["id"],))
    else:
        placeholders_d = ",".join("?" * len(BATCHABLE_DIALECTS))
        placeholders_t = ",".join("?" * len(BATCHABLE_OBJECT_TYPES))
        all_rows = conn.execute(
            f"""
            SELECT id, name FROM member
             WHERE dialect IN ({placeholders_d}) AND object_type IN ({placeholders_t})
             ORDER BY name
            """,
            (*BATCHABLE_DIALECTS, *BATCHABLE_OBJECT_TYPES),
        ).fetchall()
        unambiguous, ambiguous = group_members_by_name(all_rows)
        members = list(unambiguous.values())
        conn.execute("DELETE FROM test_case")

    total = 0
    for m in members:
        total += len(build_member_test_cases(conn, m["id"], m["name"], overlay=overlay))
    conn.commit()
    result = {"members": len(members), "test_cases": total}
    if ambiguous:
        result["ambiguous"] = ambiguous
    return result
