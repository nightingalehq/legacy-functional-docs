"""Surrounding-environment parsers: SQL DDL, COBOL copybooks, JCL, CICS CSD.

These are not the 4GL codebases themselves, but functional documentation that
omits them is unusable in practice. JCL tells you what actually runs and in what
order, which is the batch process model. CICS CSD tells you which transaction
code a user types to reach which program, which is the online process model.
Copybooks describe flat-file and non-DBMS record layouts the 4GL code reads and
writes. Skipping them is how you end up with a beautiful module catalogue that
nobody can trace to a business process.
"""

from __future__ import annotations

import re

from ..db import add_gap, insert, set_metric, upsert_entity

# --------------------------------------------------------------------- SQL DDL

RE_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?P<name>[A-Z0-9_.\"]+)\s*\((?P<body>.*?)\)\s*(?:IN\s+\S+)?\s*;", re.I | re.S)
RE_CREATE_INDEX = re.compile(r"CREATE\s+(?P<uq>UNIQUE\s+)?INDEX\s+(?P<idx>[A-Z0-9_.\"]+)\s+ON\s+(?P<tbl>[A-Z0-9_.\"]+)\s*\((?P<cols>[^)]*)\)", re.I | re.S)
RE_FK = re.compile(r"FOREIGN\s+KEY\s*\((?P<cols>[^)]*)\)\s*REFERENCES\s+(?P<ref>[A-Z0-9_.\"]+)", re.I)
RE_PK = re.compile(r"PRIMARY\s+KEY\s*\((?P<cols>[^)]*)\)", re.I)
RE_COL = re.compile(
    r"^\s*(?P<name>[A-Z0-9_\"]+)\s+(?P<type>[A-Z]+(?:\s+PRECISION)?)(?:\s*\((?P<spec>[^)]*)\))?"
    r"(?P<rest>.*)$", re.I)
DDL_NOISE = re.compile(r"^\s*(PRIMARY|FOREIGN|UNIQUE|CONSTRAINT|CHECK|KEY)\b", re.I)


def extract_sql_ddl(conn, member_id, lines, member_name="?") -> dict:
    text = "\n".join(t for _, _, t in lines)
    offsets = {}
    pos = 0
    for line_no, _, t in lines:
        offsets[pos] = line_no
        pos += len(t) + 1
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=None, text=t, is_comment=0)

    def line_of(char_pos: int) -> int:
        best = 1
        for p, ln in offsets.items():
            if p <= char_pos:
                best = ln
            else:
                break
        return best

    tables = cols = 0
    for m in RE_CREATE_TABLE.finditer(text):
        name = m.group("name").strip('"').upper()
        ln = line_of(m.start())
        eid = upsert_entity(conn, name, "sql_table", defined_in=member_id, defined_line=ln,
                            physical_ref=name)
        tables += 1
        pk_cols = set()
        if (pk := RE_PK.search(m.group("body"))):
            pk_cols = {c.strip().strip('"').upper() for c in pk.group("cols").split(",")}
        for raw_col in _split_top_level(m.group("body")):
            if DDL_NOISE.match(raw_col):
                if (fk := RE_FK.search(raw_col)):
                    ref = upsert_entity(conn, fk.group("ref").strip('"').upper(), "sql_table")
                    insert(conn, "entity_link", from_entity=eid, to_entity=ref,
                           link_kind="foreign_key", link_name=fk.group("cols").strip(),
                           via_member=member_id, via_line=ln, confidence="verified")
                continue
            if (c := RE_COL.match(raw_col.strip())):
                cname = c.group("name").strip('"').upper()
                rest = c.group("rest") or ""
                insert(conn, "entity_field", entity_id=eid, name=cname,
                       format=c.group("type").upper(), length=c.group("spec"),
                       is_descriptor=1 if cname in pk_cols else 0,
                       descriptor_kind="primary_key" if cname in pk_cols else None,
                       options="NOT NULL" if re.search(r"NOT\s+NULL", rest, re.I) else None,
                       defined_line=ln, remark=rest.strip()[:120] or None)
                cols += 1

    for m in RE_CREATE_INDEX.finditer(text):
        tbl = upsert_entity(conn, m.group("tbl").strip('"').upper(), "sql_table")
        insert(conn, "entity_field", entity_id=tbl, name=m.group("idx").strip('"').upper(),
               is_descriptor=1, descriptor_kind="UQ" if m.group("uq") else "index",
               parent_fields=m.group("cols").strip(), defined_line=line_of(m.start()))

    set_metric(conn, member_name, "sqlddl.tables", tables)
    set_metric(conn, member_name, "sqlddl.columns", cols)
    return {"tables": tables, "columns": cols}


