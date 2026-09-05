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


# A generic Natural statement label (e.g. "SETA. SETTIME") -- not the
# R#/F#/H# loop-label convention specific verbs already capture inline (see
# RE_READ/RE_FIND/RE_HISTOGRAM), but an arbitrary label preceding any
# statement. The char class excludes "." on purpose: it's what terminates
# the label, and Natural identifiers with a qualifying dot (VIEW.FIELD, e.g.
# "CERT-VIEW.HEAT-NO") never have whitespace right after that dot, whereas a
# label always does -- `\.\s+` is what tells the two apart.
RE_GENERIC_LABEL = re.compile(r"^\s*(?P<label>[A-Z][A-Z0-9#@$&\-_]*)\.\s+(?P<rest>.+)$", re.I)


def strip_generic_label(stmt: str, masked: str) -> tuple[str, str, str] | None:
    """Strip a leading "LABEL. " prefix, returning (label, stripped_stmt, stripped_masked).

    Matches against `masked` (consistent with every other matcher, and so a
    quoted literal at the start of a statement is never mistaken for a
    label), then slices the same offset out of both `stmt` and `masked` --
    masking preserves length, so the two stay aligned and `orig()` keeps
    working on whatever calls this returns. Returns None if the line has no
    such prefix. Callers must only use this as a fallback after the normal
    matcher cascade has already had its unstripped chance -- see extract()'s
    comment at the call site for why.
    """
    m = RE_GENERIC_LABEL.match(masked)
    if not m:
        return None
    label_end = m.start("rest")
    return m.group("label").upper(), stmt[label_end:], masked[label_end:]


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

# SET CONTROL sends terminal/printer control codes (page eject, column
# ruler, etc.) -- presentation, not a business decision, so it's recognised
# the same way as RESET/IGNORE above: no rule_candidate, just enough to stop
# it showing up as an unparsed_line gap.
RE_SET_CONTROL = re.compile(r"^\s*SET\s+CONTROL\b", re.I)

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

# Natural map (.nsm) source body, after DEFINE DATA/END-DEFINE: a level, a T
# (constant/text) or F (field) tag, the content, optional parenthesised
# attributes (edit mask, colour/intensity, etc.), and a row/column position.
# Documented Natural map-source convention; not verified against a real
# client export -- see the map_body_unverified gap this raises. Restricted
# to object_type='map' members so a guess this specific never fires against
# an ordinary program's statements.
RE_MAP_BODY = re.compile(
    r"^\s*(?P<level>\d+)\s+(?P<kind>[TF])\s+"
    # A quoted literal is entirely NULs by the time it reaches `masked` --
    # mask_literals substitutes the quote characters too, not just the
    # text between them -- so match the NUL run here and recover the
    # original text with orig() rather than looking for a literal quote.
    r"(?P<content>\x00+|\*[A-Z0-9\-]+|[A-Z0-9#@$&\-_.]+)"
    r"\s*(?:\((?P<opts>[^)]*)\))?\s*(?P<pos>\d{1,3}/\d{1,3})?\s*$", re.I)

RE_COMPUTE = re.compile(r"^\s*(?:COMPUTE|ADD|SUBTRACT|MULTIPLY|DIVIDE|MOVE|EXAMINE|COMPRESS|SEPARATE|ASSIGN)\b", re.I)
RE_MSG_NUM = re.compile(r"\bMESSAGE\s+NUMBER\s+(?P<num>[0-9#A-Z\-]+)", re.I)

# The ASSIGN keyword is optional in Natural -- `#FIELD := value` is a
# complete, valid statement on its own, and real source uses the short form
# far more often than the explicit `ASSIGN #FIELD := value`. RE_COMPUTE only
# ever anchored on the keyword, so every bare short-form assignment fell
# through as an unparsed_line gap. Anchored on a leading identifier (so it
# can't accidentally swallow a mid-statement ":=" that already matched
# something upstream) followed by ":=" -- the one operator Natural never
# uses for anything but assignment.
RE_BARE_ASSIGN = re.compile(r"^\s*[A-Z#@$][A-Z0-9#@$&\-_.]*(?:\([^()]*\))?\s*:=\s*.+$", re.I)

