"""Guards on incremental ingest (issue 4.9b/#9).

A source_file whose content hasn't changed since the last ingest run must
be skipped outright rather than re-parsed and re-extracted -- re-extraction
without a purge first would duplicate every derived-fact row for that
member (upsert_member reuses the member id; the dialect extractors it
feeds into only ever INSERT). A file that *has* changed must still fully
replace its own facts, and must not disturb any other file's.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from mfdoc import cli, graph
from mfdoc.db import connect, insert
from mfdoc.dialects import natural


def _connect(args):
    # Mirror cmd_ingest's own index_db resolution -- relative to the config
    # file's directory, not the process's cwd.
    cfg = cli.load_config(args.config)
    return connect(Path(args.config).parent / cfg["index_db"])


PROGRAM_A_V1 = """\
DEFINE DATA LOCAL
1 #STATUS (A1)
1 #FLAG (A1)
END-DEFINE
MOVE 'A' TO #STATUS
END
"""

PROGRAM_A_V2 = """\
DEFINE DATA LOCAL
1 #STATUS (A1)
1 #FLAG (A1)
END-DEFINE
MOVE 'A' TO #STATUS
MOVE 'B' TO #FLAG
END
"""

PROGRAM_B = """\
DEFINE DATA LOCAL
1 #FLAG (A1)
END-DEFINE
CALLNAT 'PROGA'
END
"""

# A single unload file holding two members via a banner the natural
# splitter recognises (DEFAULT_SPLITTERS["natural"]'s "* MEMBER: name"
# form), then a rewritten version with the second member dropped entirely.
MULTI_MEMBER_V1 = """\
* MEMBER: PROGX
DEFINE DATA LOCAL
1 #A (A1)
END-DEFINE
MOVE 'A' TO #A
END
* MEMBER: PROGY
DEFINE DATA LOCAL
1 #B (A1)
END-DEFINE
MOVE 'B' TO #B
END
"""

MULTI_MEMBER_V2 = """\
* MEMBER: PROGX
DEFINE DATA LOCAL
1 #A (A1)
END-DEFINE
MOVE 'A' TO #A
END
"""


def _write_project(tmp_path, natural_dir):
    cfg = {
        "project": "Incremental ingest test",
        "system": "TEST",
        "index_db": ".mfdoc/index.db",
        "sources": [
            {
                "path": str(natural_dir),
                "glob": ["*.nsp"],
                "dialect": "natural",
                "library": "TESTLIB",
                "system": "TEST",
                "sequence_columns": "none",
            }
        ],
        "options": {"quality_gates": {}},
    }
    config_path = tmp_path / "project.yml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return config_path


def test_second_ingest_skips_every_unchanged_file(tmp_path, capsys):
    natural_dir = tmp_path / "natural"
    natural_dir.mkdir()
    (natural_dir / "PROGA.nsp").write_text(PROGRAM_A_V1, encoding="utf-8")
    (natural_dir / "PROGB.nsp").write_text(PROGRAM_B, encoding="utf-8")
    config_path = _write_project(tmp_path, natural_dir)
    args = SimpleNamespace(config=str(config_path))

    assert cli.cmd_ingest(args) == 0
    capsys.readouterr()
    assert cli.cmd_ingest(args) == 0
    out = capsys.readouterr().out
    assert "2 unchanged file(s) skipped" in out

    conn = _connect(args)
    assert conn.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 2
    # No duplicated facts from the second (skipped) run.
    assert conn.execute(
        "SELECT COUNT(*) FROM rule_candidate rc JOIN member m ON m.id=rc.member_id WHERE m.name='PROGA'"
    ).fetchone()[0] == 1


def test_coverage_identical_between_full_rebuild_and_noop_incremental_run(tmp_path):
    natural_dir = tmp_path / "natural"
    natural_dir.mkdir()
    (natural_dir / "PROGA.nsp").write_text(PROGRAM_A_V1, encoding="utf-8")
    (natural_dir / "PROGB.nsp").write_text(PROGRAM_B, encoding="utf-8")
    config_path = _write_project(tmp_path, natural_dir)
    args = SimpleNamespace(config=str(config_path))

    assert cli.cmd_ingest(args) == 0
    conn = _connect(args)
    graph.run_all(conn)
    conn.commit()
    full_rebuild_coverage = graph.coverage(conn)
    conn.close()

    assert cli.cmd_ingest(args) == 0
    conn = _connect(args)
    graph.run_all(conn)
    conn.commit()
    incremental_noop_coverage = graph.coverage(conn)

    assert incremental_noop_coverage == full_rebuild_coverage


def test_changed_file_is_reingested_without_touching_other_members(tmp_path):
    natural_dir = tmp_path / "natural"
    natural_dir.mkdir()
    (natural_dir / "PROGA.nsp").write_text(PROGRAM_A_V1, encoding="utf-8")
    (natural_dir / "PROGB.nsp").write_text(PROGRAM_B, encoding="utf-8")
    config_path = _write_project(tmp_path, natural_dir)
    args = SimpleNamespace(config=str(config_path))

    assert cli.cmd_ingest(args) == 0
    conn = _connect(args)
    proga_id_before = conn.execute("SELECT id FROM member WHERE name='PROGA'").fetchone()["id"]
    progb_id_before = conn.execute("SELECT id FROM member WHERE name='PROGB'").fetchone()["id"]
    progb_source_lines_before = conn.execute(
        "SELECT text FROM source_line WHERE member_id=? ORDER BY line_no", (progb_id_before,)
    ).fetchall()
    conn.close()

    (natural_dir / "PROGA.nsp").write_text(PROGRAM_A_V2, encoding="utf-8")
    assert cli.cmd_ingest(args) == 0

    conn = _connect(args)
    assert conn.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 2
    proga_id_after = conn.execute("SELECT id FROM member WHERE name='PROGA'").fetchone()["id"]
    progb_id_after = conn.execute("SELECT id FROM member WHERE name='PROGB'").fetchone()["id"]

    # PROGB's identity and content are untouched by PROGA's re-ingest.
    assert progb_id_after == progb_id_before
    progb_source_lines_after = conn.execute(
        "SELECT text FROM source_line WHERE member_id=? ORDER BY line_no", (progb_id_after,)
    ).fetchall()
    assert progb_source_lines_after == progb_source_lines_before

    # PROGA's own facts reflect the new content, with no stale rows from V1
    # sitting alongside the V2 extraction.
    assert proga_id_after == proga_id_before
    rule_candidates = conn.execute(
        "SELECT raw FROM rule_candidate WHERE member_id=? ORDER BY line_no", (proga_id_after,)
    ).fetchall()
    assert [r["raw"] for r in rule_candidates] == ["MOVE 'A' TO #STATUS", "MOVE 'B' TO #FLAG"]
    variables = conn.execute(
        "SELECT name FROM variable WHERE member_id=? ORDER BY line_no", (proga_id_after,)
    ).fetchall()
    assert [r["name"] for r in variables] == ["#STATUS", "#FLAG"]


def test_dialect_parser_hash_differs_between_module_contents(tmp_path, monkeypatch):
    """`cli._dialect_parser_hash` reads the actual module file(s) off disk,
    so a real edit to a dialect module's source (not just its behaviour)
    must change the hash it returns -- this is what lets the cache key
    notice a parser code change with no source-file edit at all."""
    from types import ModuleType

    cli._dialect_parser_hash.cache_clear()
    fake = ModuleType("fake_dialect_module")
    module_path = tmp_path / "fake_dialect.py"
    module_path.write_text("VERSION = 1\n", encoding="utf-8")
    fake.__file__ = str(module_path)
    monkeypatch.setitem(cli.DIALECT_PARSER_MODULES, "fake", (fake,))

    hash_before = cli._dialect_parser_hash("fake")
    cli._dialect_parser_hash.cache_clear()
    module_path.write_text("VERSION = 2\n", encoding="utf-8")
    hash_after = cli._dialect_parser_hash("fake")

    assert hash_before != hash_after
    cli._dialect_parser_hash.cache_clear()


def test_dialect_parser_hash_fails_closed_for_a_sourceless_module(monkeypatch):
    """A module `inspect.getsource()` can't read from (a genuinely
    sourceless frozen module, with no guarantee its own `__version__`, if
    any, is bumped on every code change) must never be treated as
    "unchanged" -- `_dialect_parser_hash` must return a different value on
    every call for it, so such a dialect's files are always re-parsed
    rather than risking a quieter repeat of issue #194."""
    from types import ModuleType

    cli._dialect_parser_hash.cache_clear()
    sourceless = ModuleType("fake_sourceless_module")
    # No __file__ at all -- inspect.getsource() raises TypeError for this,
    # the same as it would for a real frozen/zipimport module with no
    # source available.
    monkeypatch.setitem(cli.DIALECT_PARSER_MODULES, "sourceless", (sourceless,))

    first = cli._dialect_parser_hash("sourceless")
    cli._dialect_parser_hash.cache_clear()
    second = cli._dialect_parser_hash("sourceless")

    assert first != second
    cli._dialect_parser_hash.cache_clear()


