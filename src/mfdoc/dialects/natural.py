"""Natural (Software AG) extractor.

Deliberately a *heuristic line-and-clause scanner*, not a full grammar. Natural
has no statement terminator in structured mode, supports reporting mode with
entirely different block semantics, and permits dynamic targets everywhere. A
real grammar is a multi-month project; a scanner that is honest about what it
could not resolve is worth more for documentation purposes, because every
uncertainty becomes an SME question rather than a confident fabrication.

Contract for any dialect module (see reference/adding-a-dialect.md):
    extract(conn, member_id, lines) -> None
where `lines` is a list of (line_no, seq, text) with line_no being the citation
ordinal. The module writes rows to the fact tables and records a `gap` for
anything it cannot resolve.
"""

from __future__ import annotations

import re

from ..db import add_gap, insert, set_metric, upsert_entity

# --------------------------------------------------------------------- masking

_STR = re.compile(r"'[^']*'|\"[^\"]*\"")


def mask_literals(text: str) -> tuple[str, list[str]]:
    """Replace quoted literals with placeholders so keyword matching is safe."""
    found: list[str] = []

    def _sub(m):
        found.append(m.group(0))
        return "\x00" * len(m.group(0))

    return _STR.sub(_sub, text), found


def orig(stmt: str, m: re.Match, group: str | int) -> str | None:
    """Return the unmasked text behind a group matched against a masked string.

    Literal masking replaces each quoted string with an equal-length run of NULs,
    so character offsets are preserved and the original text can be sliced back
    out. This matters more than it looks: the masked form of
    `IF STATUS NE 'CONF'` loses `CONF`, and `CONF` is the entire business content
    of the rule. Matching on the masked string avoids false keyword hits inside
    literals; slicing the original back out keeps the meaning.
    """
    if m.start(group) < 0:
        return None
    return stmt[m.start(group):m.end(group)]


def strip_comment(text: str) -> tuple[str, bool]:
    """Return (code, is_full_line_comment)."""
    stripped = text.lstrip()
    if stripped.startswith("**") or re.match(r"^\*\s", stripped) or stripped == "*":
        return "", True
    if stripped.startswith("/*"):
        return "", True
    masked, _ = mask_literals(text)
    pos = masked.find("/*")
    if pos >= 0:
        return text[:pos], False
    return text, False


# ------------------------------------------------------------------- patterns

RE_DEFINE_DATA = re.compile(r"^\s*DEFINE\s+DATA\b(.*)$", re.I)
RE_END_DEFINE = re.compile(r"^\s*END-DEFINE\b", re.I)
RE_SCOPE = re.compile(r"^\s*(LOCAL|PARAMETER|GLOBAL|INDEPENDENT|CONTEXT|OBJECT)\b(?:\s+USING\s+(\S+))?", re.I)
RE_VIEW_OF = re.compile(r"^\s*(\d+)\s+([A-Z0-9#@$&\-_.]+)\s+VIEW\s+(?:OF\s+)?([A-Z0-9#@$&\-_.]+)", re.I)
RE_VAR_DECL = re.compile(
    r"^\s*(?P<level>\d+)\s+(?P<name>[A-Z0-9#@$&\-_.\+]+)\s*"
    r"(?:\((?P<fmt>[^)]*)\))?"
    r"(?P<rest>.*)$",
    re.I,
)
RE_REDEFINE = re.compile(r"^\s*REDEFINE\s+([A-Z0-9#@$&\-_.]+)", re.I)
RE_INIT = re.compile(r"\bINIT\s*(?:<(?P<v1>[^>]*)>|\((?P<v2>[^)]*)\))", re.I)

# Data access. `view` may be a view name defined in DEFINE DATA, which is then
# mapped back to its DDM.
RE_READ = re.compile(
    r"^\s*(?:(?P<label>R\d+)\.\s*)?READ\s+(?:\((?P<limit>[^)]*)\)\s+)?"
    r"(?P<rest>.*)$", re.I)
RE_READ_WORK = re.compile(r"^\s*READ\s+WORK\s+(?:FILE\s+)?(?P<num>\d+)(?P<rest>.*)$", re.I)
RE_WRITE_WORK = re.compile(r"^\s*WRITE\s+WORK\s+(?:FILE\s+)?(?P<num>\d+)(?P<rest>.*)$", re.I)
RE_FIND = re.compile(
    r"^\s*(?:(?P<label>F\d+)\.\s*)?FIND\s+(?:\((?P<limit>[^)]*)\)\s+)?"
    r"(?P<mods>(?:NUMBER|FIRST|UNIQUE|ALL)\s+)?"
    r"(?:RECORDS?\s+IN\s+(?:FILE\s+)?)?(?P<view>[A-Z0-9#@$&\-_.]+)(?P<rest>.*)$", re.I)
RE_HISTOGRAM = re.compile(
    r"^\s*(?:(?P<label>H\d+)\.\s*)?HISTOGRAM\s+(?:\((?P<limit>[^)]*)\)\s+)?"
    r"(?:(?:ALL|VALUE\s+IN)\s+)?(?P<view>[A-Z0-9#@$&\-_.]+)(?P<rest>.*)$", re.I)

