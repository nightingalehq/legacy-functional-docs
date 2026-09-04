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

import json

from .redact import NULL_REDACTOR, Redactor


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


def _branch_data_access(conn, mid: int, name: str, start_line: int, end_line: int) -> str | None:
    """Compact citation list of every data_access row strictly between
    `start_line` and `end_line` (inclusive of end_line, exclusive of
    start_line -- the construct's own line) -- attached to an IF/ELSE
    rule's bullet so a GET/UPDATE/DELETE inside that specific branch is
    impossible to miss when writing the branch up, rather than only
    appearing, uncorrelated, in the brief's separate "Data access"
    section. None when nothing falls in range."""
    rows = conn.execute(
        "SELECT * FROM data_access WHERE member_id=? AND line_no > ? AND line_no <= ? ORDER BY line_no",
        (mid, start_line, end_line),
    ).fetchall()
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
    if line is None:
        return f"[[{name}]]"
    if end and end != line:
        return f"[[{name}:{line}-{end}]]"
    return f"[[{name}:{line}]]"


def module_brief(conn, member_name: str, excerpt_rules: bool = True,
                  redact: Redactor = NULL_REDACTOR, lexicon: dict[str, str] | None = None,
                  rule_range: tuple[int, int] | None = None,
                  chunk_info: tuple[int, int] | None = None) -> str:
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
    chunked module-doc path for what calls this with both set."""
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
    add(f"- line_count: {conn.execute('SELECT COUNT(*) FROM source_line WHERE member_id=?', (mid,)).fetchone()[0]}")
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
            "it; the other chunks cover them independently."
        )
    add("")
    vocab_insert_at = len(out)

    # --- header comments often carry the only surviving prose description
    # Only the leading contiguous comment block, and only lines with real content.
    # Rule-of-thumb separators (`*`, `****`) and lone `*` spacers add noise that
    # crowds out the two or three lines that actually say what the module is for.
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
    if hdr:
        add("## Header comments (unverified author prose — treat as claims, not facts)")
        for r in hdr:
            add(f"- {_cite(name, r['line_no'])} `{redact(r['text'][:160])}`")
        add("")

    # --- interfaces
    params = conn.execute(
        "SELECT * FROM variable WHERE member_id=? AND scope IN ('parameter','entry') ORDER BY line_no",
        (mid,),
    ).fetchall()
    if params:
        add("## Interface (parameters)")
        for r in params:
            spec = f" ({r['format'] or ''}{r['length'] or ''})" if (r["format"] or r["length"]) else ""
            add(f"- {_cite(name, r['line_no'])} level {r['level'] or '-'} `{r['name']}`{spec}")
        add("")

    views = conn.execute(
        "SELECT * FROM variable WHERE member_id=? AND scope='view' ORDER BY line_no", (mid,)
    ).fetchall()
    if views:
        add("## Data views declared")
        for r in views:
            add(f"- {_cite(name, r['line_no'])} view `{r['name']}` over `{r['view_of']}`")
        add("")

    # --- internal routines (Natural DEFINE SUBROUTINE / Mantis ENTRY) --
    # the structural grouping the "Candidate business rules" section below
    # tags each rule with, so the narrator can (and should) write one
    # subsection per routine rather than a flat list -- see writing-rules.md.
    routines = fetch_routines(conn, mid)
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
        for r in routines:
            span = _cite(name, r["start_line"], r["end_line"]) if r["end_line"] else \
                f"{_cite(name, r['start_line'])} **[no matching end found -- extent unresolved]**"
            add(f"- `{r['name']}` ({r['kind']}) {span}")
        add("")

    # --- data access
    acc = conn.execute(
        "SELECT * FROM data_access WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()
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
            add(f"- {_cite(name, r['line_no'])} `{r['verb']}` ({r['crud']}) on "
                f"`{r['entity_name'] or 'UNKNOWN'}`{desc}{key}{flag}{source}")
        add("")

    # --- transaction markers
    tx = conn.execute(
        "SELECT * FROM transaction_marker WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()
    if tx:
        add("## Transaction boundaries")
        for r in tx:
            add(f"- {_cite(name, r['line_no'])} `{r['marker']}`"
                + (f" restart data: `{redact(r['et_data'])}`" if r["et_data"] else ""))
        add("")

    # --- calls
    calls = conn.execute(
        "SELECT * FROM call_edge WHERE caller_id=? ORDER BY line_no", (mid,)
    ).fetchall()
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

    inbound = conn.execute(
        """
        SELECT c.name AS caller, ce.call_kind, ce.line_no
          FROM call_edge ce JOIN member c ON c.id = ce.caller_id
         WHERE UPPER(ce.callee_name)=UPPER(?) ORDER BY c.name, ce.line_no
        """,
        (name,),
    ).fetchall()
    if inbound:
        add("## Inbound callers")
        for r in inbound:
            add(f"- {_cite(r['caller'], r['line_no'])} `{r['call_kind']}` from `{r['caller']}`")
        add("")

    # --- interactions
    inter = conn.execute(
        "SELECT * FROM interaction WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()
    if inter:
        add("## User interaction points")
        for r in inter:
            add(f"- {_cite(name, r['line_no'])} `{r['kind']}`"
                + (f" target `{r['target']}`" if r["target"] else "")
                + (f" `{redact((r['fields'] or '')[:90])}`" if r["fields"] else ""))
        add("")

    msgs = conn.execute(
        "SELECT * FROM message_ref WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()
    if msgs:
        add("## Messages and error handling")
        for r in msgs:
            add(f"- {_cite(name, r['line_no'])} `{r['kind']}`"
                + (f" number `{r['number']}`" if r["number"] else "")
                + (f" text: \"{redact((r['text'] or '')[:120])}\"" if r["text"] else ""))
        add("")

    # --- rule candidates, with exact conditions
    rules = conn.execute(
        "SELECT * FROM rule_candidate WHERE member_id=? ORDER BY line_no", (mid,)
    ).fetchall()
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
        for n, r in enumerate(rules, start=1):
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
                access_summary = _branch_data_access(conn, mid, name, r["line_no"], body_end)
                if access_summary:
                    bits.append(f"data access on the true branch: {access_summary}")
            elif r["construct"] == "ELSE" and r["pair_line_no"]:
                bits.append(f"pairs with the IF at {_cite(name, r['pair_line_no'])}")
                if r["end_line"]:
                    bits.append(f"else-branch extent {_cite(name, r['line_no'], r['end_line'])}")
                    access_summary = _branch_data_access(conn, mid, name, r["line_no"], r["end_line"])
                    if access_summary:
                        bits.append(f"data access on this branch: {access_summary}")
            add("- " + " — ".join(bits))
        add("")

    # --- rules inherited from included copycode, cited against the copycode
    # itself. Without this, a rule defined only in a copycode member is
    # attributed solely to that member's own brief, and never appears when
    # briefing the module that actually includes and runs it -- a module doc
    # can look complete and still miss a validation rule it depends on.
    for cc_id, cc_name, cc_rules in _copycode_rule_candidates(conn, mid):
        add(f"## Business rules from included copycode `{cc_name}`")
        for n, r in enumerate(cc_rules, start=1):
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

    gaps = conn.execute(
        "SELECT * FROM gap WHERE member_id=? ORDER BY severity DESC, line_no", (mid,)
    ).fetchall()
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

    return "\n".join(out) + "\n"


def entity_brief(conn, entity_name: str, redact: Redactor = NULL_REDACTOR,
                  lexicon: dict[str, str] | None = None) -> str:
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
    for member_name in names:
        matches = rows_by_name.get(member_name, [])
        if len(matches) != 1:
            libs = ", ".join(sorted({m["library"] or "unknown" for m in matches})) or "none found"
            out.append(
                f"| — | `{member_name}` | — | — | ambiguous | name is ambiguous across "
                f"libraries ({libs}) -- re-run `mfdoc brief --module {member_name}` "
                "per library | — |"
            )
            continue
        modules_included += 1
        rules = rules_by_member_id.get(matches[0]["id"], [])
        for n, r in enumerate(rules, start=1):
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
    out.append(f"Total: {total} rule candidate(s) across {modules_included} batchable module(s).")
    out.append("")
    return "\n".join(out) + "\n"


def json_index(conn) -> str:
    """Machine-readable dump for downstream tooling."""
    payload = {}
    for table in ("member", "entity", "entity_field", "entity_link", "data_access",
                  "call_edge", "transaction_marker", "interaction", "rule_candidate",
                  "message_ref", "job_step", "job_dd", "cics_resource", "gap", "metric"):
        payload[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    return json.dumps(payload, indent=2)
