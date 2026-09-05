"""Guards on db.connect()'s column-migration step (_apply_column_migrations).

CREATE TABLE IF NOT EXISTS never adds a column to a table that already
exists -- an index.db built before a column was added to SCHEMA keeps the
table it already has, forever, regardless of how many times `mfdoc ingest`
reruns SCHEMA against it. Without a migration step, the first INSERT naming
that column on such a database fails with "no such column", confusingly far
from the actual cause (a code upgrade, not anything the user did wrong).
"""

from __future__ import annotations

import sqlite3

import pytest

from mfdoc import db as db_mod

# A stand-in for a pre-migration rule_candidate/data_access shape -- just
# enough columns to exercise the migration, not a full historical schema.
_OLD_SCHEMA = """
CREATE TABLE member (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, dialect TEXT NOT NULL,
    library TEXT, system TEXT, object_type TEXT, mode TEXT, source_file_id INTEGER
);
CREATE TABLE rule_candidate (
    id INTEGER PRIMARY KEY, member_id INTEGER NOT NULL, line_no INTEGER NOT NULL,
    end_line INTEGER, construct TEXT NOT NULL, condition TEXT, depth INTEGER DEFAULT 0,
    fields_used TEXT, literals TEXT, raw TEXT NOT NULL, confidence TEXT NOT NULL DEFAULT 'verified'
);
CREATE TABLE data_access (
    id INTEGER PRIMARY KEY, member_id INTEGER NOT NULL, line_no INTEGER NOT NULL,
    verb TEXT NOT NULL, crud TEXT NOT NULL, entity_name TEXT, entity_id INTEGER,
    via_view TEXT, key_expr TEXT, descriptor TEXT, raw TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'verified'
);
CREATE TABLE interaction (
    id INTEGER PRIMARY KEY, member_id INTEGER NOT NULL, line_no INTEGER NOT NULL,
    kind TEXT NOT NULL, target TEXT, fields TEXT
);
"""


def _make_old_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_SCHEMA)
    conn.close()


def test_connect_adds_missing_columns_to_a_pre_existing_database(tmp_path):
    path = tmp_path / "old.db"
    _make_old_db(path)

    conn = db_mod.connect(path)
    rc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(rule_candidate)").fetchall()}
    da_cols = {r["name"] for r in conn.execute("PRAGMA table_info(data_access)").fetchall()}
    int_cols = {r["name"] for r in conn.execute("PRAGMA table_info(interaction)").fetchall()}
    assert "pair_line_no" in rc_cols
    assert "key_source_line" in da_cols
    assert "key_source_expr" in da_cols
    assert "dynamic" in int_cols


def test_connect_migration_lets_new_columns_be_inserted_on_an_old_db(tmp_path):
    path = tmp_path / "old.db"
    _make_old_db(path)

    conn = db_mod.connect(path)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'X', 'mantis')")
    # Would raise sqlite3.OperationalError: no such column, pre-migration.
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, construct, raw, pair_line_no) "
        "VALUES (1, 1, 'ELSE', 'x', 1)"
    )
    conn.execute(
        "INSERT INTO data_access (member_id, line_no, verb, crud, raw, key_source_line, key_source_expr) "
        "VALUES (1, 2, 'GET', 'R', 'x', 1, 'expr')"
    )
    conn.execute(
        "INSERT INTO interaction (member_id, line_no, kind, target, dynamic) "
        "VALUES (1, 3, 'CONVERSE', 'SCREEN1', 1)"
    )
    row = conn.execute("SELECT pair_line_no FROM rule_candidate").fetchone()
    assert row["pair_line_no"] == 1
    row = conn.execute("SELECT dynamic FROM interaction").fetchone()
    assert row["dynamic"] == 1


def test_connect_migration_backfills_existing_interaction_rows_with_default(tmp_path):
    """An `interaction` row inserted before the `dynamic` column existed
    must read back as 0 (the schema's own default), not NULL or an error
    -- the retrofit case a brand-new-row test can't exercise, since that
    row was never written pre-migration."""
    path = tmp_path / "old.db"
    _make_old_db(path)
    pre_conn = sqlite3.connect(path)
    pre_conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'X', 'mantis')")
    pre_conn.execute(
        "INSERT INTO interaction (member_id, line_no, kind, target) "
        "VALUES (1, 1, 'CONVERSE', 'SCREEN1')"
    )
    pre_conn.commit()
    pre_conn.close()

    conn = db_mod.connect(path)
    row = conn.execute("SELECT dynamic FROM interaction WHERE target='SCREEN1'").fetchone()
    assert row["dynamic"] == 0


def test_connect_migration_is_a_no_op_on_a_brand_new_database(tmp_path):
    """A database created fresh (via SCHEMA's own CREATE TABLE) already has
    every column -- the migration step must not error or duplicate a
    column on the ordinary, already-current case."""
    path = tmp_path / "new.db"
    conn = db_mod.connect(path)
    conn2 = db_mod.connect(path)  # re-`connect()` to the same, already-migrated db
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(rule_candidate)").fetchall()}
    assert "pair_line_no" in cols


def test_rule_theme_table_exists_with_expected_columns(tmp_path):
    from mfdoc.db import connect

    conn = connect(tmp_path / "index.db")
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(rule_theme)")}
    assert cols == {"id", "rule_candidate_id", "theme", "source"}


def test_rule_theme_unique_per_rule_candidate(tmp_path):
    from mfdoc.db import connect

    conn = connect(tmp_path / "index.db")
    conn.execute(
        "INSERT INTO member (id, name, dialect) VALUES (1, 'X', 'natural')"
    )
    conn.execute(
        "INSERT INTO rule_candidate (id, member_id, line_no, construct, raw) "
        "VALUES (1, 1, 10, 'IF', 'IF X')"
    )
    conn.execute(
        "INSERT INTO rule_theme (rule_candidate_id, theme, source) VALUES (1, 'eligibility', 'keyword')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rule_theme (rule_candidate_id, theme, source) VALUES (1, 'posting', 'llm')"
        )
        conn.commit()


def test_purge_member_facts_removes_orphaned_rule_theme_rows(tmp_path):
    """Regression test: rule_theme carries no member_id of its own -- only
    rule_candidate_id, a foreign key into a table purge_member_facts DOES
    delete per-member. Without an explicit cleanup, deleting a member's
    rule_candidate rows left their rule_theme rows behind, orphaned; if a
    later re-ingest let SQLite reuse one of those freed rowids for an
    unrelated new rule_candidate, the stale theme would silently reattach
    to it."""
    from mfdoc.db import connect, purge_member_facts

    conn = connect(tmp_path / "index.db")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'X', 'natural')")
    conn.execute(
        "INSERT INTO rule_candidate (id, member_id, line_no, construct, raw) "
        "VALUES (1, 1, 10, 'IF', 'IF X')"
    )
    conn.execute(
        "INSERT INTO rule_theme (rule_candidate_id, theme, source) VALUES (1, 'eligibility', 'keyword')"
    )
    conn.commit()

    purge_member_facts(conn, 1)
    conn.commit()

    remaining = conn.execute("SELECT COUNT(*) FROM rule_theme").fetchone()[0]
    assert remaining == 0, "rule_theme rows must not survive their rule_candidate's deletion"
