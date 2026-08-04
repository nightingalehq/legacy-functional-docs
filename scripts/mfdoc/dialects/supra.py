"""Supra (Cincom) directory parser.

Supra's classic structure is a network model: Master datasets keyed by a control
field, Related/Variable-Entry datasets holding dependent occurrences, and
linkpaths connecting them. The documentation value is almost entirely in the
linkpaths, because they encode the navigational business relationships that
application code walks.

Directory report layouts vary by release and by how the site's DBA configured
the reporting utility, so parsing here is *label-driven*: sections and fields
are matched by configurable label patterns rather than fixed column positions.
When a report does not match, the parser records the unmatched lines as gaps
rather than silently producing an empty schema — an empty schema that looks
successful is the worst possible outcome for a documentation project.
"""

from __future__ import annotations

import re

from ..db import add_gap, insert, resolve_entity, set_metric, upsert_entity, upsert_field

LABELS = {
    # Anchored: an unanchored pattern also matches the "PRIMARY DATA-SET:" and
    # "RELATED DATA-SET:" lines inside a linkpath block, which silently invents a
    # duplicate dataset of the wrong type for every relationship in the schema.
    "dataset": r"^\s*(?:DATA\s*-?\s*SET|DATASET|FILE)\s*(?:NAME)?\s*[:=]\s*(?P<v>[A-Z0-9\-_#$]{1,32})",
    "dataset_type": r"(?:DATA\s*-?\s*SET\s+)?TYPE\s*[:=]\s*(?P<v>MASTER|RELATED|VARIABLE|VED|PRIMARY|SECONDARY)",
    "control_key": r"(?:CONTROL\s+(?:KEY|FIELD)|PRIMARY\s+KEY|KEY\s+ELEMENT)\s*[:=]\s*(?P<v>[A-Z0-9\-_#$,\s]{1,80})",
    "element": r"(?:ELEMENT|FIELD)\s*(?:NAME)?\s*[:=]\s*(?P<v>[A-Z0-9\-_#$]{1,32})",
    "linkpath": r"(?:LINKPATH|LINK\s*PATH|LINK)\s*(?:NAME)?\s*[:=]\s*(?P<v>[A-Z0-9\-_#$]{1,32})",
    "link_from": r"(?:PRIMARY|OWNER|FROM|MASTER)\s+DATA\s*-?\s*SET\s*[:=]\s*(?P<v>[A-Z0-9\-_#$]{1,32})",
    "link_to": r"(?:RELATED|MEMBER|TO|DEPENDENT)\s+DATA\s*-?\s*SET\s*[:=]\s*(?P<v>[A-Z0-9\-_#$]{1,32})",
    "schema": r"(?:SCHEMA|DATA\s*BASE|DATABASE)\s*(?:NAME)?\s*[:=]\s*(?P<v>[A-Z0-9\-_#$]{1,32})",
}

# Tabular element rows: NAME  TYPE  LEN  DEC  OCCURS
RE_ELEMENT_ROW = re.compile(
    r"^\s*(?P<name>[A-Z][A-Z0-9\-_#$]{1,31})\s+"
    r"(?P<type>CHAR|CHARACTER|NUM|NUMERIC|PACKED|PACK|BINARY|BIN|ZONED|DISPLAY|DATE|TIME|[ACNPBZDX])\s+"
    r"(?P<len>\d{1,5})\s*(?P<dec>\d{0,2})\s*(?P<occ>\d{0,4})\s*(?P<rest>.*)$", re.I)

TYPE_MAP = {
    "CHAR": "A", "CHARACTER": "A", "X": "A", "A": "A", "DISPLAY": "A",
    "NUM": "N", "NUMERIC": "N", "N": "N", "ZONED": "N", "Z": "N",
    "PACKED": "P", "PACK": "P", "P": "P",
    "BINARY": "B", "BIN": "B", "B": "B",
    "DATE": "D", "D": "D", "TIME": "T", "C": "A",
}

DATASET_KIND = {
    "MASTER": "supra_master", "PRIMARY": "supra_master",
    "RELATED": "supra_ved", "VARIABLE": "supra_ved", "VED": "supra_ved",
    "SECONDARY": "supra_ved",
}


def _entity(conn, name: str, default_kind: str, **kw) -> int:
    return resolve_entity(conn, name, "supra", default_kind, **kw)