# UPDATE (R1.) / DELETE (R1.) -- a database-loop label, not a named view.
# The char classes RE_UPDATE/RE_DELETE's `view` group matches don't include
# ")", so what actually lands in `view` for this input is "(R1." (the ")"
# spills into `rest`) -- match against the stripped-down candidate, not an
# assumed balanced "(...)" wrapper.
RE_LOOP_LABEL_REF = re.compile(r"^[A-Z]\d+$", re.I)
RE_GET = re.compile(r"^\s*GET\s+(?P<mods>SAME|TRANSACTION\s+DATA|)\s*(?P<view>[A-Z0-9#@$&\-_.]*)(?P<rest>.*)$", re.I)
RE_STORE = re.compile(r"^\s*STORE\s+(?:RECORD\s+)?(?:IN\s+(?:FILE\s+)?)?(?P<view>[A-Z0-9#@$&\-_.]+)(?P<rest>.*)$", re.I)
RE_UPDATE = re.compile(r"^\s*UPDATE\s+(?:RECORD\s+)?(?:IN\s+(?:FILE\s+)?)?(?P<view>[A-Z0-9#@$&\-_.(]*)(?P<rest>.*)$", re.I)
RE_DELETE = re.compile(r"^\s*DELETE\s+(?:RECORD\s+)?(?:IN\s+(?:FILE\s+)?)?(?P<view>[A-Z0-9#@$&\-_.(]*)(?P<rest>.*)$", re.I)

RE_SQL_SELECT = re.compile(r"\bSELECT\b(?P<cols>.*?)\bFROM\s+(?P<tbl>[A-Z0-9_.\"]+)", re.I | re.S)
RE_SQL_INSERT = re.compile(r"\bINSERT\s+INTO\s+(?P<tbl>[A-Z0-9_.\"]+)", re.I)
RE_SQL_UPDATE = re.compile(r"\bUPDATE\s+(?P<tbl>[A-Z0-9_.\"]+)\s+SET\b", re.I)
RE_SQL_DELETE = re.compile(r"\bDELETE\s+FROM\s+(?P<tbl>[A-Z0-9_.\"]+)", re.I)
RE_PROCESS_SQL = re.compile(r"^\s*PROCESS\s+SQL\b", re.I)

RE_ET = re.compile(r"^\s*END\s+(?:OF\s+)?TRANSACTION\b(?P<rest>.*)$", re.I)
RE_BT = re.compile(r"^\s*BACKOUT\s+TRANSACTION\b", re.I)

# RESET sets a field back to its initial value; IGNORE is a no-op, most often
# seen inside DELETE/loop processing. Neither carries a business decision by
# itself -- structural syntax, like END-IF -- so they're recognised (to stop
# them showing up as unparsed_line gaps) without capturing a rule_candidate.
# Found via a smoke test against SoftwareAG/adabas-natural-code-samples
# (issue 4.11): RESET was already the one pre-existing unparsed_line gap in
# our own MMP0100.nsp fixture, just never named until that corpus made the
# pattern obvious at scale.
RE_RESET = re.compile(r"^\s*RESET\b(?P<rest>.*)$", re.I)
RE_IGNORE = re.compile(r"^\s*IGNORE\s*$", re.I)

RE_CALLNAT = re.compile(r"^\s*CALLNAT\s+(?P<target>'[^']+'|\"[^\"]+\"|[A-Z0-9#@$&\-_.]+)(?P<args>.*)$", re.I)
RE_FETCH = re.compile(r"^\s*FETCH\s+(?P<ret>RETURN\s+|REPEAT\s+)?(?P<target>'[^']+'|\"[^\"]+\"|[A-Z0-9#@$&\-_.]+)(?P<args>.*)$", re.I)
RE_PERFORM = re.compile(r"^\s*PERFORM\s+(?!BREAK)(?P<target>'[^']+'|[A-Z0-9#@$&\-_.]+)(?P<args>.*)$", re.I)
RE_CALL = re.compile(r"^\s*CALL\s+(?:FILE\s+)?(?P<target>'[^']+'|\"[^\"]+\"|[A-Z0-9#@$&\-_.]+)(?P<args>.*)$", re.I)
RE_INCLUDE = re.compile(r"^\s*INCLUDE\s+(?P<target>[A-Z0-9#@$&\-_.]+)(?P<args>.*)$", re.I)
RE_RUN = re.compile(r"^\s*RUN\s+(?P<target>'[^']+'|[A-Z0-9#@$&\-_.]+)", re.I)
RE_DEFINE_SUB = re.compile(r"^\s*DEFINE\s+SUBROUTINE\s+(?P<name>[A-Z0-9#@$&\-_.]+)", re.I)

