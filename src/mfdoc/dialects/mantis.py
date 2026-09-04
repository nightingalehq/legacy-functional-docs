"""Mantis (Cincom) extractor.

Honest caveat, stated in code because it matters operationally: Mantis is far
less publicly documented than Natural, its source normally lives inside the
Mantis library in the DBMS rather than on a filesystem, and export layouts and
even comment conventions vary between sites and releases. The keyword tables
below are a defensible starting point, not a validated grammar.

Consequently this module is written to be *calibrated*, not rewritten. Every
keyword set is a module-level table that project config can override, and the
scanner records a `gap` for every statement it does not recognise so the first
run on a new codebase produces a measurable, reviewable coverage figure. Treat a
first-run recognition rate below roughly 85% as a signal to calibrate the tables
against real source before trusting any narrative built on top of it.

One site convention calibrated in here: some Mantis exports render block
nesting as a literal run of leading dots (`.IF ...`, `..GET ...`, `...END`)
instead of whitespace indentation, with a bare `|` right after the dots (or at
column 0) marking a remark or commented-out statement (`.|`, `..|RFC-START`,
`|C00306 START`). Both are stripped before keyword matching -- see
`_split_depth_marker` -- and the existing IF/WHILE/DO block-depth tracking
below is unaffected, since it derives depth from matched constructs rather
than from the dot count itself.

The same export style also wraps a long condition or string expression across
physical lines by marking every continuation line with a leading `'` (after
its own depth-dots), e.g. an unclosed `IF(...` followed by `'OR ...)`. Unlike
Natural's implicit continuation (inferred from a trailing/leading connective,
see `natural.CONTINUATION_TAIL`/`CONTINUATION_LEAD`), this is an explicit,
unambiguous marker, so `extract` folds every run of `'`-marked lines onto the
statement they continue -- joined with a single inserted space -- before any
keyword pattern is matched against it. Each continuation line is still
visited afterwards in its own right and correctly fails to stand alone as a
statement (the same accepted double-visit `natural.py`'s own continuation
fold relies on), so it still raises its own
low-severity `unparsed_line` gap -- the fold only fixes the *content*
recorded for the statement it belongs to.
"""

from __future__ import annotations

import re

from ..db import add_gap, insert, resolve_entity, set_metric, upsert_entity
from .natural import mask_literals, orig

# ------------------------------------------------------- calibratable tables

COMMENT_PREFIXES = ("*", "%", "!", "/*", "//")

DECL_TYPES = (
    "TEXT", "SMALLTEXT", "BIGTEXT", "NUMERIC", "BIGNUMERIC", "SMALLNUMERIC",
    "ARRAY", "KANJI", "LEVEL", "PICTURE", "BIG", "SMALL",
)

# Supra / TOTAL DML function codes, mapped to CRUD intent.
SUPRA_DML = {
    "READM": "R", "READD": "R", "READV": "R", "READR": "R", "RDNXT": "R",
    "READX": "R", "RDNXTX": "R",
    "ADD-M": "C", "ADDM": "C", "ADD-D": "C", "ADDD": "C", "ADDVA": "C", "ADDVB": "C",
    "WRITM": "U", "WRITD": "U", "WRITV": "U", "WRITX": "U",
    "DEL-M": "D", "DELM": "D", "DEL-D": "D", "DELD": "D", "DELVD": "D",
}

TXN_MARKERS = {
    "COMMIT": "COMMIT", "ROLLBACK": "ROLLBACK", "ENDTR": "ENDTR", "ENDTRAN": "ENDTR",
    "CTRL-BEGIN": "CTRL-BEGIN", "CTRL-END": "CTRL-END", "SINON": "SIGNON", "SINOF": "SIGNOFF",
}

# ----------------------------------------------------------------- patterns

RE_PROGRAM = re.compile(r"^\s*PROGRAM\s+\"(?P<name>[^\"]+)\"", re.I)
RE_ENTRY = re.compile(r"^\s*ENTRY\s+(?P<name>[A-Z0-9_#$\-]+)\s*(?:\((?P<params>[^)]*)\))?", re.I)
RE_EXIT = re.compile(r"^\s*EXIT\b", re.I)
RE_EXTERNAL = re.compile(r"^\s*EXTERNAL\s+(?P<args>.+)$", re.I)
RE_DECL = re.compile(
    r"^\s*(?P<type>" + "|".join(DECL_TYPES) + r")\s+(?P<name>[A-Z0-9_#$\-]+)"
    r"\s*(?:\((?P<spec>[^)]*)\))?(?P<rest>.*)$", re.I)
