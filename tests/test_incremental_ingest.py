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
from mfdoc.db import connect


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