RE_IF = re.compile(r"^\s*IF\s+(?P<cond>.+?)\s*$", re.I)
RE_IF_NO = re.compile(r"^\s*IF\s+NO\s+RECORDS?\s+(?:FOUND|WERE\s+FOUND)", re.I)
RE_ELSE = re.compile(r"^\s*ELSE\b", re.I)
RE_DECIDE_ON = re.compile(r"^\s*DECIDE\s+ON\s+(?P<mods>FIRST|EVERY)\s+VALUE\s*(?:OF)?\s*(?P<subj>.*)$", re.I)
RE_DECIDE_FOR = re.compile(r"^\s*DECIDE\s+FOR\s+(?P<mods>FIRST|EVERY)\s+CONDITION", re.I)
RE_VALUE = re.compile(r"^\s*VALUE\s+(?P<v>.+)$", re.I)
RE_WHEN = re.compile(r"^\s*WHEN\s+(?P<cond>.+)$", re.I)
RE_NONE_ANY_ALL = re.compile(r"^\s*(NONE|ANY|ALL)\s*(?:VALUE)?\b", re.I)
RE_FOR = re.compile(r"^\s*FOR\s+(?P<cond>.+)$", re.I)
RE_REPEAT = re.compile(r"^\s*REPEAT\b(?P<cond>.*)$", re.I)
RE_ESCAPE = re.compile(r"^\s*ESCAPE\s+(?P<cond>.+)$", re.I)
RE_AT_EVENT = re.compile(r"^\s*AT\s+(?P<ev>BREAK|END\s+OF\s+DATA|START\s+OF\s+DATA|TOP\s+OF\s+PAGE|END\s+OF\s+PAGE)\b(?P<rest>.*)$", re.I)
RE_ON_ERROR = re.compile(r"^\s*ON\s+ERROR\b", re.I)
RE_END_ANY = re.compile(r"^\s*END-(IF|DECIDE|FOR|REPEAT|WHILE|ALL|WORK|SUBROUTINE|BREAK|ENDDATA|ERROR|START|TOPPAGE|NOREC|READ|FIND|HISTOGRAM|BEFORE|PROCESS)\b", re.I)

RE_INPUT = re.compile(r"^\s*INPUT\b(?P<rest>.*)$", re.I)
RE_REINPUT = re.compile(r"^\s*REINPUT\b(?P<rest>.*)$", re.I)
RE_WRITE = re.compile(r"^\s*(WRITE|DISPLAY|PRINT)\b(?P<rest>.*)$", re.I)
RE_USING_MAP = re.compile(r"USING\s+MAP\s+(?P<map>'[^']+'|[A-Z0-9#@$&\-_.]+)", re.I)

RE_COMPUTE = re.compile(r"^\s*(?:COMPUTE|ADD|SUBTRACT|MULTIPLY|DIVIDE|MOVE|EXAMINE|COMPRESS|SEPARATE|ASSIGN)\b", re.I)
RE_MSG_NUM = re.compile(r"\bMESSAGE\s+NUMBER\s+(?P<num>[0-9#A-Z\-]+)", re.I)

# The classic loop-counter idiom: ADD 1 TO x / SUBTRACT 1 FROM x. Excluded
# from arithmetic rule capture even though "1" is technically a literal,
# because it is almost never a business decision.
RE_LOOP_COUNTER = re.compile(r"^\s*(?:ADD\s+1\s+TO|SUBTRACT\s+1\s+FROM)\b", re.I)

# Reporting-mode tells
RE_REPORTING = re.compile(r"^\s*(LOOP\b|DO\b\s*$|DOEND\b)", re.I)

_STRUCTURED_TELLS = re.compile(r"\bEND-(IF|DEFINE|DECIDE|REPEAT|FOR|SUBROUTINE|WORK|ALL)\b", re.I)

IDENT = re.compile(r"[A-Z][A-Z0-9#@$&\-_.]*", re.I)
NUMLIT = re.compile(r"(?<![A-Z0-9#\-])\d+(?:\.\d+)?")

CONTINUATION_TAIL = re.compile(r"(\b(AND|OR|NOT|THRU|THROUGH|TO|WITH|BY)\s*$)|([=<>,+\-*/]\s*$)", re.I)

# Real Natural wraps long clauses at whatever column runs out, not only after a
# connective -- a `FIND ... WITH NAME = 'SMITH'` condition commonly breaks
# *before* the `AND` on the next line, so the first physical line ends in a
# closing quote or identifier with no trailing token at all. CONTINUATION_TAIL
# alone misses that case and truncates the statement mid-condition -- a silent
# partial rule, since the citation still looks complete. None of these leading
# tokens are ever the first word of a genuine new Natural statement, so
# checking the *next* line's lead is a safe second signal with no added
# false-continuation risk.
CONTINUATION_LEAD = re.compile(r"^\s*(AND|OR|NOT|THRU|THROUGH|TO|WITH|BY)\b", re.I)

# Bounds how far the continuation-fold below will look ahead per line. Without
# this, a source file where most lines end in a continuation token (adversarial
# or just malformed input) makes every line rescan the rest of the file, which
# is O(n^2) in file length.
MAX_CONTINUATION_LOOKAHEAD = 25

