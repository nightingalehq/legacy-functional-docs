"""Guards on coverage-history persistence (issue #85): `mfdoc coverage` and
`mfdoc gate` must each append a timestamped snapshot of the coverage metrics
they compute to the fact store, and `mfdoc coverage --history` must be able
to read that trend back -- without those two behaviours, drift/improvement
across a multi-week engagement is only visible if someone tracks it by hand
outside the tool.
"""

from __future__ import annotations

import json

from mfdoc import cli, db


def _args(config, **extra):
    return type("Args", (), {"config": str(config), **extra})()


def test_record_and_read_coverage_history_round_trip(indexed_db):
    indexed_db.commit()
    before = len(db.coverage_history(indexed_db))
    cov = {"line_recognition_rate": 0.9, "call_resolution_rate": 0.5,
           "entity_definition_rate": 0.7, "gaps_high": 3, "gaps_total": 10}
    db.record_coverage_history(indexed_db, cov, source="coverage")
    indexed_db.commit()

    history = db.coverage_history(indexed_db)
    assert len(history) == before + 1
    latest = history[-1]
    assert latest["source"] == "coverage"
    assert latest["line_recognition_rate"] == 0.9
    assert latest["gaps_total"] == 10
    assert latest.get("recorded_at")


def test_coverage_history_limit_keeps_most_recent_oldest_first(indexed_db):
    indexed_db.commit()
    for i in range(3):
        db.record_coverage_history(indexed_db, {"gaps_total": i}, source="coverage")
    indexed_db.commit()

    history = db.coverage_history(indexed_db, limit=2)
    assert len(history) == 2
    # oldest-first even when limited to the most recent rows
    assert history[0]["gaps_total"] <= history[1]["gaps_total"]


def test_coverage_history_limit_zero_returns_no_rows(indexed_db):
    """limit=0 must be treated as 'zero rows', not as falsy-therefore-
    unlimited -- a caller passing a computed limit of 0 should get an
    empty result, not every row in the table."""
    indexed_db.commit()
    db.record_coverage_history(indexed_db, {"gaps_total": 1}, source="coverage")
    indexed_db.commit()
    assert db.coverage_history(indexed_db, limit=0) == []


def test_cmd_coverage_appends_a_history_row(project_config, indexed_db):
    indexed_db.commit()
    before = len(db.coverage_history(indexed_db))
    rc = cli.cmd_coverage(_args(project_config))
    assert rc == 0
    indexed_db.commit()
    after = db.coverage_history(indexed_db)
    assert len(after) == before + 1
    assert after[-1]["source"] == "coverage"
    assert "line_recognition_rate" in after[-1]


def test_cmd_gate_appends_a_history_row(project_config, indexed_db):
    indexed_db.commit()
    before = len(db.coverage_history(indexed_db))
    rc = cli.cmd_gate(_args(project_config))
    assert rc in (0, 1)
    indexed_db.commit()
    after = db.coverage_history(indexed_db)
    assert len(after) == before + 1
    assert after[-1]["source"] == "gate"


def test_coverage_history_flag_prints_trend_without_recording_a_new_row(
    project_config, indexed_db, capsys
):
    indexed_db.commit()
    # seed at least one row so there's something to show
    assert cli.cmd_coverage(_args(project_config)) == 0
    indexed_db.commit()
    before = len(db.coverage_history(indexed_db))

    rc = cli.cmd_coverage(_args(project_config, history=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "line_recognition_rate" in out

    indexed_db.commit()
    after = len(db.coverage_history(indexed_db))
    assert after == before, "--history is a view; it must not itself append a new snapshot"


def test_coverage_history_flag_with_no_history_says_so(tmp_path, project_config):
    """A brand-new project (or one that's never run `mfdoc coverage`/`mfdoc
    gate`) has no history yet -- this must be reported plainly, not raise or
    print an empty/confusing table."""
    import shutil

    import yaml

    cfg = yaml.safe_load(project_config.read_text(encoding="utf-8"))
    fresh_db = tmp_path / "fresh-index.db"
    cfg["index_db"] = str(fresh_db)
    fresh_config = tmp_path / "fresh-project.yml"
    fresh_config.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    # An empty, schema-only db (no coverage runs yet).
    conn = db.connect(fresh_db)
    conn.commit()
    conn.close()

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.cmd_coverage(_args(fresh_config, history=True))
    assert rc == 0
    assert "no coverage history" in buf.getvalue().lower()
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_coverage_history_and_json_flags_are_mutually_exclusive(project_config, indexed_db, tmp_path, capsys):
    """--history is a view over past runs and computes nothing new to write
    to --json -- the combination must be rejected rather than silently
    ignoring one of the two flags."""
    indexed_db.commit()
    out_path = tmp_path / "coverage.json"
    rc = cli.cmd_coverage(_args(project_config, history=True, json=str(out_path)))
    assert rc == 2
    assert "mutually exclusive" in capsys.readouterr().err
    assert not out_path.exists()


def test_coverage_json_flag_still_works_alongside_history_persistence(project_config, indexed_db, tmp_path):
    """Guard against the new history-recording code accidentally breaking the
    existing --json output contract covered by test_coverage_cli.py."""
    indexed_db.commit()
    out_path = tmp_path / "coverage.json"
    rc = cli.cmd_coverage(_args(project_config, json=str(out_path)))
    assert rc == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert "line_recognition_rate" in data
