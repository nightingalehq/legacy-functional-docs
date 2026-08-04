"""Adabas FDT (ADAREP/ADACMP) and Natural DDM parsers.

The DDM is the *logical* contract Natural code is written against; the FDT is the
*physical* truth. Documenting only one of them is the classic mistake: DDMs can
omit fields, rename them, or exist in several variants over the same file. Both
are parsed and linked with an `implements` edge so the docs can show where the
logical and physical views disagree — which is usually where the interesting
undocumented history sits.
"""

from __future__ import annotations

import re

from ..db import add_gap, insert, upsert_entity

# ------------------------------------------------------------------------ FDT

RE_FDT_HEADER = re.compile(r"FIELD\s+DEFINITION\s+TABLE", re.I)
RE_FDT_FILE = re.compile(r"\bFILE\s*(?:NUMBER|NUM|NO\.?)?\s*[:=]?\s*(?P<fnr>\d{1,5})\b", re.I)
RE_FDT_DBID = re.compile(r"\b(?:DBID|DATABASE|DB)\s*(?:NUMBER|NO\.?|ID)?\s*[:=]?\s*(?P<dbid>\d{1,5})\b", re.I)
RE_FDT_NAME = re.compile(r"\bFILE\s*NAME\s*[:=]\s*(?P<name>[A-Z0-9\-_]{2,32})", re.I)

# Pipe-delimited ADAREP layout
RE_FDT_PIPE = re.compile(
    r"^\s*(?P<level>\d+)\s*[I|]\s*(?P<short>[A-Z0-9]{1,3})\s*[I|]\s*(?P<len>\d*)\s*[I|]"
    r"\s*(?P<fmt>[A-Z]?)\s*[I|]\s*(?P<opts>[^I|]*)", re.I)
# Whitespace layout
RE_FDT_WS = re.compile(
    r"^\s*(?P<level>[1-7])\s+(?P<short>[A-Z][A-Z0-9])\s+(?P<len>\d*)\s*(?P<fmt>[ABPUFGIWL])?\s*(?P<opts>[A-Z,;\s]*)$",
    re.I)
# Descriptor definitions: SA = AA(1-4),AB(1-2)  /  SUPERDE SA: AA(1,4),AB(1,2)
RE_DESC_DEF = re.compile(
    r"^\s*(?:SUPERDE|SUBDE|HYPERDE|PHONDE|COLLDE)?\s*(?P<name>[A-Z][A-Z0-9])\s*[:=]\s*(?P<parts>[A-Z0-9(),\-\s]+)$",
    re.I)
RE_DESC_SECTION = re.compile(r"(SUPER|SUB|HYPER|PHONETIC|COLLATION)\s*DESCRIPTOR", re.I)

OPTION_TOKENS = {"DE", "NU", "FI", "MU", "PE", "UQ", "NC", "NN", "NB", "NV", "LA", "LB", "HF", "XI"}


def _split_opts(raw: str) -> tuple[list[str], bool, str | None]:
    toks = [t.strip().upper() for t in re.split(r"[,\s;]+", raw or "") if t.strip()]
    toks = [t for t in toks if t in OPTION_TOKENS]
    is_desc = "DE" in toks or "UQ" in toks
    kind = "UQ" if "UQ" in toks else ("DE" if "DE" in toks else None)
    return toks, is_desc, kind


def extract_fdt(conn, member_id, lines, member_name="?") -> dict:
    fnr = dbid = file_name = None
    header = "\n".join(t for _, _, t in lines[:40])
    if (m := RE_FDT_NAME.search(header)):
        file_name = m.group("name").strip().upper()
    if (m := RE_FDT_FILE.search(header)):
        fnr = m.group("fnr")
    if (m := RE_FDT_DBID.search(header)):
        dbid = m.group("dbid")

    entity_name = file_name or (f"FILE-{fnr}" if fnr else member_name.upper())
    eid = upsert_entity(conn, entity_name, "adabas_file",
                        physical_ref=f"DBID {dbid or '?'} FNR {fnr or '?'}",
                        dbid=dbid, fnr=fnr, defined_in=member_id, defined_line=1)

    in_desc_section = False
    count = 0
    for line_no, seq, raw in lines:
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=seq, text=raw,
               is_comment=0)
        if not raw.strip() or set(raw.strip()) <= set("-=+| "):
            continue
        if RE_DESC_SECTION.search(raw):
            in_desc_section = True
            continue
        if RE_FDT_HEADER.search(raw):
            in_desc_section = False
            continue

        m = RE_FDT_PIPE.match(raw) or RE_FDT_WS.match(raw)
        if m and not in_desc_section:
            opts, is_desc, kind = _split_opts(m.group("opts"))
            occ = "MU" if "MU" in opts else ("PE" if "PE" in opts else None)
            insert(conn, "entity_field", entity_id=eid, level=int(m.group("level")),
                   name=m.group("short").upper(), short_name=m.group("short").upper(),
                   format=(m.group("fmt") or "").upper() or None,
                   length=m.group("len") or None, occurrences=occ,
                   is_descriptor=1 if is_desc else 0, descriptor_kind=kind,
                   options=",".join(opts) or None, defined_line=line_no)
            count += 1
            continue

        if in_desc_section and (d := RE_DESC_DEF.match(raw)):
            insert(conn, "entity_field", entity_id=eid, name=d.group("name").upper(),
                   short_name=d.group("name").upper(), is_descriptor=1,
                   descriptor_kind="SUPER", parent_fields=d.group("parts").strip(),
                   defined_line=line_no)
            count += 1

    if count == 0:
        add_gap(conn, "unparsed_line",
                f"No field rows recognised in FDT listing {member_name}. The report layout "
                f"likely differs from the shipped patterns and needs a config override.",
                member_id=member_id, severity="high")
    return {"fields": count, "entity": entity_name}


