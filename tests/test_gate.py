"""Guards on `mfdoc gate`: options.quality_gates must actually be enforced,
not just read into config and ignored, and a failure must say which gate,
by how much, and what it blocks.
"""

from __future__ import annotations

import yaml

from mfdoc import cli


def _run_gate(project_config, indexed_db, tmp_path, gates: dict) -> int:
    # cmd_gate opens its own connection to the same index_db; the shared
    # session-scoped `indexed_db` connection must not be holding an open
    # write transaction (e.g. from a prior graph.coverage() call) or the
    # new connection's schema/write statements will find the database
    # locked.
    indexed_db.commit()
    cfg = yaml.safe_load(project_config.read_text(encoding="utf-8"))
    cfg["options"]["quality_gates"] = gates
    patched = tmp_path / "gate-project.yml"
    patched.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    args = type("Args", (), {"config": str(patched)})()
    return cli.cmd_gate(args)


def test_gate_passes_when_thresholds_are_loose(project_config, indexed_db, tmp_path, capsys):
    rc = _run_gate(project_config, indexed_db, tmp_path, {
        "min_line_recognition_rate": 0.5,
        "max_high_severity_gaps": 1000,
    })
    out = capsys.readouterr().out
    assert rc == 0
    assert "all configured gates passed" in out


def test_gate_fails_and_reports_which_and_by_how_much(project_config, indexed_db, tmp_path, capsys):
    rc = _run_gate(project_config, indexed_db, tmp_path, {
        "min_call_resolution_rate": 0.99,
    })
    out = capsys.readouterr().out
    assert rc == 1
    assert "min_call_resolution_rate" in out
    assert "blocks:" in out


def test_gate_only_evaluates_configured_gates(project_config, indexed_db, tmp_path, capsys):
    """A gate absent from options.quality_gates must not be silently
    enforced -- config is the only source of truth for thresholds."""
    rc = _run_gate(project_config, indexed_db, tmp_path, {"min_line_recognition_rate": 0.5})
    out = capsys.readouterr().out
    assert rc == 0
    assert "call_resolution_rate" not in out
    assert "entity_definition_rate" not in out