def _split_top_level(body: str) -> list[str]:
    out, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


# ---------------------------------------------------------------- COBOL copybook

RE_COB = re.compile(
    r"^\s*(?P<level>\d\d)\s+(?P<name>[A-Z0-9\-]+)(?P<rest>.*?)\.?\s*$", re.I)
RE_PIC = re.compile(r"\b(?:PIC|PICTURE)\s+(?:IS\s+)?(?P<pic>[X9AVSZ(\)0-9,.+\-]+)", re.I)
RE_OCCURS = re.compile(r"\bOCCURS\s+(?P<n>\d+)(?:\s+TO\s+(?P<n2>\d+))?", re.I)
RE_REDEF = re.compile(r"\bREDEFINES\s+(?P<t>[A-Z0-9\-]+)", re.I)
RE_COMP = re.compile(r"\b(COMP-3|COMP-4|COMP-5|COMP-1|COMP-2|COMP|BINARY|PACKED-DECIMAL|DISPLAY)\b", re.I)


def _pic_to_format(pic: str, usage: str | None) -> tuple[str, str]:
    exp = ""
    for m in re.finditer(r"([X9AVSZ])(?:\((\d+)\))?", pic.upper()):
        exp += m.group(1) * int(m.group(2) or 1)
    digits = exp.count("9")
    if usage and re.match(r"COMP-3|PACKED", usage, re.I):
        fmt = "P"
    elif usage and re.match(r"COMP|BINARY", usage, re.I):
        fmt = "B"
    elif "X" in exp or "A" in exp:
        fmt = "A"
    else:
        fmt = "N"
    dec = 0
    if "V" in exp:
        dec = len(exp.split("V", 1)[1].replace("S", ""))
    length = len(exp.replace("V", "").replace("S", "")) if fmt == "A" else digits
    return fmt, f"{length}.{dec}" if dec else str(length)


def extract_copybook(conn, member_id, lines, member_name="?") -> dict:
    eid = upsert_entity(conn, member_name.upper(), "vsam", defined_in=member_id, defined_line=1,
                        notes="record layout from COBOL copybook")
    n = 0
    for line_no, seq, raw in lines:
        code = raw[6:72] if len(raw) > 6 and raw[6:7] in " -*/D" and re.match(r"^[\s\d]{6}", raw) else raw
        is_comment = bool(re.match(r"^.{6}[*/]", raw)) or code.lstrip().startswith("*")
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=seq, text=raw,
               is_comment=1 if is_comment else 0)
        if is_comment or not code.strip():
            continue
        if (m := RE_COB.match(code)):
            rest = m.group("rest") or ""
            pic = RE_PIC.search(rest)
            usage = RE_COMP.search(rest)
            occ = RE_OCCURS.search(rest)
            red = RE_REDEF.search(rest)
            fmt = length = None
            if pic:
                fmt, length = _pic_to_format(pic.group("pic"), usage.group(1) if usage else None)
            insert(conn, "entity_field", entity_id=eid, level=int(m.group("level")),
                   name=m.group("name").upper(), format=fmt, length=length,
                   occurrences=(occ.group("n2") or occ.group("n")) if occ else None,
                   options=usage.group(1).upper() if usage else None,
                   parent_fields=f"REDEFINES {red.group('t').upper()}" if red else None,
                   defined_line=line_no, remark=rest.strip()[:120] or None)
            n += 1
    set_metric(conn, member_name, "copybook.fields", n)
    return {"fields": n}