# The classic loop-counter idiom: ADD 1 TO x / SUBTRACT 1 FROM x. Excluded
# from arithmetic rule capture even though "1" is technically a literal,
# because it is almost never a business decision.
RE_LOOP_COUNTER = re.compile(r"^\s*(?:ADD\s+1\s+TO|SUBTRACT\s+1\s+FROM)\b", re.I)

# Reporting-mode tells
RE_REPORTING = re.compile(r"^\s*(LOOP\b|DO\b\s*$|DOEND\b)", re.I)

# LOOP specifically, as a block opener for the indentation-based depth
# inference below (issue #5) -- DO/DOEND (RE_REPORTING's other two tells)
# are left untouched as pure mode signals; scope here is deliberately just
# LOOP, the construct the issue asks for.
RE_LOOP_OPEN = re.compile(r"^\s*LOOP\b", re.I)

_STRUCTURED_TELLS = re.compile(r"\bEND-(IF|DEFINE|DECIDE|REPEAT|FOR|SUBROUTINE|WORK|ALL)\b", re.I)

IDENT = re.compile(r"[A-Z][A-Z0-9#@$&\-_.]*", re.I)
NUMLIT = re.compile(r"(?<![A-Z0-9#\-])\d+(?:\.\d+)?")

# Natural's two logical-field literals, `TRUE`/`FALSE` -- bare keywords, not
# quoted strings (`mask_literals`) or numbers (`NUMLIT`), so a statement
# like `#DEBUG := TRUE` was invisible to _match_arithmetic's "does this
# assignment carry a literal value" test and to _condition_facts' own
# `literals` output for an `IF #FLAG = TRUE`-shaped condition, even though
# a boolean flag assignment is exactly the kind of fixed-value business
# decision `_match_arithmetic` exists to capture. Bounded the same way as
# every other identifier-adjacent match in this codebase (not `\b`, which
# cannot fire between two non-word characters -- see `_condition_facts`'
# sibling checks and reference/writing-rules.md for the same lesson).
BOOL_LITERAL = re.compile(r"(?<![A-Z0-9#@$&\-_])(TRUE|FALSE)(?![A-Z0-9#@$&\-_])", re.I)

CONTINUATION_TAIL = re.compile(r"(\b(AND|OR|NOT|THRU|THROUGH|TO|WITH|BY)\s*$)|([=<>,+\-*/]\s*$)", re.I)

# Real Natural wraps long clauses at whatever column runs out, not only after a
# connective -- a `FIND ... WITH NAME = 'SMITH'` condition commonly breaks
# *before* the `AND` on the next line, so the first physical line ends in a
# closing quote or identifier with no trailing token at all. CONTINUATION_TAIL
# alone misses that case and truncates the statement mid-condition -- a silent
# partial rule, since the citation still looks complete. None of these leading
# tokens are ever the first word of a genuine new Natural statement, so
# checking the *next* line's lead is a safe second signal with no added
# false-continuation risk. INTO covers the equally common
# `COMPRESS ... \n INTO target` and `SEPARATE ... \n INTO target` wrap --
# INTO is never the first word of a genuine new statement either.
CONTINUATION_LEAD = re.compile(r"^\s*(AND|OR|NOT|THRU|THROUGH|TO|WITH|BY|INTO)\b", re.I)

# Natural report-writer column-position tokens ("5T" = tab to column 5, "2X"
# = skip 2 spaces) commonly appear on their own continuation line within a
# multi-line WRITE/DISPLAY/PRINT/INPUT/REINPUT operand list -- CONTINUATION_LEAD
# doesn't cover them (they aren't a keyword), so without this such a line falls
# through as its own unparsed_line gap instead of folding into the statement
# it's actually part of. Scoped to WRITE/DISPLAY/PRINT/INPUT/REINPUT only (see
# the fold loop's verb check) -- a bare "5T" on its own line in any other
# context is much more likely a genuine unrecognised construct than a
# continuation, and this is specifically a report-writer/screen-layout
# convention. The optional leading "/" or "//" is Natural's own
# next-line/skip-a-line marker, routinely paired with a column-position token
# on the same continuation line (e.g. "// 1X #MESSAGE").
CONTINUATION_LEAD_COLSPEC = re.compile(r"^\s*/{0,2}\s*\d+[TX]\b", re.I)

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
    bools = [b.upper() for b in BOOL_LITERAL.findall(masked)]
    lits = [l.strip("'\"") for l in literals] + nums + bools
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