# Maps an END-xxx keyword to the opener label(s) it can legitimately close.
# Keywords not listed here (READ, FIND, HISTOGRAM, WORK, SUBROUTINE, WHILE,
# ALL, PROCESS, NOREC, BEFORE) have no matching opener tracked in open_blocks
# -- e.g. READ/FIND/HISTOGRAM never push -- so their END- must be a no-op
# rather than popping an unrelated block (previously this silently corrupted
# nesting for any IF/DECIDE/FOR/REPEAT wrapping a READ...END-READ).
_END_TO_OPENERS = {
    "IF": {"IF", "IF NO RECORDS FOUND"},
    "NOREC": {"IF NO RECORDS FOUND"},
    "DECIDE": {"DECIDE"},
    "FOR": {"FOR"},
    "REPEAT": {"REPEAT"},
    "ERROR": {"ON ERROR"},
    "BREAK": {"AT-EVENT"},
    "ENDDATA": {"AT-EVENT"},
    "START": {"AT-EVENT"},
    "TOPPAGE": {"AT-EVENT"},
}


def _clean_target(tok: str) -> tuple[str, bool]:
    """Return (name, is_dynamic). Quoted literal => static; bare #VAR => dynamic."""
    tok = tok.strip()
    if tok.startswith(("'", '"')):
        return tok.strip("'\""), False
    if tok.startswith(("#", "+")) or tok.upper().startswith("*"):
        return tok, True
    return tok, False


def _condition_facts(cond: str) -> tuple[str, str]:
    masked, literals = mask_literals(cond)
    idents = [t for t in IDENT.findall(masked) if t.upper() not in _NOISE_WORDS]
    nums = NUMLIT.findall(masked)
    lits = [l.strip("'\"") for l in literals] + nums
    return ",".join(dict.fromkeys(idents)), ",".join(dict.fromkeys(lits))


_NOISE_WORDS = {
    "AND", "OR", "NOT", "IF", "THEN", "ELSE", "EQ", "NE", "GT", "LT", "GE", "LE",
    "MASK", "SCAN", "THRU", "THROUGH", "TO", "BY", "WITH", "WHEN", "VALUE", "IS",
    "OF", "IN", "FOR", "MODIFIED", "SPECIFIED", "BREAK", "TRUE", "FALSE",
}


# --------------------------------------------------------------------- extract


def detect_mode(lines) -> str:
    body = "\n".join(t for _, _, t in lines)
    if _STRUCTURED_TELLS.search(body):
        return "structured"
    if any(RE_REPORTING.match(t) for _, _, t in lines):
        return "reporting"
    return "unknown"