# -------------------------------------------------------------------------- JCL

RE_JOB = re.compile(r"^//(?P<name>[A-Z0-9#@$]{1,8})\s+JOB\b(?P<rest>.*)$", re.I)
RE_EXEC = re.compile(r"^//(?P<step>[A-Z0-9#@$]{0,8})\s+EXEC\s+(?P<rest>.*)$", re.I)
RE_DD = re.compile(r"^//(?P<dd>[A-Z0-9#@$]{1,8})\s+DD\s+(?P<rest>.*)$", re.I)
RE_CONT = re.compile(r"^//\s+(?P<rest>.*)$")
RE_SYSIN_END = re.compile(r"^(/\*|//)")
RE_PGM = re.compile(r"\bPGM=(?P<pgm>[A-Z0-9#@$]+)", re.I)
RE_PROCNAME = re.compile(r"\bEXEC\s+(?:PROC=)?(?P<proc>[A-Z0-9#@$]+)", re.I)
RE_DSN = re.compile(r"\bDSN=(?P<dsn>[A-Z0-9$#@.\-()&]+)", re.I)
RE_DISP = re.compile(r"\bDISP=(?P<disp>\([^)]*\)|[A-Z]+)", re.I)
RE_COND = re.compile(r"\b(COND|IF)=(?P<cond>\([^)]*\)|[A-Z0-9,.]+)", re.I)
RE_PARM = re.compile(r"\bPARM=(?P<parm>'[^']*'|\([^)]*\)|\S+)", re.I)


# DD names that carry infrastructure rather than business data. Registering their
# datasets as data stores inflates the entity count with load libraries and print
# files, which then appear in the data model as undefined entities and bury the
# real gaps. Override via config when a site uses non-standard names.
INFRASTRUCTURE_DDS = {
    "STEPLIB", "JOBLIB", "SYSLIB", "SYSPRINT", "SYSOUT", "SYSUDUMP", "SYSABEND",
    "SYSMDUMP", "SYSIN", "SYSTSIN", "SYSTSPRT", "SYSEXEC", "SYSPROC", "SYSTERM",
    "DDCARD", "ADARUN", "CMPRINT", "CMSYNIN", "CMOBJIN", "CMWKF00", "NATPARM",
    "SORTLIB", "SORTWK01", "SORTWK02", "SORTWK03", "SYSTSMSG", "ABNLIGNR",
}

# Natural batch is driven by a stacked command sequence on CMSYNIN, so the program
# actually executed appears there rather than on the EXEC card. Without reading
# this, every batch Natural program looks like unreferenced dead code.
RE_NAT_STACK_LOGON = re.compile(r"^\s*LOGON\s+(?P<lib>[A-Z0-9#@$\-]+)", re.I)
RE_NAT_STACK_PGM = re.compile(r"^\s*(?P<pgm>[A-Z][A-Z0-9#@$\-]{1,31})\s*(?P<parms>.*)$", re.I)
NAT_STACK_NOISE = {"FIN", "LOGON", "LOGOFF", "MENU", "SYSMAIN", "SYSDDM", "SYSOBJH", "GLOBALS"}


# Programs that drive a stacked Natural session. SYSIN belonging to any other
# program is a control-card stream (IDCAMS REPRO, DFSORT, IEBGENER) and mining it
# for program names manufactures call edges to things like REPRO and OUTDATASET.
NATURAL_DRIVERS = re.compile(r"^(NAT|NATBATCH|NATB|NATVSAM|ADARUN)", re.I)