def _reporting_code_lines(lines):
    """[(line_no, code, indent_col)] for a member's non-comment, non-blank lines."""
    out = []
    for line_no, _, raw in lines:
        code, is_comment = strip_comment(raw)
        if is_comment or not code.strip():
            continue
        out.append((line_no, code, len(raw) - len(raw.lstrip())))
    return out


def reporting_loop_plan(lines) -> dict[int, int] | None:
    """Map each LOOP's line_no to its own indentation column, for reporting-mode
    depth inference (issue #5) -- or None if inference isn't safe to trust.

    Reporting mode has no END-LOOP keyword; scope is implicit and normally
    unrecoverable from a line scan. Indentation is a usable proxy *only*
    when it's unambiguous: every LOOP's body must be more indented than the
    LOOP line itself, checked against the very next code line. The moment
    any LOOP in the member fails that check -- inconsistent indentation, or
    a LOOP with nothing after it -- inference is abandoned for the whole
    member and the caller falls back to flagging the existing high-severity
    gap rather than emitting a confident-looking wrong depth for any of it.
    This is deliberately member-wide, not per-LOOP: a member with one
    ambiguous LOOP casts doubt on whether its indentation convention can be
    trusted at all.
    """
    code_lines = _reporting_code_lines(lines)
    loop_indents: dict[int, int] = {}
    for i, (line_no, code, indent) in enumerate(code_lines):
        if not RE_LOOP_OPEN.match(code):
            continue
        if i + 1 >= len(code_lines) or code_lines[i + 1][2] <= indent:
            return None
        loop_indents[line_no] = indent
    return loop_indents


def _scan_routines(lines: list[tuple[int, str | None, str]]) -> list[dict]:
    """`DEFINE SUBROUTINE name` / `END-SUBROUTINE` boundaries, as a
    standalone pre-scan independent of the main per-statement dispatch loop
    below -- deliberately: that loop's continuation-folding and dual
    masked/masked2 handling is already intricate, and routine boundaries
    only need two keywords recognised, never nested (Natural subroutines
    don't nest), so a dedicated single pass is both simpler and safer than
    threading "current open routine" state through the existing dispatch.

    Returns dicts with name/kind/start_line/end_line (end_line is None when
    no matching END-SUBROUTINE was found before EOF or the next DEFINE
    SUBROUTINE -- recorded as unresolved via a gap by the caller, not
    guessed here)."""
    routines: list[dict] = []
    open_routine: dict | None = None
    for line_no, _, raw in lines:
        code, _ = strip_comment(raw)
        if not code.strip():
            continue
        if (m := RE_DEFINE_SUB.match(code)):
            if open_routine is not None:
                routines.append(open_routine)
            open_routine = {
                "name": m.group("name").upper(), "kind": "natural_subroutine",
                "start_line": line_no, "end_line": None,
            }
        elif open_routine is not None and RE_END_ANY.match(code) and RE_END_ANY.match(code).group(1).upper() == "SUBROUTINE":
            open_routine["end_line"] = line_no
            routines.append(open_routine)
            open_routine = None
    if open_routine is not None:
        routines.append(open_routine)
    return routines


