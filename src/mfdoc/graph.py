"""Stage 2 — derivation.

Nothing here reads source. It only joins facts already extracted, which keeps the
distinction clear: if a claim in the final documentation traces to this stage it
is still `verified`, because every derived fact is a deterministic consequence of
cited rows. Anything requiring judgement is deliberately pushed out to the
narrative stage and marked `inferred`.
"""

from __future__ import annotations

from collections import defaultdict

from .db import add_gap, set_metric

# Members that are entry points by construction rather than by being called.
ENTRY_KINDS = {"EXEC_PGM"}

# gap_kinds this module's own functions add. Unlike extraction-time gaps
# (unparsed_line, dynamic_target, ...), which cli.py's incremental ingest
# already keeps in sync via purge_member_facts, nothing purges these before
# they're regenerated -- run_all() re-derives everything from the current
# fact store every time it's called, so without this, running `mfdoc derive`
# twice against an unchanged index (now a normal thing to do, since
# incremental ingest can legitimately no-op and still be followed by a
# derive pass) would silently double every one of these gap rows.
DERIVED_GAP_KINDS = (
    "ambiguous_adabas_file", "no_ddl_for_entity", "unresolved_call",
    "orphan_module", "sme_question", "unused_field",
)


def reconcile_adabas_files(conn) -> int:
    """Merge `FILE-nnn` placeholders into the named file from the FDT.

    A DDM listing names its physical file only by DBID and FNR, while an FDT
    report names it properly. Left unreconciled the index shows two data stores
    where there is one, which produces a data model diagram with phantom entities
    — the kind of error that destroys reviewer confidence in the whole document
    set on first read.
    """
    merged = 0
    placeholders = conn.execute(
        "SELECT id, name, dbid, fnr FROM entity WHERE kind='adabas_file' AND name LIKE 'FILE-%'"
    ).fetchall()
    for p in placeholders:
        # Fetch every candidate rather than LIMIT 1: when the placeholder's
        # DBID is unknown, FNR alone may match more than one named file
        # across distinct databases. Merging on the first hit would fold two
        # different files' facts together; only merge when the match is
        # unambiguous, and record a gap otherwise.
        candidates = conn.execute(
            """
            SELECT id FROM entity
             WHERE kind='adabas_file' AND id<>? AND name NOT LIKE 'FILE-%'
               AND IFNULL(fnr,'')=IFNULL(?,'') AND (IFNULL(dbid,'')=IFNULL(?,'') OR dbid IS NULL OR ? IS NULL)
            """,
            (p["id"], p["fnr"], p["dbid"], p["dbid"]),
        ).fetchall()
        if len(candidates) != 1:
            if len(candidates) > 1:
                add_gap(
                    conn, "ambiguous_adabas_file",
                    f"FILE-{p['fnr']} matches {len(candidates)} named Adabas files with "
                    f"differing DBIDs; left unreconciled to avoid merging distinct databases. "
                    f"Confirm the correct DBID for FNR {p['fnr']}.",
                    severity="medium",
                )
            continue
        target = candidates[0]
        conn.execute("UPDATE entity_field SET entity_id=? WHERE entity_id=?", (target["id"], p["id"]))
        conn.execute("UPDATE entity_link SET from_entity=? WHERE from_entity=?", (target["id"], p["id"]))
        conn.execute("UPDATE entity_link SET to_entity=? WHERE to_entity=?", (target["id"], p["id"]))
        conn.execute("UPDATE data_access SET entity_id=? WHERE entity_id=?", (target["id"], p["id"]))
        conn.execute("DELETE FROM entity_link WHERE from_entity=to_entity", ())
        conn.execute("DELETE FROM entity WHERE id=?", (p["id"],))
        merged += 1
    return merged