RE_VIEW = re.compile(
    r"^\s*VIEW\s+(?P<name>[A-Z0-9_#$\-]+)\s*"
    r"(?:OF\s+(?P<of>[A-Z0-9_#$\-]+)|\(\s*\"(?P<pof>[^\"]+)\")?", re.I)
RE_OBTAIN = re.compile(r"^\s*OBTAIN\s+(?P<rest>.+)$", re.I)
RE_GET = re.compile(r"^\s*GET\s+(?P<rest>.+)$", re.I)
RE_INSERT = re.compile(r"^\s*(?:INSERT|ADD)\s+(?P<rest>.+)$", re.I)
RE_UPDATE = re.compile(r"^\s*(?:UPDATE|REPLACE)\s+(?P<rest>.+)$", re.I)
RE_DELETE = re.compile(r"^\s*(?:DELETE|REMOVE)\s+(?P<rest>.+)$", re.I)
RE_CONVERSE = re.compile(r"^\s*CONVERSE\s+(?P<screen>[A-Z0-9_#$\-\"]+)(?P<rest>.*)$", re.I)
RE_SHOW = re.compile(r"^\s*SHOW\s+(?P<screen>[A-Z0-9_#$\-\"]+)(?P<rest>.*)$", re.I)
RE_CALL = re.compile(r"^\s*(?P<kind>CALL|CHAIN|LINK|TRANSFER)\s+(?P<target>\"[^\"]+\"|[A-Z0-9_#$\-]+)(?P<args>.*)$", re.I)
RE_DO_ENTRY = re.compile(r"^\s*(?:DO|PERFORM)\s+(?P<target>[A-Z0-9_#$\-]+)\s*(?:\((?P<args>[^)]*)\))?\s*$", re.I)
# Local screen-map binding: `SCREEN mapname("physical map")`, same shape as
# VIEW's dataset binding but for a CONVERSE/SHOW target instead of a dataset.
RE_SCREEN = re.compile(r"^\s*SCREEN\s+(?P<name>[A-Z0-9_#$\-]+)\s*\(\s*\"?(?P<target>[^\")]+?)\"?\s*\)", re.I)
# `PROGRAM name(...)` / `INTERFACE name(...)` declare an external call target
# by identifier+arg-list, as distinct from RE_PROGRAM's `PROGRAM "name"`
# self-declaration (no parens) at the top of a program's own source.
RE_EXT_DECL = re.compile(r"^\s*(?P<kind>PROGRAM|INTERFACE)\s+(?P<name>[A-Z0-9_#$\-]+)\s*\((?P<args>[^)]*)\)", re.I)
RE_PAD = re.compile(r"^\s*(?:UN)?PAD\b", re.I)
RE_RELEASE = re.compile(r"^\s*RELEASE\s+(?P<target>[A-Z0-9_#$\-]+)\s*$", re.I)
RE_CLEAR = re.compile(r"^\s*CLEAR\s+(?P<rest>.+)$", re.I)
# `PROMPT "text"` / `PERFORM "target"` -- verbs that take a quoted argument
# directly, no space required (`PERFORM"/BACK,...;TTPLP211"`). PERFORM here
# is a dynamic chain/transfer string (site convention), distinct from
# RE_DO_ENTRY's `PERFORM name` internal-entry-point form above, which never
# has a quote.
RE_PROMPT = re.compile(r'^\s*PROMPT\s*"(?P<screen>[^"]+)"(?P<rest>.*)$', re.I)
RE_PERFORM_STR = re.compile(r'^\s*PERFORM\s*"(?P<target>[^"]+)"', re.I)
# Assignment target: an identifier, optionally subscripted (one level of
# nested parens, e.g. `ATTRIBUTE(MAP,NEXT_SCHD(X))`), optionally followed by
# a `ROUNDED` or `ROUNDED(n)` numeric-rounding qualifier.
_ASSIGN_LHS = (
    r"[A-Z][A-Z0-9_#$\-]*(?:\((?:[^()]*(?:\([^()]*\)[^()]*)*)\))?"
    r"(?:\s*ROUNDED(?:\(\d+\))?)?"
)
_ASSIGN_SEG = rf"(?:{_ASSIGN_LHS}\s*=\s*[^:]+|RESET)"
# Plain assignment(s), e.g. `STATUS="HOLD"`, `X=1:Y=2` chained on one line,
# or a bare `RESET` (alone or chained, e.g. `RESET:X=OUTCT+1`). A trailing
# `:|remark` (this export's inline-remark marker) is already stripped by
# `_strip_trailing_remark` before this ever runs, so it isn't handled here.
RE_ASSIGN = re.compile(rf"^{_ASSIGN_SEG}(?::{_ASSIGN_SEG})*$", re.I)
RE_IF = re.compile(r"^\s*IF(?:\s+|(?=\())(?P<cond>.+?)(?:\s+THEN)?\s*$", re.I)
RE_ELSE = re.compile(r"^\s*ELSE\b", re.I)
RE_WHILE = re.compile(r"^\s*WHILE\s+(?P<cond>.+)$", re.I)
RE_UNTIL = re.compile(r"^\s*UNTIL\s+(?P<cond>.+)$", re.I)
RE_FOR = re.compile(r"^\s*FOR\s+(?P<cond>.+)$", re.I)
RE_CASE = re.compile(r"^\s*CASE\s*(?P<subj>.*)$", re.I)
RE_WHEN = re.compile(r"^\s*WHEN\s+(?P<cond>.+)$", re.I)
RE_END = re.compile(r"^\s*END\s*$", re.I)
RE_ONERR = re.compile(r"^\s*(?:ON\s+ERROR|WHEN\s+ERROR|SIGNAL)\b(?P<rest>.*)$", re.I)
RE_SUPRA_CALL = re.compile(
    r"\b(?P<fn>" + "|".join(sorted(SUPRA_DML, key=len, reverse=True)).replace("-", r"\-") + r")\b"
    r"\s*[,( ]\s*(?P<args>[^)\n]*)", re.I)
