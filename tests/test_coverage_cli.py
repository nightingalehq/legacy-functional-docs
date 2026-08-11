"""Guard on `mfdoc coverage --json` (cli.py's cmd_coverage), distinct from
test_coverage_snapshot.py's direct graph.coverage() checks -- this is about
the CLI's own --json wiring, since stdout mixes the coverage JSON with a
non-JSON gap-breakdown block on the same stream and isn't itself
machine-parseable.
"""

from __future__ import annotations

import json

from mfdoc import cli


def test_coverage_json_flag_writes_clean_json(project_config, indexed_db, tmp_path):
    indexed_db.commit()
    out_path = tmp_path / "coverage.json"
    args = type("Args", (), {"config": str(project_config), "json": str(out_path)})()
    rc = cli.cmd_coverage(args)
    assert rc == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert "line_recognition_rate" in data


def test_coverage_without_json_flag_does_not_require_the_attribute(project_config, indexed_db, capsys):
    """A caller building a bare args object without --json (an older
    script, a notebook) must keep working exactly as before this flag
    existed, not raise AttributeError."""
    indexed_db.commit()
    args = type("Args", (), {"config": str(project_config)})()
    rc = cli.cmd_coverage(args)
    assert rc == 0
    assert "line_recognition_rate" in capsys.readouterr().out