def extract(conn, member_id: int, lines: list[tuple[int, str | None, str]], member_name: str = "?") -> dict:
    """Populate fact tables for one Natural member."""
    mode = detect_mode(lines)
    conn.execute("UPDATE member SET mode=? WHERE id=?", (mode, member_id))
    if mode == "reporting":
        add_gap(
            conn, "reporting_mode",
            "Member appears to be written in Natural reporting mode; block scope is "
            "implicit so loop and condition nesting reported here is unreliable and "
            "needs SME confirmation.",
            member_id=member_id, severity="high",
        )

    view_to_ddm: dict[str, str] = {}
    # Maps a database-loop label (R1, F1, H1, ...) to the (entity, via_view)
    # its FIND/READ/HISTOGRAM opened, so a later UPDATE (R1.) / DELETE (R1.)
    # -- "act on the record this labelled loop is currently processing" --
    # can resolve to the same store instead of staying an unresolved gap.
    label_to_view: dict[str, tuple[str, str]] = {}
    # Pre-scan: a PERFORM can precede the DEFINE SUBROUTINE it targets, and an
    # internal subroutine wrongly reported as a missing external module puts a
    # spurious high-severity item in the gap register that an SME then has to
    # chase down and dismiss.
    internal_subroutines: set[str] = {
        m.group("name").upper()
        for _, _, t in lines
        if (m := RE_DEFINE_SUB.match(strip_comment(t)[0]))
    }
    stats = {"lines": len(lines), "code_lines": 0, "comment_lines": 0, "unparsed": 0}

    in_define = False
    scope = None
    depth = 0
    open_blocks: list[tuple[str, int]] = []

    idx = 0
    while idx < len(lines):
        line_no, seq, raw = lines[idx]
        code, is_comment = strip_comment(raw)
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=seq,
               text=raw, is_comment=1 if is_comment else 0)
        if is_comment or not code.strip():
            stats["comment_lines"] += 1 if is_comment else 0
            idx += 1
            continue
        stats["code_lines"] += 1

        # Fold obvious continuations so key expressions survive intact.
        stmt = code
        look = idx
        while look + 1 < len(lines) and look - idx < MAX_CONTINUATION_LOOKAHEAD:
            nxt_code, nxt_comment = strip_comment(lines[look + 1][2])
            if nxt_comment:
                look += 1
                continue
            if not (CONTINUATION_TAIL.search(stmt.rstrip()) or CONTINUATION_LEAD.match(nxt_code)):
                break
            look += 1
            stmt = stmt.rstrip() + " " + nxt_code.strip()
        masked, _ = mask_literals(stmt)

        matched = False

        # ---------------------------------------------------------- DEFINE DATA
        if RE_DEFINE_DATA.match(masked):
            in_define, scope, matched = True, None, True
        elif RE_END_DEFINE.match(masked):
            in_define, matched = False, True
        elif in_define:
            matched = True
            m = RE_SCOPE.match(stmt)
            if m:
                scope = m.group(1).lower()
                using = m.group(2)
                if using:
                    insert(conn, "call_edge", caller_id=member_id, callee_name=using.upper(),
                           call_kind="INCLUDE", line_no=line_no, args=f"DEFINE DATA {scope} USING")
                    insert(conn, "variable", member_id=member_id, scope=scope, name=f"USING {using.upper()}",
                           line_no=line_no)
            elif (v := RE_VIEW_OF.match(stmt)):
                lvl, vname, ddm = v.group(1), v.group(2).upper(), v.group(3).upper()
                view_to_ddm[vname] = ddm
                eid = upsert_entity(conn, ddm, "ddm")
                insert(conn, "variable", member_id=member_id, scope="view", level=int(lvl),
                       name=vname, view_of=ddm, line_no=line_no)
                _ = eid  # entity registered; access rows carry the code-level linkage
            elif (d := RE_VAR_DECL.match(stmt)):
                rest = d.group("rest") or ""
                fmt_raw = (d.group("fmt") or "").strip()
                fmt, length = _split_format(fmt_raw)
                init = RE_INIT.search(rest)
                insert(conn, "variable", member_id=member_id, scope=scope or "local",
                       level=int(d.group("level")), name=d.group("name").upper(),
                       format=fmt, length=length,
                       init_value=(init.group("v1") or init.group("v2")) if init else None,
                       line_no=line_no)
            elif RE_REDEFINE.match(stmt):
                pass
            else:
                stats["unparsed"] += 1

        # -------------------------------------------------------- data access
        if not matched:
            matched = _match_data_access(conn, member_id, line_no, stmt, masked, view_to_ddm, label_to_view)

        # ------------------------------------------------------------- calls
        if not matched:
            matched = _match_calls(conn, member_id, line_no, stmt, masked,
                                   internal_subroutines, member_id)

        # ------------------------------------------------ transaction markers
        if not matched:
            if (m := RE_ET.match(masked)):
                insert(conn, "transaction_marker", member_id=member_id, line_no=line_no,
                       marker="END TRANSACTION", et_data=(m.group("rest") or "").strip() or None)
                matched = True
            elif RE_BT.match(masked):
                insert(conn, "transaction_marker", member_id=member_id, line_no=line_no,
                       marker="BACKOUT TRANSACTION")
                matched = True

        # -------------------------------------------------------- interaction
        if not matched:
            matched = _match_interaction(conn, member_id, line_no, stmt, masked)

        # ------------------------------------------------------ rule candidates
        if not matched:
            matched, depth, open_blocks = _match_rules(
                conn, member_id, line_no, stmt, masked, depth, open_blocks)

        # ------------------------------------------------ arithmetic candidates
        if not matched:
            matched = _match_arithmetic(conn, member_id, line_no, stmt, masked, depth)

        if not matched:
            if RE_COMPUTE.match(masked) or RE_END_ANY.match(masked) \
               or RE_RESET.match(masked) or RE_IGNORE.match(masked):
                matched = True
            else:
                stats["unparsed"] += 1
                if len(masked.strip()) > 3:
                    add_gap(conn, "unparsed_line",
                            f"Statement not recognised by the Natural scanner in {member_name}.",
                            member_id=member_id, line_no=line_no, severity="low", raw=stmt.strip()[:400])

        idx += 1

    # Close any blocks left open (common in reporting mode).
    for construct, ln in open_blocks:
        add_gap(conn, "unparsed_line",
                f"{construct} opened at line {ln} has no matching END- statement; "
                f"block extent is unknown.",
                member_id=member_id, line_no=ln, severity="medium")

    for name, val in stats.items():
        set_metric(conn, member_name, f"natural.{name}", val)
    set_metric(conn, member_name, "natural.views", list(view_to_ddm.items()))
    return stats


def _split_format(fmt_raw: str) -> tuple[str | None, str | None]:
    if not fmt_raw:
        return None, None
    m = re.match(r"^\s*([ABPNIFLDTCU])\s*(\d+(?:[.,]\d+)?)?", fmt_raw, re.I)
    if m:
        return m.group(1).upper(), (m.group(2) or (fmt_raw if "/" in fmt_raw else None))
    return None, fmt_raw


def _resolve(view: str, view_to_ddm: dict) -> tuple[str, str]:
    v = view.upper().strip("() ")
    if v in view_to_ddm:
        return view_to_ddm[v], v
    return v, v


def _resolve_loop_label(view: str, label_to_view: dict) -> tuple[str, str] | None:
    """UPDATE (R1.) / DELETE (R1.) act on the record the labelled loop R1 is
    currently processing. Only resolves the R#/F#/H# convention that
    RE_READ/RE_FIND/RE_HISTOGRAM already recognise as a label -- an
    unrecognised label stays an honest gap rather than a guess."""
    candidate = view.upper().strip("().# ")
    if RE_LOOP_LABEL_REF.match(candidate):
        return label_to_view.get(candidate)
    return None