RE_TXN = re.compile(r"^\s*(?P<m>" + "|".join(sorted(TXN_MARKERS, key=len, reverse=True)).replace("-", r"\-") + r")\b", re.I)

IDENT = re.compile(r"[A-Z][A-Z0-9_#$\-]*", re.I)
NUM = re.compile(r"(?<![A-Z0-9_])\d+(?:\.\d+)?")
_NOISE = {"AND", "OR", "NOT", "IF", "THEN", "ELSE", "OF", "IN", "TO", "BY", "WHERE",
          "EQ", "NE", "GT", "LT", "GE", "LE", "WHEN", "CASE", "IS", "TRUE", "FALSE"}


def _is_comment(text: str) -> bool:
    s = text.lstrip()
    return any(s.startswith(p) for p in COMMENT_PREFIXES)


def _strip_trailing_remark(stmt: str) -> str:
    """Drop a trailing `:|remark` -- this export's inline-remark marker,
    e.g. `DO DELETE_TTTL:|TEST HOUSE`, `PREV_PACK=...:|RFC-7943` -- from the
    end of a statement, so it stops the verb/keyword patterns below from
    seeing trailing junk after a colon that isn't part of the statement at
    all. Checked against the masked form so a `:|` that happens to appear
    inside a string literal can't trigger a false trim.
    """
    masked, _ = mask_literals(stmt)
    m = re.search(r":\|.*$", masked)
    return stmt[: m.start()].rstrip() if m else stmt


def _split_depth_marker(stmt: str) -> tuple[str, bool]:
    """Strip a leading run of '.' used as a block-depth marker in this export
    style, and report whether what follows is a '|' remark line.

    Returns (body_without_dots, is_remark). `body_without_dots` is safe to run
    the ordinary keyword patterns against for source that has no dots at all
    (zero stripped is a no-op).
    """
    body = stmt.lstrip(".")
    return body, body.startswith("|")


# Bounds how far the continuation-fold below will look ahead per statement,
# mirroring natural.py's own guard against O(n^2) rescans on adversarial or
# malformed input where most lines would otherwise look like continuations.
MAX_CONTINUATION_LOOKAHEAD = 25


def _facts(expr: str) -> tuple[str, str]:
    masked, lits = mask_literals(expr or "")
    idents = [t for t in IDENT.findall(masked) if t.upper() not in _NOISE]
    vals = [l.strip("'\"") for l in lits] + NUM.findall(masked)
    return ",".join(dict.fromkeys(idents))[:500], ",".join(dict.fromkeys(vals))[:500]