def resolve(conn) -> dict:
    """Resolve call edges and data-access entities to their definitions."""
    merged = reconcile_adabas_files(conn)
    conn.execute(
        """
        UPDATE call_edge
           SET callee_id = (SELECT m.id FROM member m
                             WHERE UPPER(m.name) = UPPER(call_edge.callee_name)
                             LIMIT 1),
               resolved  = CASE
                             -- An internal subroutine was already resolved to its
                             -- own member at extraction time; it has no member row
                             -- of its own, so a name lookup would undo that.
                             WHEN call_kind = 'PERFORM_INTERNAL' THEN 1
                             WHEN EXISTS (SELECT 1 FROM member m
                                           WHERE UPPER(m.name) = UPPER(call_edge.callee_name))
                             THEN 1 ELSE 0 END
        """
    )
    conn.execute(
        """
        UPDATE data_access
           SET entity_id = COALESCE(entity_id,
                 (SELECT e.id FROM entity e WHERE UPPER(e.name) = UPPER(data_access.entity_name) LIMIT 1))
         WHERE entity_name IS NOT NULL
        """
    )

    # A DDM referenced in code but never defined by a parsed DDM/FDT listing is a
    # real documentation risk: field semantics are unknown.
    rows = conn.execute(
        """
        SELECT DISTINCT da.entity_name
          FROM data_access da
          LEFT JOIN entity e ON UPPER(e.name) = UPPER(da.entity_name)
         WHERE da.entity_name IS NOT NULL
           AND (e.id IS NULL OR e.defined_in IS NULL)
           AND da.entity_name NOT LIKE 'WORKFILE-%'
        """
    ).fetchall()
    for r in rows:
        add_gap(conn, "no_ddl_for_entity",
                f"Data store '{r['entity_name']}' is accessed by application code but no DDM, "
                f"FDT, Supra directory entry or DDL was supplied for it. Field-level meaning "
                f"cannot be documented until the definition is provided.",
                severity="high")

    unresolved = conn.execute(
        """
        SELECT ce.callee_name, COUNT(*) n, MIN(ce.line_no) ln, ce.caller_id, ce.call_kind
          FROM call_edge ce
         WHERE ce.resolved = 0 AND ce.dynamic = 0
           AND ce.call_kind <> 'PERFORM_INTERNAL'
         GROUP BY UPPER(ce.callee_name), ce.call_kind
        """
    ).fetchall()
    for r in unresolved:
        add_gap(conn, "unresolved_call",
                f"{r['call_kind']} target '{r['callee_name']}' ({r['n']} call site(s)) has no "
                f"source member in the ingested set. Either the source is missing or the target "
                f"lives in another library.",
                member_id=r["caller_id"], line_no=r["ln"], severity="high")

    return {"unresolved_calls": len(unresolved), "undefined_entities": len(rows),
            "adabas_entities_merged": merged}


