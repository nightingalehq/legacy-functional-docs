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
"""

from __future__ import annotations

import re

from ..db import add_gap, insert, resolve_entity, set_metric, upsert_entity
from .natural import mask_literals, orig

# ------------------------------------------------------- calibratable tables

COMMENT_PREFIXES = ("*", "%", "!", "/*", "//")

DECL_TYPES = (
    "TEXT", "SMALLTEXT", "BIGTEXT", "NUMERIC", "BIGNUMERIC", "SMALLNUMERIC",
    "ARRAY", "KANJI", "LEVEL", "PICTURE",
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
RE_VIEW = re.compile(r"^\s*VIEW\s+(?P<name>[A-Z0-9_#$\-]+)\s*(?:OF\s+(?P<of>[A-Z0-9_#$\-]+))?", re.I)
RE_OBTAIN = re.compile(r"^\s*OBTAIN\s+(?P<rest>.+)$", re.I)
RE_GET = re.compile(r"^\s*GET\s+(?P<rest>.+)$", re.I)
RE_INSERT = re.compile(r"^\s*(?:INSERT|ADD)\s+(?P<rest>.+)$", re.I)
RE_UPDATE = re.compile(r"^\s*(?:UPDATE|REPLACE)\s+(?P<rest>.+)$", re.I)
RE_DELETE = re.compile(r"^\s*(?:DELETE|REMOVE)\s+(?P<rest>.+)$", re.I)
RE_CONVERSE = re.compile(r"^\s*CONVERSE\s+(?P<screen>[A-Z0-9_#$\-\"]+)(?P<rest>.*)$", re.I)
RE_SHOW = re.compile(r"^\s*SHOW\s+(?P<screen>[A-Z0-9_#$\-\"]+)(?P<rest>.*)$", re.I)
RE_CALL = re.compile(r"^\s*(?P<kind>CALL|CHAIN|LINK|TRANSFER)\s+(?P<target>\"[^\"]+\"|[A-Z0-9_#$\-]+)(?P<args>.*)$", re.I)
RE_DO_ENTRY = re.compile(r"^\s*DO\s+(?P<target>[A-Z0-9_#$\-]+)\s*(?:\((?P<args>[^)]*)\))?\s*$", re.I)
RE_IF = re.compile(r"^\s*IF\s+(?P<cond>.+?)(?:\s+THEN)?\s*$", re.I)
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


def _facts(expr: str) -> tuple[str, str]:
    masked, lits = mask_literals(expr or "")
    idents = [t for t in IDENT.findall(masked) if t.upper() not in _NOISE]
    vals = [l.strip("'\"") for l in lits] + NUM.findall(masked)
    return ",".join(dict.fromkeys(idents))[:500], ",".join(dict.fromkeys(vals))[:500]


def _view_target(rest: str) -> tuple[str | None, str]:
    """First identifier in the clause is normally the view or record name."""
    masked, _ = mask_literals(rest)
    m = re.search(r"\b(?:FROM|INTO|IN|OF)\s+([A-Z0-9_#$\-]+)", masked, re.I)
    if m:
        return m.group(1).upper(), rest
    m = IDENT.search(masked)
    return (m.group(0).upper() if m else None), rest


def extract(conn, member_id: int, lines, member_name: str = "?") -> dict:
    stats = {"lines": len(lines), "code_lines": 0, "comment_lines": 0, "unparsed": 0}
    views: dict[str, str] = {}
    depth = 0
    open_blocks: list[tuple[str, int]] = []

    def rule(construct, cond, line_no, raw):
        f, l = _facts(cond or "")
        insert(conn, "rule_candidate", member_id=member_id, line_no=line_no,
               construct=construct, condition=(cond or "").strip()[:500] or None,
               depth=depth, fields_used=f or None, literals=l or None, raw=raw.strip()[:500])

    def access(verb, crud, entity, key_expr, line_no, raw, via=None, confidence="verified"):
        eid = resolve_entity(conn, entity, "supra", "supra_master") if entity else None
        insert(conn, "data_access", member_id=member_id, line_no=line_no, verb=verb,
               crud=crud, entity_name=entity, entity_id=eid, via_view=via,
               key_expr=(key_expr or "").strip()[:500] or None, raw=raw.strip()[:500],
               confidence=confidence)

    for line_no, seq, raw in lines:
        comment = _is_comment(raw)
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=seq,
               text=raw, is_comment=1 if comment else 0)
        if comment:
            stats["comment_lines"] += 1
            continue
        if not raw.strip():
            continue
        stats["code_lines"] += 1
        stmt = raw.strip()
        masked, _ = mask_literals(stmt)
        matched = False

        if (m := RE_PROGRAM.match(stmt)):
            conn.execute("UPDATE member SET object_type=COALESCE(object_type,'program') WHERE id=?", (member_id,))
            matched = True
        elif (m := RE_ENTRY.match(masked)):
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
        elif (m := RE_VIEW.match(masked)):
            vname = m.group("name").upper()
            of = (m.group("of") or vname).upper()
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

        if not matched and (m := RE_SUPRA_CALL.search(masked)):
            fn = m.group("fn").upper()
            args = m.group("args")
            ent, _ = _view_target(args)
            access(fn, SUPRA_DML.get(fn, "?"), ent, args, line_no, stmt,
                   confidence="verified" if ent else "unresolved")
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
                    access(verb, crud, resolved, key, line_no, stmt, via=via)
                    matched = True
                    break

        if not matched and (m := RE_TXN.match(masked)):
            insert(conn, "transaction_marker", member_id=member_id, line_no=line_no,
                   marker=TXN_MARKERS[m.group("m").upper()])
            matched = True

        if not matched:
            for pat, kind in ((RE_CONVERSE, "CONVERSE"), (RE_SHOW, "SHOW")):
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
            insert(conn, "call_edge", caller_id=member_id, callee_name=m.group("target").upper(),
                   call_kind="PERFORM", args=m.group("args"), line_no=line_no)
            matched = True

        if not matched:
            if RE_END.match(masked):
                if open_blocks:
                    open_blocks.pop()
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
                        rule(name, orig(stmt, m, grp), line_no, stmt)
                        if name in {"IF", "WHILE", "FOR", "CASE"}:
                            open_blocks.append((name, line_no))
                            depth += 1
                        matched = True
                        break
                else:
                    if RE_ELSE.match(masked):
                        rule("ELSE", None, line_no, stmt)
                        matched = True

        if not matched:
            stats["unparsed"] += 1
            if len(stmt) > 3 and not re.match(r"^[A-Z0-9_#$\-]+\s*=", stmt, re.I):
                add_gap(conn, "unparsed_line",
                        f"Statement not recognised by the Mantis scanner in {member_name}. "
                        f"Recurring shapes here indicate the keyword tables need calibration.",
                        member_id=member_id, line_no=line_no, severity="low", raw=stmt[:400])

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
