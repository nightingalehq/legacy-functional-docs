"""Testability advisory — classifies each batchable member for test
generation and names, from facts alone, what a unit test of it would need.

Deterministic, no model call, same rank as graph.py: everything here is a
join over data_access/call_edge/transaction_scopes, not a judgement call. The
one piece of prose it emits -- a refactor-seam suggestion -- is built from a
fixed template parameterised only by cited facts (verb, entity/callee name,
line), never by reading or paraphrasing surrounding source. It is advice for
a human to act on, not a code transform: this module never touches source.
"""

from __future__ import annotations

from .graph import transaction_scopes

# Classifications, in the order a reviewer should act on them.
PURE = "pure"                    # no data_access, no external calls -> unit test directly
NEEDS_MOCK = "needs-mock"        # data_access/calls present -> unit test, with named seams
INTEGRATION_ONLY = "integration-only"  # a transaction scope spans >1 entity
UNTESTABLE_GAP = "untestable-gap"      # dynamic/unresolved call blocks safe mocking


def _outbound_calls_by_member(conn, member_ids: list[int]) -> dict[int, list[dict]]:
    # PERFORM_INTERNAL runs in-process (nothing external to mock); INCLUDE
    # pulls in a data area/copycode's *declarations*, not a callable
    # behaviour -- there is nothing to stub or characterize about it, so
    # treating it as a mockable dependency (or an unresolved-call gap when
    # its source wasn't supplied) would misclassify a plain data-area
    # reference as an untested external call.
    #
    # One batched fetch for every member instead of a query per member (see
    # brief.rules_register's identical rationale) -- run_all iterates the
    # whole batchable-member list, so a per-member round trip scales with
    # system size.
    if not member_ids:
        return {}
    placeholders = ",".join("?" * len(member_ids))
    out: dict[int, list[dict]] = {}
    for r in conn.execute(
        f"SELECT caller_id, callee_name, call_kind, dynamic, resolved, line_no FROM call_edge "
        f"WHERE caller_id IN ({placeholders}) AND call_kind NOT IN ('PERFORM_INTERNAL','INCLUDE') "
        "ORDER BY caller_id, line_no",
        member_ids,
    ).fetchall():
        out.setdefault(r["caller_id"], []).append(dict(r))
    return out


def _data_accesses_by_member(conn, member_ids: list[int]) -> dict[int, list[dict]]:
    if not member_ids:
        return {}
    placeholders = ",".join("?" * len(member_ids))
    out: dict[int, list[dict]] = {}
    for r in conn.execute(
        f"SELECT member_id, verb, crud, entity_name, line_no FROM data_access "
        f"WHERE member_id IN ({placeholders}) AND entity_name IS NOT NULL ORDER BY member_id, line_no",
        member_ids,
    ).fetchall():
        out.setdefault(r["member_id"], []).append(dict(r))
    return out