def extract(conn, member_id: int, lines: list[tuple[int, str | None, str]], member_name: str = "?") -> dict:
    """Populate fact tables for one Natural member."""
    mode = detect_mode(lines)
    conn.execute("UPDATE member SET mode=? WHERE id=?", (mode, member_id))

    row = conn.execute("SELECT object_type FROM member WHERE id=?", (member_id,)).fetchone()
    is_map = (row["object_type"] if row else None) == "map"
    if is_map:
        add_gap(
            conn, "map_body_unverified",
            "Map body field/text recognition is best-effort against documented Natural "
            "map source conventions (T/F tagged lines with row/column position), not "
            "verified against a real client export -- no public or shipped sample was "
            "available to confirm the exact layout. Treat extracted field names, prompt "
            "text and edit masks as needing SME/screen confirmation before relying on them.",
            member_id=member_id, severity="medium",
        )
    loop_plan: dict[int, int] | None = None
    if mode == "reporting":
        loop_plan = reporting_loop_plan(lines)
        if loop_plan is not None:
            add_gap(
                conn, "reporting_mode",
                "Member is written in Natural reporting mode. LOOP nesting was inferred "
                "from indentation (recorded as rule_candidate rows with confidence="
                "'inferred') because every LOOP's body was consistently more indented "
                "than the LOOP line itself; confirm against a real listing before "
                "treating it as verified structure.",
                member_id=member_id, severity="medium",
            )
        else:
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
    for r in _scan_routines(lines):
        if r["end_line"] is None:
            add_gap(
                conn, "unparsed_line",
                f"DEFINE SUBROUTINE {r['name']} opened at line {r['start_line']} has no "
                "matching END-SUBROUTINE; its extent is unknown, so facts inside it "
                "can't be grouped under this routine.",
                member_id=member_id, line_no=r["start_line"], severity="medium",
            )
        insert(conn, "routine", member_id=member_id, name=r["name"], kind=r["kind"],
               start_line=r["start_line"], end_line=r["end_line"])
    stats = {"lines": len(lines), "code_lines": 0, "comment_lines": 0, "unparsed": 0}

    in_define = False
    scope = None
    depth = 0
    open_blocks: list[tuple[str, int]] = []
    # IF/ELSE branch-extent tracking (see _match_rules): the rule_candidate
    # id of the IF that opened each currently-open block, and of its ELSE
    # (if one has fired), keyed by the IF's own line_no -- so, once the
    # matching END is found, both rows' end_line can be set to where
    # their branch actually ends. Without this, a generated document has
    # no structural cue that a later GET/DELETE/etc. belongs to a
    # particular IF's ELSE branch rather than being unrelated main-line
    # code, and narration silently describes only the branch that read as
    # interesting (usually the error/validation one).
    if_rule_ids: dict[int, int] = {}
    else_rule_ids: dict[int, int] = {}
    # Reporting-mode LOOP nesting (issue #5) -- (line_no, indent_col) per
    # open LOOP, decoupled from open_blocks/the END-* keyword closing
    # mechanism above, since LOOP closes on dedent, not on a keyword. Stays
    # empty (a no-op) whenever loop_plan is None -- ambiguous indentation
    # or a non-reporting member -- so nothing below this point changes
    # behaviour for structured-mode members at all.
    loop_stack: list[tuple[int, int]] = []

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
            is_colspec_continuation = (
                (RE_WRITE.match(stmt) or RE_INPUT.match(stmt) or RE_REINPUT.match(stmt))
                and CONTINUATION_LEAD_COLSPEC.match(nxt_code)
            )
            if not (CONTINUATION_TAIL.search(stmt.rstrip()) or CONTINUATION_LEAD.match(nxt_code)
                    or is_colspec_continuation):
                break
            look += 1
            stmt = stmt.rstrip() + " " + nxt_code.strip()
        masked, _ = mask_literals(stmt)

        matched = False

        # ------------------------------------------- reporting-mode LOOP nesting
        # Only active when loop_plan is not None (mode == "reporting" and every
        # LOOP's indentation was unambiguous -- see reporting_loop_plan). Must
        # run before every other matcher below: closing a LOOP on dedent has to
        # happen before this line's own statement is classified, and opening
        # one has to bump `depth` before any IF/DECIDE/etc rule_candidate
        # inside its body is recorded, or their depth would be wrong by one.
        if loop_plan is not None:
            cur_indent = len(raw) - len(raw.lstrip())
            while loop_stack and cur_indent <= loop_stack[-1][1]:
                loop_stack.pop()
                depth = max(depth - 1, 0)
            if line_no in loop_plan and RE_LOOP_OPEN.match(masked):
                insert(conn, "rule_candidate", member_id=member_id, line_no=line_no,
                       construct="LOOP", condition=None, depth=depth,
                       fields_used=None, literals=None, raw=stmt.strip()[:500],
                       confidence="inferred")
                loop_stack.append((line_no, cur_indent))
                depth += 1
                matched = True

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

        # ---------------------------------------------------------- map body
        if not matched and is_map:
            matched = _match_map_body(conn, member_id, line_no, stmt, masked)

        # ------------------------------------------------------ rule candidates
        if not matched:
            matched, depth, open_blocks = _match_rules(
                conn, member_id, line_no, stmt, masked, depth, open_blocks,
                if_rule_ids, else_rule_ids)

        # ------------------------------------------------ arithmetic candidates
        if not matched:
            matched = _match_arithmetic(conn, member_id, line_no, stmt, masked, depth)

        if not matched:
            matched = RE_COMPUTE.match(masked) or RE_END_ANY.match(masked) \
                or RE_RESET.match(masked) or RE_IGNORE.match(masked) \
                or RE_SET_CONTROL.match(masked) or RE_BARE_ASSIGN.match(masked)

        # ---------------------------------------------- labelled statements
        # Last resort: a generic statement label ("SETA. SETTIME") defeats
        # every verb pattern above, since they all anchor on ^\s* and none
        # (other than RE_READ/RE_FIND/RE_HISTOGRAM's own inline R#/F#/H#
        # groups, a different, narrower thing -- see label_to_view) expect
        # a label prefix. Only tried after every matcher above has already
        # had its unstripped chance, so it can never pre-empt one of those
        # more specific matches -- most importantly the R#/F#/H# loop-label
        # capture in _match_data_access, which this must not disturb.
        # Whatever inserted row ends up carrying a `raw`/`condition` excerpt
        # for the stripped-and-rematched statement won't include the label
        # text in that excerpt, but the label is not lost: source_line
        # (inserted above, unconditionally) still holds the full original
        # line verbatim, which is what the citation actually anchors to.
        if not matched and (stripped := strip_generic_label(stmt, masked)):
            _label, stmt2, masked2 = stripped
            matched = (
                _match_data_access(conn, member_id, line_no, stmt2, masked2, view_to_ddm, label_to_view)
                or _match_calls(conn, member_id, line_no, stmt2, masked2, internal_subroutines, member_id)
                or _match_interaction(conn, member_id, line_no, stmt2, masked2)
                or (is_map and _match_map_body(conn, member_id, line_no, stmt2, masked2))
            )
            if not matched:
                matched, depth, open_blocks = _match_rules(
                    conn, member_id, line_no, stmt2, masked2, depth, open_blocks,
                    if_rule_ids, else_rule_ids)
            if not matched:
                matched = _match_arithmetic(conn, member_id, line_no, stmt2, masked2, depth)
            if not matched:
                matched = RE_COMPUTE.match(masked2) or RE_END_ANY.match(masked2) \
                    or RE_RESET.match(masked2) or RE_IGNORE.match(masked2) \
                    or RE_SET_CONTROL.match(masked2) or RE_BARE_ASSIGN.match(masked2)

        if not matched:
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
        # Reuses _clean_target (the same quoted-literal-vs-#VAR test
        # _match_calls uses for CALLNAT/PERFORM/FETCH/etc targets) rather
        # than the old strip-and-upper: a `USING MAP #MAP-NAME` (the map
        # held in a field, not written literally) is exactly as
        # indeterminate from source as a dynamic CALLNAT target, and was
        # previously indistinguishable here from `USING MAP MAP01`.
        map_name, map_dynamic = _clean_target(mp.group("map")) if mp else (None, False)
        insert(conn, "interaction", member_id=member_id, line_no=line_no, kind="INPUT",
               target=(map_name.upper() if mp else None),
               dynamic=1 if (mp and map_dynamic) else 0,
               fields=rest.strip()[:300] or None)
        if mp:
            insert(conn, "call_edge", caller_id=member_id,
                   callee_name=map_name.upper(),
                   call_kind="INCLUDE", dynamic=1 if map_dynamic else 0,
                   line_no=line_no, args="USING MAP")
            if map_dynamic:
                add_gap(conn, "dynamic_target",
                        f"INPUT USING MAP target is a variable ({map_name}); the map "
                        f"actually displayed cannot be determined from source alone.",
                        member_id=member_id, line_no=line_no, severity="high",
                        raw=stmt.strip()[:300])
        return True
    if (m := RE_WRITE.match(masked)):
        insert(conn, "interaction", member_id=member_id, line_no=line_no,
               kind=m.group(1).upper(), target=None,
               fields=(m.group("rest") or "").strip()[:300] or None)
        return True
    return False


