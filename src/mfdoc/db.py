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
-- graph.resolve()'s callee-name lookup and orphans()'s NOT EXISTS both
-- compare UPPER(name); a plain index on name can't serve that predicate,
-- so this is an expression index, matched only when the query uses the
-- identical UPPER(...) expression.
CREATE INDEX IF NOT EXISTS ix_member_upper_name ON member(UPPER(name));

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
-- resolve_entity() and graph.resolve()'s entity_name lookup both compare
-- UPPER(name); see ix_member_upper_name's comment for why this needs to be
-- an expression index rather than a plain one.
CREATE INDEX IF NOT EXISTS ix_entity_upper_name ON entity(UPPER(name));

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
    key_expr      TEXT,                   -- WITH/BY/WHERE clause text, as written at this access
    descriptor    TEXT,                   -- descriptor or key used, if identifiable
    -- When key_expr is (or contains) a bare variable rather than a literal
    -- or inline expression, and that variable's most recent assignment in
    -- this member (before this line) was found, these two record where and
    -- how it was actually built -- e.g. GET WIDGETFILE01(LOOKUP_KEY)FIRST with
    -- LOOKUP_KEY="H"+BUILD_PART(1,1,5)+... assigned a few lines earlier.
    -- Without this, "the key is LOOKUP_KEY" tells a reader nothing about what
    -- value is really being looked up. NULL when no such assignment was
    -- found (or key_expr isn't a bare variable) -- never guessed.
    key_source_line INTEGER,
    key_source_expr TEXT,
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
-- graph.resolve()'s member lookup and orphans()'s NOT EXISTS both compare
-- UPPER(callee_name); see ix_member_upper_name's comment for why this
-- needs to be an expression index rather than a plain one.
CREATE INDEX IF NOT EXISTS ix_call_edge_upper_callee ON call_edge(UPPER(callee_name));
-- orphans()'s NOT EXISTS subquery ORs `ce.callee_id = m.id` against the
-- UPPER(callee_name) comparison above -- without an index on callee_id too,
-- SQLite can't apply its multi-index OR optimisation and falls back to a
-- full scan of call_edge per candidate orphan (confirmed with
-- EXPLAIN QUERY PLAN; adding this index changes that plan to
-- "MULTI-INDEX OR" over ix_call_edge_callee_id + ix_call_edge_upper_callee).
CREATE INDEX IF NOT EXISTS ix_call_edge_callee_id ON call_edge(callee_id);

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

-- Internal subroutine/paragraph boundaries: Natural's DEFINE SUBROUTINE /
-- END-SUBROUTINE, Mantis's ENTRY name / EXIT. Every other per-line fact
-- table (rule_candidate, data_access, interaction, call_edge) only ever
-- recorded *that* a line belongs to some member -- never *which internal
-- routine within it*, so there was no deterministic way to group a
-- member's facts by subroutine, measure per-routine coverage, or chunk
-- narration along routine lines rather than an arbitrary rule count. A
-- line whose line_no falls in no routine's [start_line, end_line] belongs
-- to the member's main body, not to any named routine.
CREATE TABLE IF NOT EXISTS routine (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,          -- natural_subroutine | mantis_entry
    start_line    INTEGER NOT NULL,
    end_line      INTEGER,                -- NULL when no matching END-SUBROUTINE/EXIT was found
    confidence    TEXT NOT NULL DEFAULT 'verified'
);
CREATE INDEX IF NOT EXISTS ix_routine_member ON routine(member_id);

-- Candidate business rules: conditionals, validations, computations, escapes
CREATE TABLE IF NOT EXISTS rule_candidate (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    line_no       INTEGER NOT NULL,
    -- For a construct that opens a block (IF, WHILE, FOR, CASE, ...), the
    -- line of its matching END/END-*, once found -- the block's extent.
    -- Populated for IF specifically (natural.py's _match_rules, mantis.py's
    -- extract()) so a later fact (a GET, a DELETE, another rule) inside
    -- either the IF's or its ELSE's own extent can be told apart from
    -- unrelated code that merely follows it -- without this, narration has
    -- no structural cue that the two share a branch, and consistently
    -- describes only the branch that reads as interesting (typically the
    -- error/validation one) while silently dropping the other's effects.
    end_line      INTEGER,
    -- For an ELSE (or an equivalent alternate-branch construct), the
    -- line_no of the IF/etc. it pairs with -- lets a reader join "if this
    -- condition is false" back to the condition itself without relying on
    -- indentation the brief doesn't carry.
    pair_line_no  INTEGER,
    construct     TEXT NOT NULL,          -- IF | DECIDE ON | DECIDE FOR | CASE | WHILE | FOR | REPEAT | AT BREAK | ON ERROR | REINPUT | ESCAPE | LOOP
    condition     TEXT,
    depth         INTEGER DEFAULT 0,
    fields_used   TEXT,                   -- comma separated field/variable names referenced
    literals      TEXT,                   -- literal values in the condition (magic numbers/codes)
    raw           TEXT NOT NULL,
    confidence    TEXT NOT NULL DEFAULT 'verified'  -- verified | inferred -- 'inferred' for
                                          -- reporting-mode LOOP/depth inference (issue #5),
                                          -- where nesting is read from indentation, not an
                                          -- explicit END-* keyword
);
CREATE INDEX IF NOT EXISTS ix_rule_member ON rule_candidate(member_id);

-- Rule-theme classification: which business concept a rule_candidate
-- belongs to, for the thematic rules-register rollup. UNIQUE on
-- rule_candidate_id so re-classifying after a taxonomy edit is an
-- upsert (INSERT ... ON CONFLICT), never an ever-growing history.
CREATE TABLE IF NOT EXISTS rule_theme (
    id                INTEGER PRIMARY KEY,
    rule_candidate_id INTEGER NOT NULL REFERENCES rule_candidate(id),
    theme             TEXT NOT NULL,
    source            TEXT NOT NULL,   -- keyword | llm | structural
    UNIQUE(rule_candidate_id)
);
CREATE INDEX IF NOT EXISTS ix_rule_theme_theme ON rule_theme(theme);

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

-- Test-plan derive: one row per test scenario, built only from facts already
-- in the tables above (rule_candidate branches, parameter variables, data
-- access / call edges for mocking). Wholly derived and rebuilt on every
-- `mfdoc test-plan` run, the same way gap.DERIVED_GAP_KINDS rows are --
-- never edited in place, never carried forward from a stale run.
CREATE TABLE IF NOT EXISTS test_case (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES member(id),
    kind          TEXT NOT NULL,          -- unit | integration
    rule_candidate_id INTEGER REFERENCES rule_candidate(id),
    scenario_name TEXT NOT NULL,          -- stable MEMBER:BR-nnn-derived handle
    given_json    TEXT NOT NULL,          -- parameters + required mocks (entities/callees)
    when_json     TEXT NOT NULL,          -- construct/condition exercised, with citation
    then_json     TEXT NOT NULL,          -- cited source excerpt for the branch body --
                                          -- never a guessed expected value
    status        TEXT NOT NULL DEFAULT 'characterization',
                                          -- characterization | spec | bug-current | bug-desired;
                                          -- set from test-overlay.yml, defaults to
                                          -- characterization when a rule has no overlay entry
    citation      TEXT NOT NULL,          -- MEMBER:LINE or MEMBER:LINE-LINE
    confidence    TEXT NOT NULL DEFAULT 'verified'
);
CREATE INDEX IF NOT EXISTS ix_testcase_member ON test_case(member_id);

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


# Columns added to an *existing* table after its CREATE TABLE first shipped.
# `CREATE TABLE IF NOT EXISTS` (SCHEMA, above) only ever creates a table
# that doesn't exist yet -- an index.db built before one of these columns
# existed keeps the table it already has, forever, no matter how many
# times `mfdoc ingest` reruns SCHEMA against it. Without this, a column
# added here in source stays invisible on every pre-existing engagement's
# index.db, and the first INSERT naming it fails with "no such column",
# well after the point a reader would connect that error to a schema
# change. A brand new table (e.g. `routine`) never needs an entry here --
# CREATE TABLE IF NOT EXISTS already covers it correctly either way.
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("rule_candidate", "pair_line_no", "INTEGER"),
    ("data_access", "key_source_line", "INTEGER"),
    ("data_access", "key_source_expr", "TEXT"),
]


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    for table, column, coltype in _COLUMN_MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    conn.commit()


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _apply_column_migrations(conn)
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


# Every table a dialect extractor (src/mfdoc/dialects/*.py) writes into for a
# given member_id, keyed NOT NULL member_id -- these are wholly owned by the
# member and deleted outright on re-ingest. Kept as one list so a new dialect
# fact table only needs adding here, not re-derived by hand at every call
# site that needs to purge stale facts before re-extracting a changed file.
_MEMBER_OWNED_TABLES = (
    "source_line", "variable", "data_access", "transaction_marker",
    "interaction",
    # test_case.rule_candidate_id references rule_candidate(id) -- must be
    # deleted before rule_candidate itself, or PRAGMA foreign_keys=ON makes
    # the rule_candidate delete below fail outright.
    "test_case", "rule_candidate", "message_ref", "job_step", "job_dd",
    "cics_resource",
)


def purge_member_facts(conn, member_id: int) -> None:
    """Delete everything a member owns, without deleting the member row itself.

    Re-running extraction for a member without this first would leave every
    prior run's rows sitting alongside the new ones -- upsert_member reuses
    the same member_id across runs (matched by name/library/dialect), but
    the dialect extractors it feeds into only ever INSERT. Use this (not
    purge_member) when the member row is being kept and re-extracted into,
    e.g. cmd_ingest's per-chunk loop -- deleting the row here would just
    force upsert_member to immediately recreate an identical one, and would
    incorrectly null out cross-references from *other* members that are
    still valid because this member still exists.
    """
    # rule_theme.rule_candidate_id references rule_candidate(id), but
    # rule_theme carries no member_id of its own -- it isn't reachable by
    # the member_id-keyed loop below. Must run before the rule_candidate
    # DELETE removes the rows this join needs, or a later re-ingest that
    # lets SQLite reuse one of those freed rowids for an unrelated new
    # rule_candidate would silently inherit this orphaned theme.
    conn.execute(
        "DELETE FROM rule_theme WHERE rule_candidate_id IN "
        "(SELECT id FROM rule_candidate WHERE member_id=?)",
        (member_id,),
    )
    for table in _MEMBER_OWNED_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE member_id=?", (member_id,))
    conn.execute("DELETE FROM gap WHERE member_id=?", (member_id,))
    conn.execute("DELETE FROM call_edge WHERE caller_id=?", (member_id,))


def purge_member(conn, member_id: int) -> None:
    """Delete a member outright, along with everything it owns.

    Beyond purge_member_facts, this also clears the nullable cross-references
    *other* rows hold into this member -- call_edge.callee_id (another
    member's call landing here), entity.defined_in, entity_link.via_member --
    rather than deleting those rows, since the fact they describe (the call
    was made; the entity exists) is still true even though this member is
    gone. The next `mfdoc derive` re-resolves them against whatever the
    fresh ingest wrote. Used when a member has genuinely stopped existing --
    a changed file no longer produces it (a concatenated member removed
    from the file, a banner pattern that no longer matches), or its
    source_file was removed from the source set entirely -- not for
    re-extracting an existing member in place; see purge_member_facts for
    that.
    """
    purge_member_facts(conn, member_id)
    conn.execute(
        "UPDATE call_edge SET callee_id=NULL, resolved=0 WHERE callee_id=?", (member_id,)
    )
    conn.execute("UPDATE entity SET defined_in=NULL WHERE defined_in=?", (member_id,))
    conn.execute("UPDATE entity_link SET via_member=NULL WHERE via_member=?", (member_id,))
    conn.execute("DELETE FROM member WHERE id=?", (member_id,))


def resolve_member_by_name(conn, name: str, columns: str = "*",
                            dialect_in: tuple[str, ...] | None = None,
                            object_type_in: tuple[str, ...] | None = None):
    """Resolve a bare member name to exactly one row, the shared refusal
    every narrate/derive stage that takes a `--member`/member_name argument
    makes for the identical case (module_brief, test_case_brief,
    draft_overlay_for_member, testplan.run_all): a bare name is only unique
    together with library+dialect (see the `UNIQUE(name, library, dialect)`
    constraint above), so two members can share a name across libraries.

    Returns `(rows, ambiguous_libs)`. Exactly one match: `(rows, [])` with
    one row. No match: `([], [])`. More than one match: `([], libs)` where
    `libs` is the sorted set of libraries the name matched -- callers must
    not guess which one applies, and must not treat this the same as "no
    match" when deciding whether to delete/overwrite existing derived data
    for the name.
    """
    clauses = ["UPPER(name)=UPPER(?)"]
    params: list = [name]
    if dialect_in:
        clauses.append(f"dialect IN ({','.join('?' * len(dialect_in))})")
        params.extend(dialect_in)
    if object_type_in:
        clauses.append(f"object_type IN ({','.join('?' * len(object_type_in))})")
        params.extend(object_type_in)
    where = " AND ".join(clauses)
    # Ambiguity (and the libraries to report) is checked against `id,
    # library` regardless of what the caller wants back, so a caller asking
    # for a narrower `columns` (e.g. just `library`, or `1` for an
    # existence check) still gets a correct ambiguity verdict rather than a
    # KeyError or a false "unambiguous" reading of its own trimmed columns.
    probe = conn.execute(f"SELECT id, library FROM member WHERE {where}", params).fetchall()
    if len(probe) > 1:
        return [], sorted({r["library"] or "?" for r in probe})
    if not probe:
        return [], []
    rows = conn.execute(
        f"SELECT {columns} FROM member WHERE {where}", params
    ).fetchall()
    return rows, []


def group_members_by_name(rows) -> tuple[dict, list[str]]:
    """Group already-fetched member rows (must include `name`) by name, for
    callers that resolve a known list of bare names in one batched query
    rather than one round trip per name (see rules_register's comment on
    why batching beats per-name lookups at system scale).

    Returns `(unambiguous, ambiguous)`: `unambiguous` maps name -> its one
    row, for names with no cross-library duplicate; `ambiguous` is the
    sorted list of names that matched more than one row and were skipped
    rather than guessed at.
    """
    by_name: dict[str, list] = {}
    for r in rows:
        by_name.setdefault(r["name"], []).append(r)
    unambiguous = {name: rs[0] for name, rs in by_name.items() if len(rs) == 1}
    ambiguous = sorted(name for name, rs in by_name.items() if len(rs) > 1)
    return unambiguous, ambiguous


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
