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

import json

from .brief import _cite, _rule_id
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
        "SELECT * FROM rule_candidate WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()

    inserted: list[dict] = []
    # Numbered over *every* rule_candidate row, matching brief.py's
    # `## Candidate business rules` enumeration exactly -- so a scenario's
    # BR-nnn id is the same id a reviewer sees in the module doc/rules
    # register, not a second, disagreeing numbering scheme for the same rule.
    for idx, r in enumerate(rules):
        n = idx + 1
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


def test_case_brief(conn, member_name: str, redact=None) -> str:
    """The only input the render stage (test-gen/test-batch) sees for one
    member -- plain text, every scenario already cited, mirroring
    brief.module_brief's role for narrative docs. Includes the member's own
    parameter contract once (shared by every scenario) plus a section per
    test_case row.
    """
    from .redact import NULL_REDACTOR
    redact = redact or NULL_REDACTOR

    # Same refusal brief.module_brief makes for the identical case: a bare
    # name is only unique together with library+dialect, so a second member
    # sharing this name would otherwise get its scenarios silently merged
    # into this brief under colliding BR-nnn ids.
    matches, ambiguous_libs = resolve_member_by_name(conn, member_name, columns="library, system")
    if ambiguous_libs:
        libs = ", ".join(ambiguous_libs)
        return (
            f"# Test brief: {member_name}\n\nMember name is ambiguous across libraries "
            f"({libs}). Re-run with a library-qualified name.\n"
        )
    system = matches[0]["system"] if matches else None

    rows = conn.execute(
        """
        SELECT tc.* FROM test_case tc JOIN member m ON m.id = tc.member_id
         WHERE UPPER(m.name)=UPPER(?) ORDER BY tc.id
        """,
        (member_name,),
    ).fetchall()
    if not rows:
        return f"# Test brief: {member_name}\n\nNo derived test_case rows for this member. Run `mfdoc test-plan` first.\n"

    out = [f"# Test brief: {member_name}", "", f"- system: {system or 'unknown'}", ""]
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

    out.append("## Scenarios")
    out.append("")
    for r in rows:
        when = json.loads(r["when_json"])
        then = json.loads(r["then_json"])
        out.append(f"### {r['scenario_name']} ({r['status']}) [[{r['citation']}]]")
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