def _assignment_pairs(stmt: str, masked: str) -> list[tuple[str, str]]:
    """(lhs_name, rhs_text) for each `LHS=RHS` segment in a (possibly
    `:`-chained) assignment statement, e.g. `LOOKUP_KEY="H"+BUILD_PART(1,1,5)`
    or `X=1:Y=2`. Splits on `:` at paren-depth 0 in `masked` (so a `:`
    inside a literal or a subscript expression can't wrongly split the
    statement) then on the first `=` in each segment; offsets found in
    `masked` are sliced back out of `stmt` directly (mask_literals
    preserves length/position -- see natural.orig) so the recorded RHS is
    the real value, not the masked placeholder. A bare `RESET` segment (no
    `=`) is skipped, same as everywhere else that treats RESET specially."""
    pairs: list[tuple[str, str]] = []
    depth = 0
    seg_start = 0
    bounds: list[tuple[int, int]] = []
    for i, ch in enumerate(masked):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        elif ch == ":" and depth == 0:
            bounds.append((seg_start, i))
            seg_start = i + 1
    bounds.append((seg_start, len(masked)))
    for start, end in bounds:
        eq = masked[start:end].find("=")
        if eq < 0:
            continue
        lhs = stmt[start:start + eq].strip()
        name_m = re.match(r"[A-Z][A-Z0-9_#$\-]*", lhs, re.I)
        if not name_m:
            continue
        rhs = stmt[start + eq + 1:end].strip()
        pairs.append((name_m.group(0).upper(), rhs))
    return pairs


def _key_var_candidates(text: str, entity: str | None) -> list[str]:
    """Bare identifier(s) that look like the key value(s) passed to a
    GET/OBTAIN/Supra-DML style database call, excluding the entity/view
    token itself -- candidates for the backward key-construction trace
    against `last_assign`. Handles both shapes this dialect uses: a
    parenthesised argument list right after the entity name
    (`WIDGETFILE01(LOOKUP_KEY)FIRST`) and a bare comma-separated argument list
    with the entity as one of the tokens (Supra DML's
    `READM(ORDERMST, ORDER_NO)`, already split from its own parens by
    RE_SUPRA_CALL). Only bare identifiers -- a literal or an inline
    expression (`LOOKUP_KEY+1`) isn't a case backward-resolution handles
    usefully, so those are silently skipped, not guessed at."""
    masked, _ = mask_literals(text or "")
    args_masked, args_orig = masked, text
    if entity:
        m = re.search(rf"\b{re.escape(entity)}\s*\(([^)]*)\)", masked, re.I)
        if m:
            args_masked, args_orig = m.group(1), text[m.start(1):m.end(1)]
    out = []
    for tm in re.finditer(r"[^,]+", args_masked):
        seg = args_orig[tm.start():tm.end()].strip()
        if not seg or seg.upper() == (entity or "").upper():
            continue
        if re.fullmatch(r"[A-Z][A-Z0-9_#$\-]*", seg, re.I):
            out.append(seg.upper())
    return out


def _view_target(rest: str) -> tuple[str | None, str]:
    """First identifier in the clause is normally the view or record name."""
    masked, _ = mask_literals(rest)
    m = re.search(r"\b(?:FROM|INTO|IN|OF)\s+([A-Z0-9_#$\-]+)", masked, re.I)
    if m:
        return m.group(1).upper(), rest
    m = IDENT.search(masked)
    return (m.group(0).upper() if m else None), rest


def _scan_routines(lines) -> list[dict]:
    """`ENTRY name` / `EXIT` boundaries, as a standalone pre-scan over the
    raw lines -- mirrors natural.py's _scan_routines for the same reason:
    simpler and safer than threading "current open routine" state through
    extract()'s already-intricate per-statement dispatch below. A Mantis
    program commonly declares several ENTRY points (see e.g.
    examples/inputs/mantis/ORDENQ.mantis's ENTRY MAIN / ENTRY
    VALIDATE_CREDIT_LIMIT) -- each is a callable, independently
    documentable unit, not a nested block, so this never nests.

    Everything before the first ENTRY (PROGRAM/TEXT/VIEW/EXTERNAL
    declarations) belongs to no routine -- callers treat that as the
    member's main/declaration body, same as a line outside every Natural
    DEFINE SUBROUTINE. `end_line` is None when no matching EXIT was found
    before EOF or the next ENTRY -- recorded as unresolved via a gap by the
    caller, not guessed here."""
    routines: list[dict] = []
    open_routine: dict | None = None
    for line_no, _, raw in lines:
        body, is_remark = _split_depth_marker(raw.strip())
        if _is_comment(raw) or is_remark or not raw.strip():
            continue
        stmt = _strip_trailing_remark(body)
        masked, _ = mask_literals(stmt)
        if (m := RE_ENTRY.match(masked)):
            if open_routine is not None:
                routines.append(open_routine)
            open_routine = {
                "name": m.group("name").upper(), "kind": "mantis_entry",
                "start_line": line_no, "end_line": None,
            }
        elif open_routine is not None and RE_EXIT.match(masked):
            open_routine["end_line"] = line_no
            routines.append(open_routine)
            open_routine = None
    if open_routine is not None:
        routines.append(open_routine)
    return routines