def _find(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.I)
    return m.group("v").strip().upper() if m else None


def extract(conn, member_id: int, lines, member_name: str = "?") -> dict:
    current_ds: tuple[int, str] | None = None
    pending_link: dict = {}
    counts = {"datasets": 0, "elements": 0, "linkpaths": 0, "unmatched": 0}
    schema_name = None

    for line_no, seq, raw in lines:
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=seq,
               text=raw, is_comment=0)
        if not raw.strip() or set(raw.strip()) <= set("-=*+| "):
            continue
        used = False

        if (v := _find(LABELS["schema"], raw)):
            schema_name = v
            used = True

        if (v := _find(LABELS["linkpath"], raw)):
            pending_link = {"name": v, "line": line_no}
            counts["linkpaths"] += 1
            used = True
        if pending_link:
            frm = _find(LABELS["link_from"], raw)
            to = _find(LABELS["link_to"], raw)
            if frm:
                pending_link["from"] = frm
            if to:
                pending_link["to"] = to
            if pending_link.get("from") and pending_link.get("to"):
                a = _entity(conn, pending_link["from"], "supra_master")
                b = _entity(conn, pending_link["to"], "supra_ved")
                insert(conn, "entity_link", from_entity=a, to_entity=b, link_kind="linkpath",
                       link_name=pending_link["name"], via_member=member_id,
                       via_line=pending_link["line"], confidence="verified")
                pending_link = {}
                used = True

        if not used and (v := _find(LABELS["dataset"], raw)):
            kind_tok = _find(LABELS["dataset_type"], raw)
            kind = DATASET_KIND.get(kind_tok or "", "supra_master")
            eid = _entity(conn, v, kind, defined_in=member_id, defined_line=line_no,
                          physical_ref=f"schema {schema_name}" if schema_name else None)
            conn.execute(
                "UPDATE entity SET kind=?, defined_in=?, defined_line=?, physical_ref=? WHERE id=?",
                (kind, member_id, line_no,
                 f"schema {schema_name}" if schema_name else None, eid))
            current_ds = (eid, v)
            counts["datasets"] += 1
            used = True
            if not kind_tok:
                add_gap(conn, "sme_question",
                        f"Dataset {v} has no explicit type in the directory report; assumed "
                        f"MASTER. Confirm whether it is a master or a variable-entry dataset, "
                        f"since that determines how occurrences are keyed.",
                        member_id=member_id, line_no=line_no, severity="medium")

        if current_ds and (v := _find(LABELS["control_key"], raw)):
            for key in [k.strip() for k in v.split(",") if k.strip()]:
                upsert_field(conn, current_ds[0], key, is_descriptor=1,
                             descriptor_kind="primary_key", defined_line=line_no,
                             remark="control key from directory")
            used = True

        if current_ds and (m := RE_ELEMENT_ROW.match(raw)):
            t = TYPE_MAP.get(m.group("type").upper(), m.group("type").upper()[:1])
            upsert_field(conn, current_ds[0], m.group("name").upper(),
                         format=t,
                         length=m.group("len") + (("." + m.group("dec")) if m.group("dec") else ""),
                         occurrences=m.group("occ") or None, defined_line=line_no,
                         remark=(m.group("rest") or "").strip()[:120] or None)
            counts["elements"] += 1
            used = True
        elif current_ds and (v := _find(LABELS["element"], raw)):
            upsert_field(conn, current_ds[0], v, defined_line=line_no)
            counts["elements"] += 1
            used = True

        if not used:
            counts["unmatched"] += 1

    if counts["datasets"] == 0:
        add_gap(conn, "unparsed_line",
                f"No datasets recognised in Supra directory export {member_name}. The report "
                f"layout differs from the shipped label patterns — override "
                f"`dialects.supra.labels` in project config before proceeding.",
                member_id=member_id, severity="high")
    if counts["linkpaths"] == 0 and counts["datasets"] > 1:
        add_gap(conn, "sme_question",
                f"{counts['datasets']} datasets found in {member_name} but no linkpaths. Either "
                f"the export omits the linkpath section or relationships are implemented in "
                f"application code only — confirm which, because it changes how the data model "
                f"should be documented.",
                member_id=member_id, severity="high")
    for k, v in counts.items():
        set_metric(conn, member_name, f"supra.{k}", v)
    return counts
