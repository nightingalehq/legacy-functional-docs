"""SQLite fact store for the legacy-functional-docs pipeline.

Everything deterministic lands here. The narrative pass reads from here and is
forbidden from asserting anything it cannot cite back to a row in this database.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- provenance
CREATE TABLE IF NOT EXISTS ingest_run (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT NOT NULL,
    tool_version  TEXT NOT NULL,
    config_json   TEXT
);

CREATE TABLE IF NOT EXISTS source_file (
    id            INTEGER PRIMARY KEY,
    path          TEXT NOT NULL UNIQUE,   -- path as ingested (normalised copy)
    origin_path   TEXT,                   -- original path/member as supplied
    sha256        TEXT NOT NULL,
    encoding_in   TEXT,                   -- cp037, cp500, utf-8 ...
    seq_cols      TEXT,                   -- e.g. '73:80' if sequence numbers stripped
    line_count    INTEGER,
    ingest_run_id INTEGER REFERENCES ingest_run(id)
);

-- A member is one logical legacy object. One source_file may hold many members
-- (SYSOBJH unloads, Mantis library exports, PDS listings).
CREATE TABLE IF NOT EXISTS member (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    dialect       TEXT NOT NULL,          -- natural | mantis | adabas_fdt | ddm | supra_dir | sql_ddl | jcl | cics_csd | cobol_copybook
    object_type   TEXT,                   -- program | subprogram | subroutine | copycode | map | lda | gda | pda | screen | view | job | ...
    library       TEXT,                   -- Natural library / Mantis library / PDS name
    system        TEXT,                   -- logical application/system grouping
    source_file_id INTEGER REFERENCES source_file(id),
    first_line    INTEGER NOT NULL DEFAULT 1,
    last_line     INTEGER,
    mode          TEXT,                   -- structured | reporting | unknown  (Natural)
    UNIQUE(name, library, dialect)
);
CREATE INDEX IF NOT EXISTS ix_member_dialect ON member(dialect);

CREATE TABLE IF NOT EXISTS source_line (
    member_id     INTEGER NOT NULL REFERENCES member(id),
    line_no       INTEGER NOT NULL,       -- 1-based within the member; this is what citations use
    seq           TEXT,                   -- original sequence number if present
    text          TEXT NOT NULL,
    is_comment    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (member_id, line_no)
);

-- ------------------------------------------------------------------ data defs
CREATE TABLE IF NOT EXISTS entity (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,          -- DDM name / Adabas file name / table / dataset / VSAM DD
    kind          TEXT NOT NULL,          -- adabas_file | ddm | supra_master | supra_ved | sql_table | vsam | workfile | queue
    physical_ref  TEXT,                   -- Adabas DBID/FNR, dataset name, DD name, schema.table
    dbid          TEXT,
    fnr           TEXT,
    defined_in    INTEGER REFERENCES member(id),
    defined_line  INTEGER,
    notes         TEXT,
    UNIQUE(name, kind)
);

CREATE TABLE IF NOT EXISTS entity_field (
    id            INTEGER PRIMARY KEY,
    entity_id     INTEGER NOT NULL REFERENCES entity(id),
    level         INTEGER,
    name          TEXT NOT NULL,          -- long name (DDM) or element name
    short_name    TEXT,                   -- Adabas 2-char short name
    format        TEXT,                   -- A, N, P, B, I, F, D, T, L ...
    length        TEXT,
    occurrences   TEXT,                   -- MU/PE occurrence spec
    is_descriptor INTEGER DEFAULT 0,
    descriptor_kind TEXT,                 -- DE | SUPER | SUB | PHON | HYPER | UQ | primary_key | index
    options       TEXT,                   -- NU, FI, NC, NN, UQ ... comma separated
    parent_fields TEXT,                   -- for super/sub descriptors: component field list
    defined_line  INTEGER,
    remark        TEXT
);
CREATE INDEX IF NOT EXISTS ix_field_entity ON entity_field(entity_id);

-- Relationships between entities: Adabas coupling, Supra linkpaths, FK, DDM->file
CREATE TABLE IF NOT EXISTS entity_link (
    id            INTEGER PRIMARY KEY,
    from_entity   INTEGER NOT NULL REFERENCES entity(id),
    to_entity     INTEGER NOT NULL REFERENCES entity(id),
    link_kind     TEXT NOT NULL,          -- implements | coupled | linkpath | foreign_key | joined_in_code
    link_name     TEXT,
    via_member    INTEGER REFERENCES member(id),
    via_line      INTEGER,
    confidence    TEXT NOT NULL DEFAULT 'verified'
);

-- --------------------------------------------------------------- program facts
-- Variables declared in a module (DEFINE DATA, Mantis declarations)
CREATE TABLE IF NOT EXISTS variable (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    scope         TEXT,                   -- local | parameter | global | independent | view | mantis_local | mantis_shared
    level         INTEGER,
    name          TEXT NOT NULL,
    format        TEXT,
    length        TEXT,
    redefines     TEXT,
    init_value    TEXT,
    view_of       TEXT,                   -- DDM name when scope='view'
    line_no       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_var_member ON variable(member_id);

-- Every data access statement found. This is the backbone of the CRUD matrix.
CREATE TABLE IF NOT EXISTS data_access (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    line_no       INTEGER NOT NULL,
    verb          TEXT NOT NULL,          -- READ | FIND | HISTOGRAM | GET | STORE | UPDATE | DELETE | SELECT | INSERT | RDNXT | ...
    crud          TEXT NOT NULL,          -- C | R | U | D | ?
    entity_name   TEXT,                   -- as written in source
    entity_id     INTEGER REFERENCES entity(id),
    via_view      TEXT,                   -- Natural view name / Mantis view
    key_expr      TEXT,                   -- WITH/BY/WHERE clause text
    descriptor    TEXT,                   -- descriptor or key used, if identifiable
    raw           TEXT NOT NULL,
    confidence    TEXT NOT NULL DEFAULT 'verified'
);
CREATE INDEX IF NOT EXISTS ix_access_member ON data_access(member_id);
CREATE INDEX IF NOT EXISTS ix_access_entity ON data_access(entity_name);

-- Call graph edges
CREATE TABLE IF NOT EXISTS call_edge (
    id            INTEGER PRIMARY KEY,
    caller_id     INTEGER NOT NULL REFERENCES member(id),
    callee_name   TEXT NOT NULL,
    callee_id     INTEGER REFERENCES member(id),
    call_kind     TEXT NOT NULL,          -- CALLNAT | PERFORM | FETCH | FETCH RETURN | CALL | INCLUDE | RUN | CHAIN | LINK | XCTL | EXEC_PGM
    dynamic       INTEGER NOT NULL DEFAULT 0,  -- 1 when target is a variable, not a literal
    args          TEXT,
    line_no       INTEGER NOT NULL,
    resolved      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_call_caller ON call_edge(caller_id);

-- Transaction boundaries: essential for describing units of work honestly
CREATE TABLE IF NOT EXISTS transaction_marker (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    line_no       INTEGER NOT NULL,
    marker        TEXT NOT NULL,          -- END TRANSACTION | BACKOUT TRANSACTION | COMMIT | ROLLBACK | ENDTR | SYNCPOINT
    et_data       TEXT
);

-- Screens / maps / CONVERSE points: user-facing interaction inventory
CREATE TABLE IF NOT EXISTS interaction (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    line_no       INTEGER NOT NULL,
    kind          TEXT NOT NULL,          -- INPUT | CONVERSE | SHOW | WRITE | DISPLAY | PRINT | REINPUT
    target        TEXT,                   -- map/view name
    fields        TEXT
);

-- Candidate business rules: conditionals, validations, computations, escapes
CREATE TABLE IF NOT EXISTS rule_candidate (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    line_no       INTEGER NOT NULL,
    end_line      INTEGER,
    construct     TEXT NOT NULL,          -- IF | DECIDE ON | DECIDE FOR | CASE | WHILE | FOR | REPEAT | AT BREAK | ON ERROR | REINPUT | ESCAPE
    condition     TEXT,
    depth         INTEGER DEFAULT 0,
    fields_used   TEXT,                   -- comma separated field/variable names referenced
    literals      TEXT,                   -- literal values in the condition (magic numbers/codes)
    raw           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rule_member ON rule_candidate(member_id);

-- Error / message handling, useful for surfacing user-visible business messages
CREATE TABLE IF NOT EXISTS message_ref (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    line_no       INTEGER NOT NULL,
    kind          TEXT NOT NULL,          -- REINPUT | ON ERROR | SIGNAL | message_number | literal
    number        TEXT,
    text          TEXT
);

-- Batch orchestration from JCL / scheduler
CREATE TABLE IF NOT EXISTS job_step (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    line_no       INTEGER NOT NULL,
    step_name     TEXT,
    program       TEXT,
    proc          TEXT,
    cond          TEXT,
    parm          TEXT
);

CREATE TABLE IF NOT EXISTS job_dd (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    line_no       INTEGER NOT NULL,
    step_name     TEXT,
    dd_name       TEXT,
    dsn           TEXT,
    disp          TEXT,
    sysin_body    TEXT
);

-- CICS resource definitions
CREATE TABLE IF NOT EXISTS cics_resource (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    line_no       INTEGER NOT NULL,
    resource_type TEXT NOT NULL,          -- TRANSACTION | PROGRAM | FILE | MAPSET | TDQUEUE | TSMODEL
    resource_name TEXT NOT NULL,
    attributes    TEXT
);

-- ------------------------------------------------------------- gaps & metrics
-- Anything the deterministic pass could not resolve. This becomes the SME
-- interview agenda, and it is the single most valuable output for the humans.
CREATE TABLE IF NOT EXISTS gap (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER REFERENCES member(id),
    line_no       INTEGER,
    gap_kind      TEXT NOT NULL,          -- unresolved_call | missing_source | dynamic_target | unparsed_line
                                          -- | undefined_entity | reporting_mode | external_call | orphan_module
                                          -- | no_ddl_for_entity | ambiguous_dialect | sme_question
    severity      TEXT NOT NULL DEFAULT 'medium',  -- low | medium | high
    detail        TEXT NOT NULL,
    raw           TEXT
);
CREATE INDEX IF NOT EXISTS ix_gap_kind ON gap(gap_kind);

CREATE TABLE IF NOT EXISTS metric (
    id            INTEGER PRIMARY KEY,
    scope         TEXT NOT NULL,          -- 'global' or member name
    name          TEXT NOT NULL,
    value         TEXT NOT NULL
);

-- Written by the narrative pass so citations can be validated mechanically.
CREATE TABLE IF NOT EXISTS doc_claim (
    id            INTEGER PRIMARY KEY,
    doc_path      TEXT NOT NULL,
    claim_id      TEXT,
    confidence    TEXT NOT NULL,          -- verified | inferred | unresolved
    citation      TEXT,                   -- MEMBER:LINE or MEMBER:LINE-LINE
    member_name   TEXT,
    line_from     INTEGER,
    line_to       INTEGER,
    valid         INTEGER,
    note          TEXT
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_member(conn, name, dialect, **kw):
    cur = conn.execute(
        "SELECT id FROM member WHERE name=? AND IFNULL(library,'')=? AND dialect=?",
        (name, kw.get("library") or "", dialect),
    )
    row = cur.fetchone()
    if row:
        mid = row["id"]
        sets, vals = [], []
        for k, v in kw.items():
            if v is not None:
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            conn.execute(f"UPDATE member SET {', '.join(sets)} WHERE id=?", (*vals, mid))
        return mid
    cols = ["name", "dialect", *kw.keys()]
    conn.execute(
        f"INSERT INTO member ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        (name, dialect, *kw.values()),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def upsert_entity(conn, name, kind, **kw):
    cur = conn.execute("SELECT id FROM entity WHERE name=? AND kind=?", (name, kind))
    row = cur.fetchone()
    if row:
        eid = row["id"]
        sets, vals = [], []
        for k, v in kw.items():
            if v is not None:
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            conn.execute(f"UPDATE entity SET {', '.join(sets)} WHERE id=?", (*vals, eid))
        return eid
    cols = ["name", "kind", *kw.keys()]
    conn.execute(
        f"INSERT INTO entity ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        (name, kind, *kw.values()),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def insert(conn, table, **kw):
    cols = list(kw.keys())
    conn.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        tuple(kw.values()),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def add_gap(conn, gap_kind, detail, member_id=None, line_no=None, severity="medium", raw=None):
    return insert(
        conn, "gap", gap_kind=gap_kind, detail=detail, member_id=member_id,
        line_no=line_no, severity=severity, raw=raw,
    )


def set_metric(conn, scope, name, value):
    conn.execute("DELETE FROM metric WHERE scope=? AND name=?", (scope, name))
    insert(conn, "metric", scope=scope, name=name, value=json.dumps(value) if isinstance(value, (dict, list)) else str(value))


def resolve_entity(conn, name, kind_family: str, default_kind: str, **kw) -> int:
    """Find an entity by name within a kind family before creating a new one.

    Different inputs describe the same store at different levels of detail: a
    Mantis DML call knows only a dataset name, while the Supra directory knows
    whether it is a master or a variable-entry dataset. Whichever is ingested
    first must not lock in a guess that the other then duplicates.
    """
    row = conn.execute(
        "SELECT id FROM entity WHERE UPPER(name)=UPPER(?) AND kind LIKE ? LIMIT 1",
        (name, kind_family + "%"),
    ).fetchone()
    if row:
        return row["id"]
    return upsert_entity(conn, name, default_kind, **kw)


def upsert_field(conn, entity_id: int, name: str, **kw) -> int:
    """Insert or enrich a field. Later, more specific information wins.

    A Supra control-key line names a key before the element list describes its
    type; two rows for one element would show up as a duplicated column in the
    generated data dictionary.
    """
    row = conn.execute(
        "SELECT id FROM entity_field WHERE entity_id=? AND UPPER(name)=UPPER(?) LIMIT 1",
        (entity_id, name),
    ).fetchone()
    if row:
        sets, vals = [], []
        for k, v in kw.items():
            if v is not None:
                sets.append(f"{k}=COALESCE(?, {k})" if k in ("remark",) else f"{k}=?")
                vals.append(v)
        if sets:
            conn.execute(f"UPDATE entity_field SET {', '.join(sets)} WHERE id=?", (*vals, row["id"]))
        return row["id"]
    return insert(conn, "entity_field", entity_id=entity_id, name=name, **kw)