def _match_data_access(conn, member_id, line_no, stmt, masked, view_to_ddm, label_to_view=None) -> bool:
    label_to_view = label_to_view if label_to_view is not None else {}
    def rec(verb, crud, entity_name, via_view, key_expr, descriptor=None, confidence="verified"):
        eid = None
        if entity_name and not entity_name.startswith("#"):
            kind = "workfile" if verb.endswith("WORK FILE") else ("sql_table" if verb in {"SELECT", "INSERT", "UPDATE-SQL", "DELETE-SQL"} else "ddm")
            eid = upsert_entity(conn, entity_name, kind)
        insert(conn, "data_access", member_id=member_id, line_no=line_no, verb=verb,
               crud=crud, entity_name=entity_name, entity_id=eid, via_view=via_view,
               key_expr=(key_expr or "").strip()[:500] or None, descriptor=descriptor,
               raw=stmt.strip()[:500], confidence=confidence)

    if (m := RE_READ_WORK.match(masked)):
        rec("READ WORK FILE", "R", f"WORKFILE-{m.group('num')}", None, m.group("rest"))
        return True
    if (m := RE_WRITE_WORK.match(masked)):
        rec("WRITE WORK FILE", "C", f"WORKFILE-{m.group('num')}", None, m.group("rest"))
        return True

    if RE_PROCESS_SQL.match(masked) or RE_SQL_SELECT.search(masked):
        for pat, verb, crud in (
            (RE_SQL_SELECT, "SELECT", "R"),
            (RE_SQL_INSERT, "INSERT", "C"),
            (RE_SQL_UPDATE, "UPDATE-SQL", "U"),
            (RE_SQL_DELETE, "DELETE-SQL", "D"),
        ):
            for sm in pat.finditer(stmt):
                tbl = sm.group("tbl").strip('"').upper()
                rec(verb, crud, tbl, None, stmt)
        return True

    if (rm := RE_READ.match(masked)) and not RE_READ_WORK.match(masked):
        rest = rm.group("rest") or ""
        # READ [MULTI-FETCH] [IN] [LOGICAL|PHYSICAL] [SEQUENCE] view ...
        rest_clean = re.sub(r"^\s*(?:MULTI-FETCH\s+\S+\s+)?(?:IN\s+)?(?:LOGICAL|PHYSICAL|BY\s+ISN)?\s*(?:SEQUENCE\s+)?", "", rest, flags=re.I)
        vm = re.match(r"(?P<view>[A-Z0-9#@$&\-_.]+)(?P<tail>.*)", rest_clean, re.I)
        if vm:
            ent, via = _resolve(vm.group("view"), view_to_ddm)
            if rm.group("label"):
                label_to_view[rm.group("label").upper()] = (ent, via)
            tail = vm.group("tail") or ""
            desc = None
            dm = re.search(r"\b(?:BY|WITH)\s+([A-Z0-9#@$&\-_.]+)", tail, re.I)
            if dm:
                desc = dm.group(1).upper()
            rec("READ", "R", ent, via, tail, desc)
            return True

    if (m := RE_FIND.match(masked)):
        ent, via = _resolve(m.group("view"), view_to_ddm)
        if m.group("label"):
            label_to_view[m.group("label").upper()] = (ent, via)
        tail = m.group("rest") or ""
        desc = None
        dm = re.search(r"\bWITH\s+(?:LIMIT\s*\([^)]*\)\s*)?([A-Z0-9#@$&\-_.]+)", tail, re.I)
        if dm:
            desc = dm.group(1).upper()
        verb = "FIND NUMBER" if (m.group("mods") or "").upper().startswith("NUMBER") else "FIND"
        rec(verb, "R", ent, via, tail, desc)
        return True

    if (m := RE_HISTOGRAM.match(masked)):
        ent, via = _resolve(m.group("view"), view_to_ddm)
        if m.group("label"):
            label_to_view[m.group("label").upper()] = (ent, via)
        tail = m.group("rest") or ""
        dm = re.search(r"\b(?:FOR|VALUE\s+FOR)\s+(?:FIELD\s+)?([A-Z0-9#@$&\-_.]+)", tail, re.I)
        rec("HISTOGRAM", "R", ent, via, tail, dm.group(1).upper() if dm else None)
        return True

    if (m := RE_GET.match(masked)) and m.group(0).strip():
        mods = (m.group("mods") or "").upper()
        if "TRANSACTION" in mods:
            insert(conn, "transaction_marker", member_id=member_id, line_no=line_no,
                   marker="GET TRANSACTION DATA")
            return True
        view = m.group("view") or ""
        if view:
            ent, via = _resolve(view, view_to_ddm)
            rec("GET SAME" if "SAME" in mods else "GET", "R", ent, via, m.group("rest"))
            return True

    if (m := RE_STORE.match(masked)):
        ent, via = _resolve(m.group("view"), view_to_ddm)
        rec("STORE", "C", ent, via, m.group("rest"))
        return True

    if (m := RE_UPDATE.match(masked)):
        view = (m.group("view") or "").strip()
        if not view or view.startswith("("):
            resolved = _resolve_loop_label(view, label_to_view)
            if resolved:
                rec("UPDATE", "U", resolved[0], resolved[1], m.group(0))
            else:
                # UPDATE (label) with a label this scan couldn't resolve --
                # e.g. it isn't the recognised R#/F#/H# loop-label
                # convention. Honest answer: unknown, so flag it.
                rec("UPDATE", "U", None, None, m.group(0), confidence="unresolved")
                add_gap(conn, "dynamic_target",
                        "UPDATE refers to a processing-loop label rather than a named view; "
                        "the target file must be confirmed from the enclosing loop.",
                        member_id=member_id, line_no=line_no, severity="medium",
                        raw=stmt.strip()[:300])
        else:
            ent, via = _resolve(view, view_to_ddm)
            rec("UPDATE", "U", ent, via, m.group("rest"))
        return True

    if (m := RE_DELETE.match(masked)):
        view = (m.group("view") or "").strip()
        if not view or view.startswith("("):
            resolved = _resolve_loop_label(view, label_to_view)
            if resolved:
                rec("DELETE", "D", resolved[0], resolved[1], m.group(0))
            else:
                rec("DELETE", "D", None, None, m.group(0), confidence="unresolved")
                add_gap(conn, "dynamic_target",
                        "DELETE refers to a processing-loop label rather than a named view; "
                        "the target file must be confirmed from the enclosing loop.",
                        member_id=member_id, line_no=line_no, severity="medium",
                        raw=stmt.strip()[:300])
        else:
            ent, via = _resolve(view, view_to_ddm)
            rec("DELETE", "D", ent, via, m.group("rest"))
        return True

    return False