def crud_matrix(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT m.name AS module, m.dialect, da.entity_name AS entity,
               GROUP_CONCAT(DISTINCT da.crud) AS crud,
               COUNT(*) AS hits,
               MIN(da.line_no) AS first_line,
               GROUP_CONCAT(DISTINCT da.verb) AS verbs
          FROM data_access da JOIN member m ON m.id = da.member_id
         WHERE da.entity_name IS NOT NULL
         GROUP BY m.name, da.entity_name
         ORDER BY m.name, da.entity_name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def referenced_entities(conn, member_id: int) -> list[dict]:
    """Every entity (Adabas file, Supra dataset, Mantis screen/map, ...)
    this member is known to touch: read/written via data_access, declared
    as a view over one, or shown/converged as a screen -- one row per
    distinct entity. The set unused_entity_fields_for_member checks each
    entity's own fields against.

    The `variable.view_of` join is deliberately restricted to
    `scope IN ('view', 'screen')` -- the only two scopes any dialect
    extractor currently populates `view_of` for (a Natural/Mantis `VIEW`,
    and Mantis's `SCREEN name("physical")` local-alias binding; see
    natural.py/mantis.py) -- rather than accepting any scope's `view_of`
    unconditionally. Both of those really do mean "this member touches
    that entity"; a future scope that happened to reuse the `view_of`
    column for something else must not silently start counting here too.

    Deliberately does NOT join on `call_edge.call_kind = 'INCLUDE'`: every
    Mantis CONVERSE/SHOW/PROMPT/INPUT handler that creates one of those
    also creates an `interaction` row with the same target in the same
    statement (see mantis.py), so that case is already covered by the
    interaction join above without it. Natural's INCLUDE edges, on the
    other hand, are also used for `DEFINE DATA ... USING` a copycode/LDA/
    GDA -- names that have no reason to be entities at all, but would be
    silently (and wrongly) treated as referenced entities if one happened
    to share a name with a real one. Joining on INCLUDE indiscriminately
    would add that risk for no case it actually needs to cover."""
    rows = conn.execute(
        """
        SELECT DISTINCT e.id, e.name, e.kind FROM entity e
         WHERE e.id IN (
             SELECT entity_id FROM data_access WHERE member_id=? AND entity_id IS NOT NULL
             UNION
             SELECT e2.id FROM variable v JOIN entity e2 ON UPPER(e2.name) = UPPER(v.view_of)
              WHERE v.member_id = ? AND v.view_of IS NOT NULL AND v.scope IN ('view', 'screen')
             UNION
             SELECT e3.id FROM interaction i JOIN entity e3 ON UPPER(e3.name) = UPPER(i.target)
              WHERE i.member_id = ? AND i.target IS NOT NULL
         )
        """,
        (member_id, member_id, member_id),
    ).fetchall()
    return [dict(r) for r in rows]


def unused_entity_fields_for_member(conn, member_id: int) -> list[dict]:
    r"""For every entity `member_id` is known to reference, that entity's
    own data fields (a screen's HEADING rows are literal text, not a
    referenceable field, and are excluded by format) which never appear --
    as a whole word, case-insensitive -- anywhere in this member's own
    source. A screen/table's field inventory is complete (it comes from
    its own definition, not from this member's usage of it), so a field
    that never turns up is evidence the member genuinely never touches it,
    not a scanner miss -- worth a question to an SME (is it dead, or read
    by different code than what was supplied?) rather than silence.

    Deterministic: a whole-word text scan, nothing inferred about *why* a
    field is unused. One dict per unused field: entity_id, entity_name,
    entity_kind, field_name, field_format.

    Scans only non-comment lines (`source_line.is_comment=0`) -- a field
    name mentioned only in a comment (a TODO, a commented-out statement)
    is not actually referenced by the code, and counting it as used would
    hide a real finding. Matches are bounded by lookarounds against this
    codebase's own identifier character set (`[A-Z0-9#@$&-_.]`, the union
    of natural.py's and mantis.py's own identifier patterns) rather than
    `\b`: Python's `\b` is defined relative to `\w` (letters/digits/`_`
    only), so a field name starting or ending with `#`/`$`/`&`/`-` --
    all valid leading/trailing identifier characters in both dialects --
    would never match at all, since `\b` cannot fire between two
    non-word characters (e.g. a space then `#`)."""
    import re

    _IDENT_CHAR = r"[A-Z0-9#@$&\-_.]"

    text = "\n".join(
        r["text"] for r in conn.execute(
            "SELECT text FROM source_line WHERE member_id=? AND is_comment=0", (member_id,)
        ).fetchall()
    )
    out: list[dict] = []
    for ent in referenced_entities(conn, member_id):
        fields = conn.execute(
            "SELECT name, format FROM entity_field WHERE entity_id=? "
            "AND UPPER(IFNULL(format,'')) != 'HEADING' ORDER BY id",
            (ent["id"],),
        ).fetchall()
        for f in fields:
            pattern = rf"(?<!{_IDENT_CHAR}){re.escape(f['name'])}(?!{_IDENT_CHAR})"
            if not re.search(pattern, text, re.I):
                out.append({
                    "entity_id": ent["id"], "entity_name": ent["name"], "entity_kind": ent["kind"],
                    "field_name": f["name"], "field_format": f["format"],
                })
    return out


def unused_entity_fields(conn) -> list[dict]:
    """unused_entity_fields_for_member, across every Natural/Mantis member,
    each result also recording which member it's unused *in* -- and adds
    one `unused_field` gap per finding, member-scoped, so it shows up in
    that module's own gap register section the same way any other
    per-member gap does. Called from run_all(); see DERIVED_GAP_KINDS for
    why prior findings are cleared before this reruns."""
    out: list[dict] = []
    members = conn.execute(
        "SELECT id, name FROM member WHERE dialect IN ('natural','mantis')"
    ).fetchall()
    for m in members:
        for f in unused_entity_fields_for_member(conn, m["id"]):
            f = {**f, "member_id": m["id"], "member_name": m["name"]}
            out.append(f)
            add_gap(
                conn, "unused_field",
                f"Field {f['field_name']} of {f['entity_kind']} {f['entity_name']} is never "
                f"referenced anywhere in {m['name']}'s own source. Confirm whether it is "
                f"genuinely unused (a candidate for removal, or evidence the field's real "
                f"consumer wasn't supplied) or the scanner missed an indirect reference.",
                member_id=m["id"], severity="low",
            )
    return out


def orphans(conn) -> list[dict]:
    """Modules with no inbound reference from anywhere, including JCL and CICS."""
    rows = conn.execute(
        """
        SELECT m.id, m.name, m.dialect, m.object_type
          FROM member m
         WHERE m.dialect IN ('natural','mantis')
           AND m.object_type NOT IN ('ddm','lda','gda','pda','map','text')
           AND NOT EXISTS (SELECT 1 FROM call_edge ce
                            WHERE ce.callee_id = m.id
                               OR UPPER(ce.callee_name) = UPPER(m.name))
        """
    ).fetchall()
    for r in rows:
        add_gap(conn, "orphan_module",
                f"No JCL step, CICS transaction or program call refers to {r['name']}. It may be "
                f"dead code, invoked dynamically, or started from a scheduler or menu definition "
                f"that was not supplied. Confirm before documenting it as live functionality.",
                member_id=r["id"], severity="medium")
    return [dict(r) for r in rows]


def transaction_scopes(conn) -> list[dict]:
    """Group writes into units of work delimited by commit markers.

    Only reports scopes for structured-mode members: in reporting mode the
    position of a commit relative to loop boundaries is not recoverable from a
    line scan, and a wrong unit-of-work boundary in documentation is worse than
    an admitted gap.
    """
    out = []
    members = conn.execute(
        "SELECT id, name, mode FROM member WHERE dialect IN ('natural','mantis')"
    ).fetchall()
    for m in members:
        marks = conn.execute(
            "SELECT line_no, marker FROM transaction_marker WHERE member_id=? ORDER BY line_no",
            (m["id"],),
        ).fetchall()
        writes = conn.execute(
            """
            SELECT line_no, crud, entity_name, verb FROM data_access
             WHERE member_id=? AND crud IN ('C','U','D') ORDER BY line_no
            """,
            (m["id"],),
        ).fetchall()
        if not writes:
            continue
        if not marks:
            add_gap(conn, "sme_question",
                    f"{m['name']} performs {len(writes)} write operation(s) but contains no "
                    f"explicit END TRANSACTION / COMMIT. Confirm whether commit is handled by a "
                    f"caller, by the TP monitor at task end, or not at all.",
                    member_id=m["id"], severity="high")
            continue
        if m["mode"] == "reporting":
            continue
        boundaries = [0] + [mk["line_no"] for mk in marks]
        for i, start in enumerate(boundaries[:-1]):
            end = boundaries[i + 1]
            scope_writes = [w for w in writes if start < w["line_no"] <= end]
            if scope_writes:
                out.append({
                    "module": m["name"],
                    "commit_line": end,
                    "entities": sorted({w["entity_name"] for w in scope_writes if w["entity_name"]}),
                    "operations": [f"{w['verb']} {w['entity_name']} @{w['line_no']}" for w in scope_writes],
                })
    return out


def call_closure(conn, root_name: str, max_depth: int = 12) -> dict:
    """Transitive callees from a root module, with depth, for process-flow docs."""
    seen: dict[str, int] = {}
    frontier = [(root_name.upper(), 0)]
    while frontier:
        name, d = frontier.pop()
        if name in seen and seen[name] <= d:
            continue
        seen[name] = d
        if d >= max_depth:
            continue
        rows = conn.execute(
            """
            SELECT DISTINCT ce.callee_name, ce.call_kind, ce.dynamic
              FROM call_edge ce JOIN member m ON m.id = ce.caller_id
             WHERE UPPER(m.name) = ?
            """,
            (name,),
        ).fetchall()
        for r in rows:
            frontier.append((r["callee_name"].upper(), d + 1))
    return seen


def coverage(conn) -> dict:
    def scalar(sql, *args):
        return conn.execute(sql, args).fetchone()[0]

    total_members = scalar("SELECT COUNT(*) FROM member")
    code_members = scalar("SELECT COUNT(*) FROM member WHERE dialect IN ('natural','mantis')")
    lines = scalar("SELECT COUNT(*) FROM source_line")
    unparsed = scalar("SELECT COUNT(*) FROM gap WHERE gap_kind='unparsed_line'")
    entities = scalar("SELECT COUNT(*) FROM entity")
    defined_entities = scalar("SELECT COUNT(*) FROM entity WHERE defined_in IS NOT NULL")
    fields = scalar("SELECT COUNT(*) FROM entity_field")
    accesses = scalar("SELECT COUNT(*) FROM data_access")
    rules = scalar("SELECT COUNT(*) FROM rule_candidate")
    # INCLUDE edges (copycode, maps, data areas, screens) are counted separately.
    # Folding them into the call-resolution figure makes the gate meaningless: a
    # codebase where every copycode was supplied but half the subprograms are
    # missing would score the same as the reverse, and only the second case
    # actually blocks documenting control flow.
    calls = scalar(
        "SELECT COUNT(*) FROM call_edge WHERE call_kind NOT IN ('INCLUDE','PERFORM_INTERNAL')")
    resolved = scalar(
        "SELECT COUNT(*) FROM call_edge "
        "WHERE call_kind NOT IN ('INCLUDE','PERFORM_INTERNAL') AND resolved=1")
    dynamic = scalar("SELECT COUNT(*) FROM call_edge WHERE dynamic=1")
    includes = scalar("SELECT COUNT(*) FROM call_edge WHERE call_kind = 'INCLUDE'")
    includes_resolved = scalar(
        "SELECT COUNT(*) FROM call_edge WHERE call_kind = 'INCLUDE' AND resolved=1")

    cov = {
        "members": total_members,
        "code_members": code_members,
        "source_lines": lines,
        "unparsed_lines": unparsed,
        "line_recognition_rate": round(1 - (unparsed / lines), 4) if lines else 0,
        "entities": entities,
        "entities_with_definition": defined_entities,
        "entity_definition_rate": round(defined_entities / entities, 4) if entities else 0,
        "entity_fields": fields,
        "data_accesses": accesses,
        "rule_candidates": rules,
        "invocation_edges": calls,
        "invocations_resolved": resolved,
        "call_resolution_rate": round(resolved / calls, 4) if calls else 0,
        "dynamic_call_edges": dynamic,
        "include_edges": includes,
        "includes_resolved": includes_resolved,
        "include_resolution_rate": round(includes_resolved / includes, 4) if includes else 0,
        "gaps_high": scalar("SELECT COUNT(*) FROM gap WHERE severity='high'"),
        "gaps_total": scalar("SELECT COUNT(*) FROM gap"),
    }
    # Sampling-derived, not computed from facts -- only present once
    # `mfdoc sample-citations --judge human` has recorded at least one
    # verdict (that command persists it via set_metric under this exact
    # name). Omitted rather than defaulted to 0/None when absent, so every
    # existing coverage()-snapshot test stays byte-for-byte unaffected
    # until a project actually runs the sampling command.
    accuracy_row = conn.execute(
        "SELECT value FROM metric WHERE scope='global' AND name='citation_accuracy_rate'"
    ).fetchone()
    if accuracy_row is not None:
        cov["citation_accuracy_rate"] = float(accuracy_row["value"])
    for k, v in cov.items():
        set_metric(conn, "global", f"coverage.{k}", v)
    return cov


def run_all(conn) -> dict:
    # Re-derive from a clean slate -- see DERIVED_GAP_KINDS' comment.
    conn.execute(
        f"DELETE FROM gap WHERE gap_kind IN ({','.join('?' * len(DERIVED_GAP_KINDS))})",
        DERIVED_GAP_KINDS,
    )
    res = resolve(conn)
    orph = orphans(conn)
    scopes = transaction_scopes(conn)
    unused_fields = unused_entity_fields(conn)
    cov = coverage(conn)
    set_metric(conn, "global", "derived.orphan_modules", [o["name"] for o in orph])
    set_metric(conn, "global", "derived.transaction_scopes", len(scopes))
    set_metric(conn, "global", "derived.unused_entity_fields", len(unused_fields))
    conn.commit()
    return {
        **res, "orphans": len(orph), "transaction_scopes": len(scopes),
        "unused_entity_fields": len(unused_fields), "coverage": cov,
    }