def _parse_natural_stack(conn, member_id, dd_line_no, dd_name, body, step_name,
                         step_program=None):
    """Create call edges for programs stacked on a Natural batch input DD."""
    dd = dd_name.upper()
    if dd in {"CMSYNIN", "CMOBJIN"}:
        pass  # unambiguously Natural input
    elif dd == "SYSIN" and step_program and NATURAL_DRIVERS.match(step_program):
        pass
    else:
        return 0
    n = 0
    library = None
    for offset, raw in enumerate(body.split("\n")):
        line = raw.strip()
        if not line:
            continue
        if (m := RE_NAT_STACK_LOGON.match(line)):
            library = m.group("lib").upper()
            continue
        if (m := RE_NAT_STACK_PGM.match(line)):
            pgm = m.group("pgm").upper()
            if pgm in NAT_STACK_NOISE or pgm.startswith("*"):
                continue
            insert(conn, "call_edge", caller_id=member_id, callee_name=pgm,
                   call_kind="EXEC_PGM", line_no=dd_line_no + 1 + offset,
                   args=f"stacked on {dd_name}"
                        + (f" after LOGON {library}" if library else "")
                        + (f", step {step_name}" if step_name else ""))
            n += 1
    if n == 0:
        add_gap(conn, "sme_question",
                f"{dd_name} supplies stacked input but no Natural program name was recognised "
                f"in it. Confirm how the batch program is selected — it may come from a "
                f"parameter, a driver module, or the scheduler.",
                member_id=member_id, line_no=dd_line_no, severity="medium")
    return n


def extract_jcl(conn, member_id, lines, member_name="?") -> dict:
    step = None
    step_program = None
    steps = dds = 0
    sysin_dd = None
    sysin_lines: list[str] = []
    i = 0
    raws = list(lines)
    while i < len(raws):
        line_no, seq, raw = raws[i]
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=seq, text=raw,
               is_comment=1 if raw.startswith("//*") else 0)
        if sysin_dd is not None:
            if RE_SYSIN_END.match(raw):
                body = "\n".join(sysin_lines)
                insert(conn, "job_dd", member_id=member_id, line_no=sysin_dd[0], step_name=step,
                       dd_name=sysin_dd[1], sysin_body=body[:4000])
                _parse_natural_stack(conn, member_id, sysin_dd[0], sysin_dd[1], body, step,
                                     sysin_dd[2])
                sysin_dd, sysin_lines = None, []
            else:
                sysin_lines.append(raw)
                i += 1
                continue
        if raw.startswith("//*"):
            i += 1
            continue

        stmt = raw
        j = i
        while stmt.rstrip().endswith(",") and j + 1 < len(raws):
            j += 1
            nxt = raws[j][2]
            if (c := RE_CONT.match(nxt)):
                stmt = stmt.rstrip() + c.group("rest").strip()
            else:
                break

        if (m := RE_EXEC.match(stmt)):
            step = (m.group("step") or f"STEP{steps + 1}").upper()
            rest = m.group("rest")
            pgm = RE_PGM.search(rest)
            proc = None if pgm else (RE_PROCNAME.search(stmt).group("proc") if RE_PROCNAME.search(stmt) else None)
            cond = RE_COND.search(rest)
            parm = RE_PARM.search(rest)
            step_program = pgm.group("pgm").upper() if pgm else None
            insert(conn, "job_step", member_id=member_id, line_no=line_no, step_name=step,
                   program=step_program,
                   proc=proc.upper() if proc else None,
                   cond=cond.group("cond") if cond else None,
                   parm=parm.group("parm") if parm else None)
            if pgm:
                insert(conn, "call_edge", caller_id=member_id, callee_name=pgm.group("pgm").upper(),
                       call_kind="EXEC_PGM", line_no=line_no, args=parm.group("parm") if parm else None)
            steps += 1
        elif (m := RE_DD.match(stmt)):
            dd = m.group("dd").upper()
            rest = m.group("rest")
            dsn = RE_DSN.search(rest)
            disp = RE_DISP.search(rest)
            if re.match(r"^\s*\*\s*$", rest) or rest.strip().startswith("*"):
                sysin_dd = (line_no, dd, step_program)
                i = j + 1
                continue
            insert(conn, "job_dd", member_id=member_id, line_no=line_no, step_name=step,
                   dd_name=dd, dsn=dsn.group("dsn").upper() if dsn else None,
                   disp=disp.group("disp") if disp else None)
            if dsn and dd not in INFRASTRUCTURE_DDS:
                upsert_entity(conn, dsn.group("dsn").upper(), "vsam",
                              physical_ref=dsn.group("dsn").upper())
            dds += 1
        i = j + 1

    if sysin_dd is not None:
        body = "\n".join(sysin_lines)
        insert(conn, "job_dd", member_id=member_id, line_no=sysin_dd[0], step_name=step,
               dd_name=sysin_dd[1], sysin_body=body[:4000])
        _parse_natural_stack(conn, member_id, sysin_dd[0], sysin_dd[1], body, step, sysin_dd[2])
    set_metric(conn, member_name, "jcl.steps", steps)
    set_metric(conn, member_name, "jcl.dds", dds)
    if steps == 0:
        add_gap(conn, "unparsed_line", f"No EXEC steps found in JCL member {member_name}.",
                member_id=member_id, severity="medium")
    return {"steps": steps, "dds": dds}