def _match_map_body(conn, member_id, line_no, stmt, masked) -> bool:
    """Only called for object_type='map' members. A field (F) line records
    which screen field lives here and its attributes (edit mask, etc); a
    text (T) line records the literal prompt/label shown to the user --
    both are genuinely user-visible behaviour, not decoration, so both are
    worth citing even though this format is unverified (see the
    map_body_unverified gap raised once per map member)."""
    m = RE_MAP_BODY.match(masked)
    if not m:
        return False
    kind = m.group("kind").upper()
    content = (orig(stmt, m, "content") or "").strip("'\" ")
    opts = orig(stmt, m, "opts")
    if kind == "F":
        insert(conn, "interaction", member_id=member_id, line_no=line_no, kind="MAP_FIELD",
               target=content.upper() or None, fields=(opts or "").strip()[:300] or None)
    else:
        insert(conn, "interaction", member_id=member_id, line_no=line_no, kind="MAP_TEXT",
               target=None, fields=content[:300] or None)
    return True


def _match_arithmetic(conn, member_id, line_no, stmt, masked, depth) -> bool:
    """Capture COMPUTE/MOVE/ADD/SUBTRACT/MULTIPLY/DIVIDE/EXAMINE/ASSIGN (with
    or without the optional ASSIGN keyword -- `#FIELD := value` is a complete
    statement on its own, see RE_BARE_ASSIGN) as a rule candidate when a
    literal is involved -- assigning a fixed value to a field (a status
    code, a threshold, a return code, a boolean flag) is a business
    decision. Pure
    variable-to-variable movement or accumulation (no literal operand) and
    the ADD/SUBTRACT-1 loop-counter idiom are left alone; capturing every
    arithmetic statement would bury the ones that actually carry a decision
    under running totals and index increments.
    """
    is_keyword_form = RE_COMPUTE.match(masked)
    is_bare_assign = not is_keyword_form and RE_BARE_ASSIGN.match(masked)
    if not (is_keyword_form or is_bare_assign) or RE_LOOP_COUNTER.match(masked):
        return False
    _, str_literals = mask_literals(stmt)
    if not str_literals and not NUMLIT.search(masked) and not BOOL_LITERAL.search(masked):
        return False
    verb = masked.split()[0].upper() if is_keyword_form else "ASSIGN"
    fields, lits = _condition_facts(stmt)
    insert(conn, "rule_candidate", member_id=member_id, line_no=line_no,
           construct=verb, condition=stmt.strip()[:500] or None,
           depth=depth, fields_used=fields[:500] or None, literals=lits[:500] or None,
           raw=stmt.strip()[:500])
    return True