def test_dialect_parser_code_change_invalidates_cache_without_source_edit(tmp_path, monkeypatch):
    """issue #194: a dialect parser code change, landed with zero source-file
    edits, must not be served from the stale pre-fix fact store. The
    incremental-ingest skip decision has to fold in something that changes
    when the parser's own code changes, not just the source file's content
    hash -- this exercises that via `cli._dialect_parser_hash` directly
    (real edit-and-rerun-the-suite is impractical here) while also proving
    the resulting re-parse actually reaches the fact store, by having the
    stand-in "fixed" parser record a new, distinguishable fact.
    """
    natural_dir = tmp_path / "natural"
    natural_dir.mkdir()
    (natural_dir / "PROGA.nsp").write_text(PROGRAM_A_V1, encoding="utf-8")
    config_path = _write_project(tmp_path, natural_dir)
    args = SimpleNamespace(config=str(config_path))

    # First ingest, as if the natural dialect parser were pinned at some
    # known version "v1".
    monkeypatch.setattr(cli, "_dialect_parser_hash", lambda dialect: "v1")
    assert cli.cmd_ingest(args) == 0
    conn = _connect(args)
    proga_id = conn.execute("SELECT id FROM member WHERE name='PROGA'").fetchone()["id"]
    assert conn.execute(
        "SELECT dialect_hash FROM source_file WHERE path=?",
        (str(natural_dir / "PROGA.nsp"),),
    ).fetchone()["dialect_hash"] == "v1"
    conn.close()

    # Simulate a dialect-parser bug fix landing with *no* source-file edit
    # at all: PROGA.nsp is untouched (same content, same sha256), only the
    # parser's own code -- standing in here for a real edit to natural.py --
    # changes, and its version consequently moves to "v2".
    real_extract = natural.extract

    def fixed_extract(conn, member_id, lines, member_name="?"):
        real_extract(conn, member_id, lines, member_name)
        insert(conn, "rule_candidate", member_id=member_id, line_no=1,
               construct="MOVE", raw="NEW-RULE-FROM-FIXED-PARSER")

    monkeypatch.setattr(natural, "extract", fixed_extract)
    monkeypatch.setattr(cli, "_dialect_parser_hash", lambda dialect: "v2")

    assert cli.cmd_ingest(args) == 0

    conn = _connect(args)
    assert conn.execute(
        "SELECT dialect_hash FROM source_file WHERE path=?",
        (str(natural_dir / "PROGA.nsp"),),
    ).fetchone()["dialect_hash"] == "v2"
    raws = {
        r["raw"] for r in conn.execute(
            "SELECT raw FROM rule_candidate WHERE member_id=?", (proga_id,)
        ).fetchall()
    }
    assert "NEW-RULE-FROM-FIXED-PARSER" in raws, (
        "a dialect-parser code change with no source edit must force a "
        "re-parse, not silently reuse the stale pre-fix fact store"
    )


def test_member_dropped_from_a_changed_multi_member_file_is_purged(tmp_path):
    natural_dir = tmp_path / "natural"
    natural_dir.mkdir()
    (natural_dir / "UNLOAD.nsp").write_text(MULTI_MEMBER_V1, encoding="utf-8")
    config_path = _write_project(tmp_path, natural_dir)
    args = SimpleNamespace(config=str(config_path))

    assert cli.cmd_ingest(args) == 0
    conn = _connect(args)
    assert conn.execute("SELECT COUNT(*) FROM member").fetchone()[0] == 2
    progy_id = conn.execute("SELECT id FROM member WHERE name='PROGY'").fetchone()["id"]
    conn.close()

    (natural_dir / "UNLOAD.nsp").write_text(MULTI_MEMBER_V2, encoding="utf-8")
    assert cli.cmd_ingest(args) == 0

    conn = _connect(args)
    names = {r["name"] for r in conn.execute("SELECT name FROM member").fetchall()}
    assert names == {"PROGX"}, "PROGY must be purged, not left behind as an orphaned row"
    # Nothing left owning the purged member's facts either.
    assert conn.execute(
        "SELECT COUNT(*) FROM variable WHERE member_id=?", (progy_id,)
    ).fetchone()[0] == 0