def extract(conn, member_id: int, lines, member_name: str = "?") -> dict:
    lines = list(lines)
    routines = _scan_routines(lines)
    internal_entries = {r["name"] for r in routines}
    for r in routines:
        if r["end_line"] is None:
            add_gap(
                conn, "unparsed_line",
                f"ENTRY {r['name']} opened at line {r['start_line']} has no matching "
                "EXIT; its extent is unknown, so facts inside it can't be grouped "
                "under this routine.",
                member_id=member_id, line_no=r["start_line"], severity="medium",
            )
        insert(conn, "routine", member_id=member_id, name=r["name"], kind=r["kind"],
               start_line=r["start_line"], end_line=r["end_line"])

    stats = {"lines": len(lines), "code_lines": 0, "comment_lines": 0, "unparsed": 0}
    views: dict[str, str] = {}
    depth = 0
    open_blocks: list[tuple[str, int]] = []
    # Most recent assignment to each variable name, by line -- consulted by
    # access() below to trace a bare key variable (e.g. `LOOKUP_KEY` in
    # `GET WIDGETFILE01(LOOKUP_KEY)FIRST`) back to the expression that actually
    # built it (`LOOKUP_KEY="H"+BUILD_PART(1,1,5)+...`), so the generated doc
    # doesn't have to describe an opaque token as if it were the real key.
    last_assign: dict[str, tuple[int, str]] = {}
    # IF/ELSE branch-extent tracking, mirroring natural.py's _match_rules:
    # the rule_candidate id of the IF that opened each currently-open
    # block, and of its ELSE (if one has fired), keyed by the IF's own
    # line_no -- so, once the matching END is found, both rows' end_line
    # can be set to where their branch actually ends. Without this, a
    # generated document has no structural cue that a later GET/DELETE/
    # etc. belongs to a particular IF's ELSE branch rather than being
    # unrelated main-line code.
    if_rule_ids: dict[int, int] = {}
    else_rule_ids: dict[int, int] = {}

    def rule(construct, cond, line_no, raw, pair_line_no=None):
        f, l = _facts(cond or "")
        return insert(conn, "rule_candidate", member_id=member_id, line_no=line_no,
               construct=construct, condition=(cond or "").strip()[:500] or None,
               depth=depth, fields_used=f or None, literals=l or None, raw=raw.strip()[:500],
               pair_line_no=pair_line_no)

    def access(verb, crud, entity, key_expr, line_no, raw, via=None, confidence="verified",
               key_vars: list[str] | None = None):
        eid = resolve_entity(conn, entity, "supra", "supra_master") if entity else None
        key_source_line = key_source_expr = None
        for var in key_vars or []:
            prior = last_assign.get(var)
            if prior and prior[0] < line_no:
                key_source_line, key_source_expr = prior
                break
        insert(conn, "data_access", member_id=member_id, line_no=line_no, verb=verb,
               crud=crud, entity_name=entity, entity_id=eid, via_view=via,
               key_expr=(key_expr or "").strip()[:500] or None,
               key_source_line=key_source_line,
               key_source_expr=(key_source_expr or "").strip()[:500] or None,
               raw=raw.strip()[:500], confidence=confidence)

    idx = 0
    while idx < len(lines):
        line_no, seq, raw = lines[idx]
        body, is_remark = _split_depth_marker(raw.strip())
        comment = _is_comment(raw) or is_remark
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=seq,
               text=raw, is_comment=1 if comment else 0)
        if comment:
            stats["comment_lines"] += 1
            idx += 1
            continue
        if not raw.strip():
            idx += 1
            continue
        stats["code_lines"] += 1
        stmt = body

        # Fold a run of '-marked continuation lines onto this statement (see
        # the module docstring) before any keyword pattern sees it. Each
        # folded line is still visited on its own in a later iteration of
        # this same loop -- idx only advances past the statement that
        # started the run, not past the lines folded into it -- so it still
        # gets its own source_line row and, correctly, its own unparsed_line
        # gap for standing alone.
        look = idx
        while look + 1 < len(lines) and look - idx < MAX_CONTINUATION_LOOKAHEAD:
            nxt_body, nxt_is_remark = _split_depth_marker(lines[look + 1][2].strip())
            if nxt_is_remark or not nxt_body.startswith("'"):
                break
            # A single inserted space, not a bare concatenation: real
            # continuations are usually adjacent to a natural delimiter on
            # the prior line (a closing quote or paren) where this dialect's
            # own single-line style already omits the space, but not
            # always -- a numeric or bare-identifier boundary (`500` +
            # `'OR ...`, or the real `...-1` + `'OR ...` shape this was
            # calibrated against) glues into an unrelated-looking token
            # like `500OR` with no separator at all, which distorts the
            # stored condition text. A single space is harmless either way:
            # downstream matching here is already whitespace-tolerant.
            stmt = stmt.rstrip() + " " + nxt_body[1:]
            look += 1

        stmt = _strip_trailing_remark(stmt)
        masked, _ = mask_literals(stmt)
        matched = False

        if (m := RE_PROGRAM.match(stmt)):
            conn.execute("UPDATE member SET object_type=COALESCE(object_type,'program') WHERE id=?", (member_id,))
            matched = True
        elif (m := RE_ENTRY.match(masked)):
            # Some sites export an online/screen program with no `PROGRAM
            # "name"` self-declaration at all -- its only self-identifying
            # statement is the ENTRY point callers dial into (e.g. a CICS-style
            # transaction program). That's still a callable unit by the same
            # definition testplan.py's module docstring uses, so treat it the
            # same as RE_PROGRAM for object_type -- COALESCE leaves an already
            # PROGRAM-declared member's object_type untouched.
            conn.execute("UPDATE member SET object_type=COALESCE(object_type,'program') WHERE id=?", (member_id,))
            insert(conn, "variable", member_id=member_id, scope="entry",
                   name=m.group("name").upper(), init_value=m.group("params"), line_no=line_no)
            matched = True
        elif RE_EXIT.match(masked):
            matched = True
        elif (m := RE_EXTERNAL.match(stmt)):
            # Convention is EXTERNAL "library","program"[,...]. Recording the
            # library as a callee would fabricate a call edge to something that
            # is not a program, which then shows up as a missing module in the
            # gap register and wastes SME review time.
            toks = [(a or b).upper() for a, b in
                    re.findall(r"\"([^\"]+)\"|([A-Z0-9_#$\-]+)", m.group("args"), re.I) if (a or b)]
            library_tok, program_toks = (toks[0], toks[1:]) if len(toks) > 1 else (None, toks)
            for name in program_toks:
                insert(conn, "call_edge", caller_id=member_id, callee_name=name,
                       call_kind="CALL", line_no=line_no,
                       args=f"EXTERNAL declaration in library {library_tok}" if library_tok
                            else "EXTERNAL declaration")
            add_gap(conn, "external_call",
                    f"EXTERNAL declares {', '.join(program_toks) or 'a module'} outside this "
                    f"Mantis library"
                    + (f" (library {library_tok})" if library_tok else "")
                    + ". Behaviour must be documented separately. Confirm the site's argument "
                      "order, since some installations list the program first.",
                    member_id=member_id, line_no=line_no, severity="medium", raw=stmt[:300])
            matched = True
        elif (m := RE_VIEW.match(stmt)):
            # Matched against the unmasked stmt, not masked -- mask_literals
            # replaces a quoted literal (the physical dataset name in the
            # `VIEW name("physical")` form) including its quote characters,
            # so the pof alternative's `\"` could never match a masked
            # string and `pof` was always None. Same reason RE_EXTERNAL
            # above and RE_EXT_DECL below both match unmasked too.
            vname = m.group("name").upper()
            of = (m.group("of") or m.group("pof") or vname).upper()
            views[vname] = of
            resolve_entity(conn, of, "supra", "supra_master")
            insert(conn, "variable", member_id=member_id, scope="view", name=vname,
                   view_of=of, line_no=line_no)
            matched = True
        elif (m := RE_DECL.match(masked)):
            spec = m.group("spec") or ""
            insert(conn, "variable", member_id=member_id, scope="mantis_local",
                   name=m.group("name").upper(), format=m.group("type").upper(),
                   length=spec or None, line_no=line_no)
            matched = True
        elif (m := RE_SCREEN.match(masked)):
            insert(conn, "variable", member_id=member_id, scope="screen",
                   name=m.group("name").upper(), view_of=m.group("target").upper(),
                   line_no=line_no)
            matched = True
        elif (m := RE_EXT_DECL.match(stmt)):
            # `PROGRAM name(...)` / `INTERFACE name(...)` -- an external
            # module reference by identifier+args, as opposed to RE_PROGRAM's
            # `PROGRAM "name"` self-declaration. Mirrors EXTERNAL's callee
            # recording, minus the per-line gap: these declarations are
            # numerous and formulaic at the top of this site's exports, and
            # flagging each one would drown the gap register in noise rather
            # than surface a real question.
            #
            # Matched (and tokenised below) against the unmasked `stmt`, not
            # `masked` -- the target name is inside the quoted first arg
            # (`PROGRAM TTPC003P("TTPC003P",PASSWORD)`), and masking replaces
            # the quotes themselves along with their contents, which left
            # nothing for the quoted-token alternative below to match and
            # silently fell through to the bare word after the comma
            # (`PASSWORD`) as a fabricated callee.
            toks = [(a or b).upper() for a, b in
                    re.findall(r"\"([^\"]+)\"|([A-Z0-9_#$\-]+)", m.group("args"), re.I) if (a or b)]
            target = toks[0] if toks else m.group("name").upper()
            insert(conn, "call_edge", caller_id=member_id, callee_name=target,
                   call_kind="CALL", line_no=line_no,
                   args=f"{m.group('kind').upper()} declaration")
            matched = True

        if not matched and (m := RE_SUPRA_CALL.search(masked)):
            fn = m.group("fn").upper()
            args = m.group("args")
            ent, _ = _view_target(args)
            access(fn, SUPRA_DML.get(fn, "?"), ent, args, line_no, stmt,
                   confidence="verified" if ent else "unresolved",
                   key_vars=_key_var_candidates(args, ent))
            if not ent:
                add_gap(conn, "undefined_entity",
                        f"Supra DML call {fn} found but the target dataset could not be "
                        f"identified from the argument list.",
                        member_id=member_id, line_no=line_no, severity="medium", raw=stmt[:300])
            matched = True

        if not matched:
            for pat, verb, crud in (
                (RE_OBTAIN, "OBTAIN", "R"), (RE_GET, "GET", "R"),
                (RE_INSERT, "INSERT", "C"), (RE_UPDATE, "UPDATE", "U"),
                (RE_DELETE, "DELETE", "D"),
            ):
                if (m := pat.match(masked)):
                    ent, key = _view_target(m.group("rest"))
                    via = ent if ent in views else None
                    resolved = views.get(ent or "", ent)
                    access(verb, crud, resolved, key, line_no, stmt, via=via,
                           key_vars=_key_var_candidates(m.group("rest"), ent))
                    matched = True
                    break

        if not matched and (m := RE_TXN.match(masked)):
            insert(conn, "transaction_marker", member_id=member_id, line_no=line_no,
                   marker=TXN_MARKERS[m.group("m").upper()])
            matched = True

        if not matched:
            for pat, kind in ((RE_CONVERSE, "CONVERSE"), (RE_SHOW, "SHOW"), (RE_PROMPT, "PROMPT")):
                if (m := pat.match(stmt)):
                    screen = m.group("screen").strip('"').upper()
                    insert(conn, "interaction", member_id=member_id, line_no=line_no,
                           kind=kind, target=screen, fields=(m.group("rest") or "").strip()[:300] or None)
                    insert(conn, "call_edge", caller_id=member_id, callee_name=screen,
                           call_kind="INCLUDE", line_no=line_no, args=f"{kind} screen")
                    matched = True
                    break

        if not matched and (m := RE_CALL.match(stmt)):
            target = m.group("target").strip('"').upper()
            dynamic = not m.group("target").startswith('"') and target not in views
            insert(conn, "call_edge", caller_id=member_id, callee_name=target,
                   call_kind=m.group("kind").upper(), dynamic=1 if dynamic else 0,
                   args=(m.group("args") or "").strip()[:400] or None, line_no=line_no)
            if dynamic:
                add_gap(conn, "dynamic_target",
                        f"{m.group('kind').upper()} target {target} is not a quoted literal; "
                        f"callee set is indeterminate from source.",
                        member_id=member_id, line_no=line_no, severity="high", raw=stmt[:300])
            matched = True
        elif not matched and (m := RE_DO_ENTRY.match(masked)):
            # A DO/PERFORM target matching one of this member's own ENTRY
            # points (see _scan_routines) is an internal call, not a
            # missing external module -- tagged the same way natural.py
            # tags PERFORM_INTERNAL, so it doesn't cost an SME a trip to
            # the gap register to confirm what's already provable from
            # source.
            target = m.group("target").upper()
            internal = target in internal_entries
            insert(conn, "call_edge", caller_id=member_id, callee_name=target,
                   call_kind="PERFORM_INTERNAL" if internal else "PERFORM",
                   callee_id=member_id if internal else None,
                   resolved=1 if internal else 0,
                   args=m.group("args"), line_no=line_no)
            matched = True
        elif not matched and (m := RE_PERFORM_STR.match(stmt)):
            # Site convention: `PERFORM"/BACK,APPTT,APPTT,TTGP185P;TTPLP211"`
            # navigates to the next transaction in a ';'-separated chain --
            # the program to the right of the last ';' is the actual target;
            # everything before it is chain/menu-path context, not a callee.
            navtarget = m.group("target").rsplit(";", 1)[-1].strip().upper()
            insert(conn, "call_edge", caller_id=member_id, callee_name=navtarget,
                   call_kind="TRANSFER", args=m.group("target"), line_no=line_no)
            matched = True

        if not matched:
            if RE_END.match(masked):
                if open_blocks:
                    popped_construct, opened_line = open_blocks.pop()
                    if popped_construct == "IF":
                        if_id = if_rule_ids.pop(opened_line, None)
                        else_id = else_rule_ids.pop(opened_line, None)
                        if if_id is not None:
                            conn.execute(
                                "UPDATE rule_candidate SET end_line=? WHERE id=?", (line_no, if_id)
                            )
                        if else_id is not None:
                            conn.execute(
                                "UPDATE rule_candidate SET end_line=? WHERE id=?", (line_no, else_id)
                            )
                depth = max(depth - 1, 0)
                matched = True
            else:
                for pat, name, grp in (
                    (RE_IF, "IF", "cond"), (RE_WHILE, "WHILE", "cond"),
                    (RE_FOR, "FOR", "cond"), (RE_CASE, "CASE", "subj"),
                    (RE_UNTIL, "UNTIL", "cond"), (RE_WHEN, "WHEN", "cond"),
                    (RE_ONERR, "ON ERROR", "rest"),
                ):
                    if (m := pat.match(masked)):
                        rule_id = rule(name, orig(stmt, m, grp), line_no, stmt)
                        if name in {"IF", "WHILE", "FOR", "CASE"}:
                            if name == "IF":
                                if_rule_ids[line_no] = rule_id
                            open_blocks.append((name, line_no))
                            depth += 1
                        matched = True
                        break
                else:
                    if RE_ELSE.match(masked):
                        pair_line = (
                            open_blocks[-1][1] if open_blocks and open_blocks[-1][0] == "IF" else None
                        )
                        else_id = rule("ELSE", None, line_no, stmt, pair_line_no=pair_line)
                        if pair_line is not None:
                            else_rule_ids[pair_line] = else_id
                        matched = True

        if not matched and RE_PAD.match(masked):
            # PAD/UNPAD are field-formatting statements (padding/trimming a
            # value), not conditional logic -- recognised to keep the
            # coverage figure honest, but not treated as a business rule.
            matched = True

        if not matched and RE_CLEAR.match(masked):
            # CLEAR resets one or more screen fields -- a formatting
            # statement, like PAD/UNPAD, not conditional business logic.
            matched = True

        if not matched and (m := RE_RELEASE.match(masked)):
            # RELEASE ident releases either a Supra record lock or a called
            # program's memory, depending on what was previously OBTAINed or
            # PROGRAM-declared -- source alone doesn't disambiguate which, so
            # this stays a bare recognised statement rather than a guessed
            # CRUD access.
            matched = True

        if not matched and RE_ASSIGN.match(masked):
            rule("ASSIGN", stmt, line_no, stmt)
            for var, rhs in _assignment_pairs(stmt, masked):
                last_assign[var] = (line_no, rhs)
            matched = True

        if not matched:
            stats["unparsed"] += 1
            if len(stmt) > 3:
                add_gap(conn, "unparsed_line",
                        f"Statement not recognised by the Mantis scanner in {member_name}. "
                        f"Recurring shapes here indicate the keyword tables need calibration.",
                        member_id=member_id, line_no=line_no, severity="low", raw=stmt[:400])

        idx += 1

    for construct, ln in open_blocks:
        add_gap(conn, "unparsed_line",
                f"{construct} opened at line {ln} has no matching END; block extent unknown.",
                member_id=member_id, line_no=ln, severity="medium")

    recognised = stats["code_lines"] - stats["unparsed"]
    rate = round(recognised / stats["code_lines"], 3) if stats["code_lines"] else 0
    for k, v in stats.items():
        set_metric(conn, member_name, f"mantis.{k}", v)
    set_metric(conn, member_name, "mantis.recognition_rate", rate)
    if stats["code_lines"] >= 20 and rate < 0.85:
        add_gap(conn, "ambiguous_dialect",
                f"Mantis scanner recognised only {rate:.0%} of code lines in {member_name}. "
                f"Calibrate the keyword tables before relying on generated narrative.",
                member_id=member_id, severity="high")
    return stats
