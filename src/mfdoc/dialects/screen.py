"""Mantis screen/map definition report parser.

A screen painter export: one `NAME: ... DESCRIPTION: ... PASSWORD: ...`
header, a field table (name, type, row/col position, length, occurrence
counts, display attributes), and — usually — an ASCII picture of the laid-
out screen. The field table is the only part with documentation value: it
is the complete inventory of what a program's `CONVERSE`/`SHOW` of this
screen can read or write, which is what makes it possible to say a field
was *never* referenced anywhere in the program that owns it, not just that
the program's own source never happened to mention it (see
`graph.unused_entity_fields`).

Anchored on the field's TYPE keyword rather than fixed column positions,
because a screen's field-name column is fixed-width in the export but a
quoted `HEADING` literal longer than that width gets truncated mid-string,
sometimes losing its closing quote entirely — a fixed-width slice would
misread the columns that follow on every truncated row, where anchoring
on TYPE does not. See `tests/test_screen_dialect.py` for synthetic cases
exercising this (there is no real export fixture under `examples/inputs`
for this dialect, deliberately -- see `CLAUDE.md`'s "Never commit
client-specific content").
"""

from __future__ import annotations

import re

from ..db import add_gap, insert, set_metric, upsert_entity, upsert_field

# Field types this format uses. A HEADING row is a literal screen label
# (its "name" is the quoted text itself, not a variable an application
# program could reference), never a data field -- kept in the inventory
# for a complete picture of the screen, but excluded from "unused field"
# analysis by format, not by a separate flag (see graph.py).
FIELD_TYPES = ("TEXT", "SMALLTEXT", "BIGTEXT", "NUMERIC", "HEADING", "DATE", "TIME")

RE_HEADER = re.compile(
    r"^\s*NAME:\s*(?P<name>[A-Z0-9_\-#$]{1,32})\s+DESCRIPTION:\s*(?P<desc>.*?)\s*"
    r"(?:PASSWORD:\s*(?P<pw>\S+))?\s*$", re.I,
)
RE_TYPE = re.compile(r"\b(?P<type>" + "|".join(FIELD_TYPES) + r")\b")
RE_POS = re.compile(r"^\s*(?P<row>\d+)\s+(?P<col>\d+)\s+(?P<len>\d+)\s*(?P<rest>.*)$")
RE_OCC = re.compile(r"^(?P<occ>\d+)\s+\d+\b")


def extract(conn, member_id: int, lines, member_name: str = "?") -> dict:
    counts = {"fields": 0, "headings": 0, "unmatched": 0}
    eid = upsert_entity(conn, member_name, "mantis_map", defined_in=member_id, defined_line=1)
    description = None

    for line_no, seq, raw in lines:
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=seq,
               text=raw, is_comment=0)
        stripped = raw.strip()
        if not stripped:
            continue

        if (m := RE_HEADER.match(raw)):
            description = (m.group("desc") or "").strip() or None
            if description:
                conn.execute("UPDATE entity SET notes=? WHERE id=?", (description, eid))
            continue

        # The trailing ASCII picture of the laid-out screen (a ruler line of
        # `....+....1....+...`, or a row of box-drawing/placeholder
        # characters) carries no field-level fact this parser can use --
        # skipped silently rather than gapped, since (unlike a code file)
        # most of this report's lines are decorative by design, and gapping
        # each one would drown the "0 fields recognised" signal that
        # actually matters in noise.
        if set(stripped) <= set(".+#|-<>0123456789"):
            continue

        # Try every TYPE-keyword occurrence on the line, not just the
        # first: a quoted HEADING literal's own free text can itself
        # contain a word matching one of FIELD_TYPES (e.g. a caption
        # ending in "...STATUS HEADING"), which would otherwise be
        # mistaken for the real TYPE column. The real column is whichever
        # occurrence is immediately followed by a row/col/len position --
        # free text inside quotes never is.
        type_m = pos_m = None
        for candidate in RE_TYPE.finditer(raw):
            candidate_pos = RE_POS.match(raw[candidate.end():])
            if candidate_pos:
                type_m, pos_m = candidate, candidate_pos
                break
        if type_m is None:
            counts["unmatched"] += 1
            continue

        name = raw[:type_m.start()].strip()
        if name.startswith('"'):
            # A HEADING literal, sometimes missing its closing quote --
            # truncated by the export's fixed-width name column, not a
            # parsing failure (see module docstring).
            name = name.strip('"')
        if not name:
            counts["unmatched"] += 1
            continue

        field_type = type_m.group("type").upper()
        occ_m = RE_OCC.match(pos_m.group("rest") or "")
        upsert_field(
            conn, eid, name,
            format=field_type,
            length=pos_m.group("len"),
            occurrences=occ_m.group("occ") if occ_m else None,
            defined_line=line_no,
            remark=f"row {pos_m.group('row')} col {pos_m.group('col')}",
        )
        counts["headings" if field_type == "HEADING" else "fields"] += 1

    if counts["fields"] == 0:
        add_gap(
            conn, "unparsed_line",
            f"No data fields recognised in screen export {member_name}. The report layout "
            "may differ from the shipped format -- check FIELD_TYPES in dialects/screen.py "
            "against this export's own TYPE column values.",
            member_id=member_id, severity="high",
        )
    for k, v in counts.items():
        set_metric(conn, member_name, f"screen.{k}", v)
    return counts