def _seam_suggestions(accesses: list[dict], calls: list[dict], name: str) -> list[str]:
    """One prose suggestion per distinct entity/callee a unit test would
    otherwise have to hit for real. Deduplicated by target, not by line, so
    a heavily-read entity gets one suggestion, not one per READ statement."""
    out = []
    seen = set()
    for a in accesses:
        key = ("entity", a["entity_name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            f"Extract the `{a['entity_name']}` access (first seen at "
            f"[[{name}:{a['line_no']}]], `{a['verb']}`) behind a seam "
            f"(a lookup/repository call this unit takes as a parameter or "
            f"can have substituted) so a unit test can supply fixture data "
            f"for `{a['entity_name']}` instead of a live database call."
        )
    for c in calls:
        if c["dynamic"]:
            continue  # can't name a fixed seam for a target that isn't fixed; see gaps
        key = ("callee", c["callee_name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            f"Extract the `{c['call_kind']}` to `{c['callee_name']}` "
            f"([[{name}:{c['line_no']}]]) behind a seam so a unit test can "
            f"substitute a stub/fake for `{c['callee_name']}` instead of "
            f"invoking it for real."
        )
    return out


def _gaps(calls: list[dict], name: str) -> list[str]:
    out = []
    for c in calls:
        if c["dynamic"]:
            out.append(
                f"[[{name}:{c['line_no']}]] `{c['call_kind']}` target is dynamic "
                f"(a variable, not a literal) -- the callee set is unknown, so no "
                f"fixed seam/mock can be named; a test can only be written once "
                f"the possible targets are confirmed with an SME."
            )
        elif not c["resolved"]:
            out.append(
                f"[[{name}:{c['line_no']}]] `{c['call_kind']}` target "
                f"`{c['callee_name']}` has no source in the ingested set -- its "
                f"behaviour can't be characterized, so this call can only be "
                f"stubbed opaquely (assert it was invoked with X), not verified "
                f"against real logic."
            )
    return out


def classify_member(conn, mid: int, name: str, scopes: list[dict],
                     accesses: list[dict] | None = None, calls: list[dict] | None = None) -> dict:
    """`scopes` is graph.transaction_scopes(conn)'s full result, computed
    once by the caller -- that function also records `sme_question` gaps as
    a side effect, so calling it fresh per member here would re-insert the
    same gap once per member instead of once per run.

    `accesses`/`calls` are optional pre-fetched rows for this member (see
    run_all's batched fetch); when omitted, fetched here for a single-member
    caller."""
    if accesses is None:
        accesses = _data_accesses_by_member(conn, [mid]).get(mid, [])
    if calls is None:
        calls = _outbound_calls_by_member(conn, [mid]).get(mid, [])
    gaps = _gaps(calls, name)
    member_scopes = [s for s in scopes if s["module"] == name]
    multi_entity_scope = next((s for s in member_scopes if len(s["entities"]) > 1), None)

    if gaps:
        classification = UNTESTABLE_GAP
    elif multi_entity_scope:
        classification = INTEGRATION_ONLY
    elif accesses or calls:
        classification = NEEDS_MOCK
    else:
        classification = PURE

    return {
        "member": name,
        "classification": classification,
        "mocks": {
            "entities": sorted({a["entity_name"] for a in accesses}),
            "callees": sorted({c["callee_name"] for c in calls if not c["dynamic"]}),
        },
        "seams": _seam_suggestions(accesses, calls, name) if classification == NEEDS_MOCK else [],
        "integration_scope": (
            {"entities": multi_entity_scope["entities"], "commit_line": multi_entity_scope["commit_line"]}
            if multi_entity_scope else None
        ),
        "gaps": gaps,
    }


def run_all(conn) -> dict:
    """Returns `{"results": [...], "ambiguous": [...]}` -- `ambiguous` names
    a batchable member skipped because its bare name collided with another
    library's member of the same name (see `db.group_members_by_name`),
    rather than dropping it with no diagnostic."""
    from .batch import select_batch_members
    from .db import group_members_by_name

    names = list(dict.fromkeys(select_batch_members(conn)))
    if not names:
        return {"results": [], "ambiguous": []}
    placeholders = ",".join("?" * len(names))
    members = conn.execute(
        f"SELECT id, name, library FROM member WHERE name IN ({placeholders})", names
    ).fetchall()
    # A bare name can be ambiguous across libraries (see brief.rules_register's
    # identical check) -- skip rather than guess which library's facts apply,
    # and surface it as `ambiguous` rather than a silent drop.
    unambiguous, ambiguous = group_members_by_name(members)
    # transaction_scopes() adds an `sme_question` gap as a side effect for
    # every write-without-commit member it finds; graph.run_all() purges
    # that gap_kind before its own call so re-running derive doesn't
    # accumulate duplicates (see DERIVED_GAP_KINDS). Do the same here --
    # otherwise a `test-advisory` run after `derive` doubles those gaps.
    conn.execute("DELETE FROM gap WHERE gap_kind='sme_question'")
    scopes = transaction_scopes(conn)
    conn.commit()

    member_ids = [r["id"] for r in unambiguous.values()]
    accesses_by_id = _data_accesses_by_member(conn, member_ids)
    calls_by_id = _outbound_calls_by_member(conn, member_ids)

    out = []
    for name in names:
        row = unambiguous.get(name)
        if row is None:
            continue
        out.append(classify_member(
            conn, row["id"], name, scopes,
            accesses=accesses_by_id.get(row["id"], []),
            calls=calls_by_id.get(row["id"], []),
        ))
    return {"results": out, "ambiguous": ambiguous}


def testability_report(conn) -> str:
    """A deterministic, reviewable report -- same "regenerate, don't
    hand-edit" contract as rules_register/test_plan_register."""
    run = run_all(conn)
    results, ambiguous = run["results"], run["ambiguous"]
    out = ["# Testability advisory", "", (
        "Classification of every batchable member for test generation, "
        "derived from data_access/call_edge/transaction_scopes facts. "
        "Regenerate with `mfdoc test-advisory`; do not hand-edit. Seam "
        "suggestions are advisory prose only -- nothing here changes source."
    ), ""]
    if ambiguous:
        out.append(
            "**Skipped as ambiguous** (name collides across libraries -- "
            "re-run `mfdoc brief --module NAME` per library to disambiguate): "
            + ", ".join(f"`{n}`" for n in ambiguous)
        )
        out.append("")
    by_class: dict[str, list] = {}
    for r in results:
        by_class.setdefault(r["classification"], []).append(r)

    labels = {
        PURE: "Pure — unit-testable directly, no mocks needed",
        NEEDS_MOCK: "Needs mocks — unit-testable with named seams",
        INTEGRATION_ONLY: "Integration-only — spans multiple entities in one transaction",
        UNTESTABLE_GAP: "Blocked — dynamic/unresolved call, confirm before testing",
    }
    for key in (PURE, NEEDS_MOCK, INTEGRATION_ONLY, UNTESTABLE_GAP):
        rows = by_class.get(key, [])
        if not rows:
            continue
        out.append(f"## {labels[key]}")
        out.append("")
        for r in rows:
            out.append(f"### `{r['member']}`")
            if r["mocks"]["entities"] or r["mocks"]["callees"]:
                out.append(f"- entities to mock: {', '.join(r['mocks']['entities']) or '-'}")
                out.append(f"- callees to mock: {', '.join(r['mocks']['callees']) or '-'}")
            if r["integration_scope"]:
                out.append(
                    f"- transaction scope entities: "
                    f"{', '.join(r['integration_scope']['entities'])} "
                    f"(commit at line {r['integration_scope']['commit_line']})"
                )
            for s in r["seams"]:
                out.append(f"- seam: {s}")
            for g in r["gaps"]:
                out.append(f"- gap: {g}")
            out.append("")
    return "\n".join(out) + "\n"