def _match_rules(conn, member_id, line_no, stmt, masked, depth, open_blocks,
                  if_rule_ids: dict[int, int] | None = None,
                  else_rule_ids: dict[int, int] | None = None):
    if_rule_ids = {} if if_rule_ids is None else if_rule_ids
    else_rule_ids = {} if else_rule_ids is None else else_rule_ids

    def rec(construct, cond, pair_line_no=None):
        fields, lits = _condition_facts(cond or "")
        return insert(conn, "rule_candidate", member_id=member_id, line_no=line_no,
               construct=construct, condition=(cond or "").strip()[:500] or None,
               depth=depth, fields_used=fields[:500] or None, literals=lits[:500] or None,
               raw=stmt.strip()[:500], pair_line_no=pair_line_no)

    if (m := RE_END_ANY.match(masked)):
        openers = _END_TO_OPENERS.get(m.group(1).upper())
        if openers and open_blocks and open_blocks[-1][0] in openers:
            popped_construct, opened_line = open_blocks.pop()
            if popped_construct == "IF":
                # This IF's (and, if it had one, its ELSE's) branch extent
                # is now known -- record it so a reader/narrator can see
                # exactly which later facts (a GET, a DELETE, another
                # rule) fall inside which branch, rather than having to
                # guess from indentation the brief doesn't carry.
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
            return True, max(depth - 1, 0), open_blocks
        return True, depth, open_blocks
    if RE_IF_NO.match(masked):
        rec("IF NO RECORDS FOUND", "no records found for preceding database loop")
        open_blocks.append(("IF NO RECORDS FOUND", line_no))
        return True, depth + 1, open_blocks
    if (m := RE_IF.match(masked)):
        if_id = rec("IF", orig(stmt, m, "cond"))
        if_rule_ids[line_no] = if_id
        open_blocks.append(("IF", line_no))
        return True, depth + 1, open_blocks
    if RE_ELSE.match(masked):
        pair_line = open_blocks[-1][1] if open_blocks and open_blocks[-1][0] == "IF" else None
        else_id = rec("ELSE", None, pair_line_no=pair_line)
        if pair_line is not None:
            else_rule_ids[pair_line] = else_id
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