# --------------------------------------------------------------------- CICS CSD

RE_CSD_DEFINE = re.compile(
    r"\bDEFINE\s+(?P<type>PROGRAM|TRANSACTION|FILE|MAPSET|TDQUEUE|TSMODEL|TRANCLASS)"
    r"\s*\(\s*(?P<name>[^)\s]+)\s*\)(?P<rest>.*)$", re.I)
RE_CSD_ATTR = re.compile(r"\b(?P<k>[A-Z]+)\s*\(\s*(?P<v>[^)]*)\)", re.I)


def extract_cics_csd(conn, member_id, lines, member_name="?") -> dict:
    n = 0
    i = 0
    raws = list(lines)
    while i < len(raws):
        line_no, seq, raw = raws[i]
        insert(conn, "source_line", member_id=member_id, line_no=line_no, seq=seq, text=raw,
               is_comment=1 if raw.lstrip().startswith("*") else 0)
        stmt = raw
        j = i
        # CSD extract listings wrap attributes across continuation lines.
        while j + 1 < len(raws) and not RE_CSD_DEFINE.search(raws[j + 1][2]) and raws[j + 1][2].startswith(" "):
            j += 1
            stmt += " " + raws[j][2].strip()
        if (m := RE_CSD_DEFINE.search(stmt)):
            attrs = {k.upper(): v.strip() for k, v in RE_CSD_ATTR.findall(m.group("rest") or "")}
            rtype = m.group("type").upper()
            rname = m.group("name").upper()
            insert(conn, "cics_resource", member_id=member_id, line_no=line_no,
                   resource_type=rtype, resource_name=rname,
                   attributes="; ".join(f"{k}={v}" for k, v in attrs.items())[:600] or None)
            n += 1
            if rtype == "TRANSACTION" and attrs.get("PROGRAM"):
                insert(conn, "call_edge", caller_id=member_id,
                       callee_name=attrs["PROGRAM"].upper(), call_kind="EXEC_PGM",
                       line_no=line_no, args=f"CICS transaction {rname}")
            if rtype == "FILE":
                upsert_entity(conn, attrs.get("DSNAME", rname).upper(), "vsam",
                              physical_ref=attrs.get("DSNAME"), defined_in=member_id,
                              defined_line=line_no, notes=f"CICS FILE {rname}")
        i = j + 1
    set_metric(conn, member_name, "cics.resources", n)
    return {"resources": n}