def _match_calls(conn, member_id, line_no, stmt, masked, internal_subroutines,
                 self_member_id=None) -> bool:
    def rec(kind, target_tok, args):
        name, dynamic = _clean_target(target_tok)
        internal = name.upper() in internal_subroutines
        insert(conn, "call_edge", caller_id=member_id,
               callee_name=name.upper(),
               call_kind="PERFORM_INTERNAL" if (internal and kind == "PERFORM") else kind,
               dynamic=1 if dynamic else 0,
               callee_id=self_member_id if internal else None,
               resolved=1 if internal else 0,
               args=(args or "").strip()[:400] or None, line_no=line_no)
        if dynamic:
            add_gap(conn, "dynamic_target",
                    f"{kind} target is a variable ({name}); the set of possible callees "
                    f"cannot be determined from source alone.",
                    member_id=member_id, line_no=line_no, severity="high",
                    raw=stmt.strip()[:300])

    if (m := RE_DEFINE_SUB.match(masked)):
        internal_subroutines.add(m.group("name").upper())
        return True
    if (m := RE_CALLNAT.match(stmt)):
        rec("CALLNAT", m.group("target"), m.group("args"))
        return True
    if (m := RE_FETCH.match(stmt)):
        kind = "FETCH RETURN" if (m.group("ret") or "").upper().startswith("RETURN") else "FETCH"
        rec(kind, m.group("target"), m.group("args"))
        return True
    if (m := RE_PERFORM.match(stmt)):
        rec("PERFORM", m.group("target"), m.group("args"))
        return True
    if (m := RE_INCLUDE.match(stmt)):
        rec("INCLUDE", m.group("target"), m.group("args"))
        return True
    if (m := RE_RUN.match(stmt)):
        rec("RUN", m.group("target"), None)
        return True
    if (m := RE_CALL.match(stmt)):
        rec("CALL", m.group("target"), m.group("args"))
        add_gap(conn, "external_call",
                "CALL invokes a non-Natural (3GL) module; its behaviour is outside the "
                "scanned source and needs separate documentation.",
                member_id=member_id, line_no=line_no, severity="high",
                raw=stmt.strip()[:300])
        return True
    return False


def _match_interaction(conn, member_id, line_no, stmt, masked) -> bool:
    if (m := RE_REINPUT.match(masked)):
        rest = m.group("rest") or ""
        mm = RE_MSG_NUM.search(stmt)
        _, lits = mask_literals(stmt)
        insert(conn, "interaction", member_id=member_id, line_no=line_no, kind="REINPUT",
               target=None, fields=rest.strip()[:300] or None)
        insert(conn, "message_ref", member_id=member_id, line_no=line_no, kind="REINPUT",
               number=mm.group("num") if mm else None,
               text=(lits[0].strip("'\"") if lits else None))
        return True
    if (m := RE_INPUT.match(masked)):
        rest = m.group("rest") or ""
        mp = RE_USING_MAP.search(stmt)
        insert(conn, "interaction", member_id=member_id, line_no=line_no, kind="INPUT",
               target=(mp.group("map").strip("'\"").upper() if mp else None),
               fields=rest.strip()[:300] or None)
        if mp:
            insert(conn, "call_edge", caller_id=member_id,
                   callee_name=mp.group("map").strip("'\"").upper(),
                   call_kind="INCLUDE", line_no=line_no, args="USING MAP")
        return True
    if (m := RE_WRITE.match(masked)):
        insert(conn, "interaction", member_id=member_id, line_no=line_no,
               kind=m.group(1).upper(), target=None,
               fields=(m.group("rest") or "").strip()[:300] or None)
        return True
    return False