# ------------------------------------------------------------------------ DDM

RE_DDM_NAME = re.compile(r"\bDDM\s*(?:NAME)?\s*[:.\s]+\s*(?P<name>[A-Z0-9\-_&$#]{2,32})", re.I)
RE_DDM_DBFILE = re.compile(r"\bDB\s*[:=]?\s*(?P<dbid>\d+)\s+FILE\s*[:=]?\s*(?P<fnr>\d+)", re.I)
RE_DDM_SEQ = re.compile(r"DEFAULT\s+SEQUENCE\s*[:.]?\s*(?P<seq>[A-Z0-9\-_]*)", re.I)
# T L DB Name  F Leng S D Remark
RE_DDM_FIELD = re.compile(
    r"^(?P<t>[GMPC\* ]?)\s*(?P<level>[1-7])\s+(?P<short>[A-Z0-9]{1,3})\s+"
    r"(?P<name>[A-Z0-9\-_/#@$&.]{1,64})\s*"
    r"(?:(?P<fmt>[ABPNUFGILDT])\s+(?P<len>[\d.,]+))?"
    r"(?P<tail>.*)$", re.I)
RE_DDM_SUPER = re.compile(r"^\s*(?:S|SUPERDESCRIPTOR)\s+(?P<name>[A-Z0-9\-_]+)\s*[:=]\s*(?P<parts>.+)$", re.I)


def extract_ddm(conn, member_id, lines, member_name="?") -> dict:
    header = "\n".join(t for _, _, t in lines[:25])
    name = None
    if (m := RE_DDM_NAME.search(header)):
        name = m.group("name").strip().upper()
    name = name or member_name.upper()
    dbid = fnr = default_seq = None
    if (m := RE_DDM_DBFILE.search(header)):
        dbid, fnr = m.group("dbid"), m.group("fnr")
    if (m := RE_DDM_SEQ.search(header)):
        default_seq = (m.group("seq") or "").strip() or None

    eid = upsert_entity(conn, name, "ddm", dbid=dbid, fnr=fnr,
                        physical_ref=f"DBID {dbid or '?'} FNR {fnr or '?'}",
                        defined_in=member_id, defined_line=1,
                        notes=f"default sequence: {default_seq}" if default_seq else None)

    # Link the DDM to the physical Adabas file when we can identify it.
    if fnr:
        phys = upsert_entity(conn, f"FILE-{fnr}", "adabas_file", dbid=dbid, fnr=fnr)
        insert(conn, "entity_link", from_entity=eid, to_entity=phys,
               link_kind="implements", link_name=f"DDM {name} over DBID {dbid} FNR {fnr}",
               via_member=member_id, via_line=1, confidence="verified")

    count = 0
    in_fields = False
    for line_no, seq, raw in lines:
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=seq, text=raw,
               is_comment=0)
        # The column-header separator is a full line of dashes/equals with
        # spaces between them (e.g. "- - -- ---- -- - ----"), not a single
        # unbroken run, so match on line composition rather than a run length.
        if raw.strip() and re.fullmatch(r"[-=\s]+", raw.strip()):
            in_fields = True
            continue
        if not raw.strip():
            continue
        if not in_fields:
            continue
        if (s := RE_DDM_SUPER.match(raw)):
            insert(conn, "entity_field", entity_id=eid, name=s.group("name").upper(),
                   is_descriptor=1, descriptor_kind="SUPER",
                   parent_fields=s.group("parts").strip(), defined_line=line_no)
            count += 1
            continue
        m = RE_DDM_FIELD.match(raw)
        if not m:
            continue
        t = (m.group("t") or "").strip().upper()
        tail = (m.group("tail") or "")
        # Suppression / descriptor columns sit in the tail for most layouts.
        supp = "NU" if re.search(r"\bN\b", tail[:6]) else ("FI" if re.search(r"\bF\b", tail[:6]) else None)
        is_desc = bool(re.search(r"\bD\b", tail[:10]))
        occ = {"M": "MU", "P": "PE"}.get(t)
        insert(conn, "entity_field", entity_id=eid, level=int(m.group("level")),
               name=m.group("name").upper(), short_name=m.group("short").upper(),
               format=(m.group("fmt") or "").upper() or None, length=m.group("len"),
               occurrences=occ, is_descriptor=1 if is_desc else 0,
               descriptor_kind="DE" if is_desc else ("group" if t == "G" else None),
               options=supp, defined_line=line_no, remark=tail.strip()[:120] or None)
        count += 1
        in_fields = True

    if count == 0:
        add_gap(conn, "unparsed_line",
                f"No field rows recognised in DDM listing {member_name}; check the listing "
                f"layout against the shipped pattern.",
                member_id=member_id, severity="high")
    return {"fields": count, "entity": name}