def _match_arithmetic(conn, member_id, line_no, stmt, masked, depth) -> bool:
    """Capture COMPUTE/MOVE/ADD/SUBTRACT/MULTIPLY/DIVIDE/EXAMINE as a rule
    candidate when a literal is involved -- assigning a fixed value to a
    field (a status code, a threshold, a return code) is a business
    decision. Pure variable-to-variable movement or accumulation (no
    literal operand) and the ADD/SUBTRACT-1 loop-counter idiom are left
    alone; capturing every arithmetic statement would bury the ones that
    actually carry a decision under running totals and index increments.
    """
    if not RE_COMPUTE.match(masked) or RE_LOOP_COUNTER.match(masked):
        return False
    _, str_literals = mask_literals(stmt)
    if not str_literals and not NUMLIT.search(masked):
        return False
    verb = masked.split()[0].upper()
    fields, lits = _condition_facts(stmt)
    insert(conn, "rule_candidate", member_id=member_id, line_no=line_no,
           construct=verb, condition=stmt.strip()[:500] or None,
           depth=depth, fields_used=fields[:500] or None, literals=lits[:500] or None,
           raw=stmt.strip()[:500])
    return True


def _match_rules(conn, member_id, line_no, stmt, masked, depth, open_blocks):
    def rec(construct, cond):
        fields, lits = _condition_facts(cond or "")
        insert(conn, "rule_candidate", member_id=member_id, line_no=line_no,
               construct=construct, condition=(cond or "").strip()[:500] or None,
               depth=depth, fields_used=fields[:500] or None, literals=lits[:500] or None,
               raw=stmt.strip()[:500])

    if (m := RE_END_ANY.match(masked)):
        openers = _END_TO_OPENERS.get(m.group(1).upper())
        if openers and open_blocks and open_blocks[-1][0] in openers:
            open_blocks.pop()
            return True, max(depth - 1, 0), open_blocks
        return True, depth, open_blocks
    if RE_IF_NO.match(masked):
        rec("IF NO RECORDS FOUND", "no records found for preceding database loop")
        open_blocks.append(("IF NO RECORDS FOUND", line_no))
        return True, depth + 1, open_blocks
    if (m := RE_IF.match(masked)):
        rec("IF", orig(stmt, m, "cond"))
        open_blocks.append(("IF", line_no))
        return True, depth + 1, open_blocks
    if RE_ELSE.match(masked):
        rec("ELSE", None)
        return True, depth, open_blocks
    if (m := RE_DECIDE_ON.match(masked)):
        rec(f"DECIDE ON {m.group('mods').upper()}", orig(stmt, m, "subj"))
        open_blocks.append(("DECIDE", line_no))
        return True, depth + 1, open_blocks
    if (m := RE_DECIDE_FOR.match(masked)):
        rec(f"DECIDE FOR {m.group('mods').upper()} CONDITION", None)
        open_blocks.append(("DECIDE", line_no))
        return True, depth + 1, open_blocks
    if (m := RE_VALUE.match(masked)):
        rec("VALUE", orig(stmt, m, "v"))
        return True, depth, open_blocks
    if (m := RE_WHEN.match(masked)):
        rec("WHEN", orig(stmt, m, "cond"))
        return True, depth, open_blocks
    if RE_NONE_ANY_ALL.match(masked):
        rec("NONE/ANY/ALL", None)
        return True, depth, open_blocks
    if (m := RE_FOR.match(masked)):
        rec("FOR", orig(stmt, m, "cond"))
        open_blocks.append(("FOR", line_no))
        return True, depth + 1, open_blocks
    if (m := RE_REPEAT.match(masked)):
        rec("REPEAT", orig(stmt, m, "cond"))
        open_blocks.append(("REPEAT", line_no))
        return True, depth + 1, open_blocks
    if (m := RE_ESCAPE.match(masked)):
        target = (orig(stmt, m, "cond") or "").strip()
        # ESCAPE ROUTINE / BOTTOM / TOP is a jump, not a predicate. Storing the
        # target as a condition makes the rule register read as though the
        # program tested a field called ROUTINE.
        rec(f"ESCAPE {target.split()[0].upper()}" if target else "ESCAPE", None)
        return True, depth, open_blocks
    if (m := RE_AT_EVENT.match(masked)):
        rec(f"AT {m.group('ev').upper()}", (orig(stmt, m, "rest") or "").strip())
        open_blocks.append(("AT-EVENT", line_no))
        return True, depth + 1, open_blocks
    if RE_ON_ERROR.match(masked):
        rec("ON ERROR", None)
        insert(conn, "message_ref", member_id=member_id, line_no=line_no, kind="ON ERROR")
        open_blocks.append(("ON ERROR", line_no))
        return True, depth + 1, open_blocks
    return False, depth, open_blocks
