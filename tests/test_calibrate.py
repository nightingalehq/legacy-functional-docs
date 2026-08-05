"""Guards on `mfdoc calibrate`: it must promote the unparsed-line shape
analysis into something that actually ranks by frequency and points at a
file to fix, not just print raw gap rows.
"""

from __future__ import annotations

from mfdoc import cli


def _run_calibrate(project_config, indexed_db, dialect, capsys):
    indexed_db.commit()  # release any open write transaction; see test_gate.py
    args = type("Args", (), {"config": str(project_config), "dialect": dialect, "top": 30})()
    rc = cli.cmd_calibrate(args)
    return rc, capsys.readouterr().out


def test_calibrate_ranks_unparsed_shapes_for_natural(project_config, indexed_db, capsys):
    """RESET is now recognised (issue 4.11), so MMP0100 no longer has an
    unparsed line. MMP9000's own continuation-fold fixture still has one by
    design -- its "AND ..." line is folded correctly into the preceding IF
    but is also visited (and not recognised) on its own once folded; see
    that fixture's comment. The command must surface it, labelled by its
    leading keyword."""
    rc, out = _run_calibrate(project_config, indexed_db, "natural", capsys)
    assert rc == 0
    assert "AND" in out
    assert "src/mfdoc/dialects/natural.py" in out


def test_calibrate_reports_cleanly_when_dialect_has_no_gaps(project_config, indexed_db, capsys):
    rc, out = _run_calibrate(project_config, indexed_db, "cics_csd", capsys)
    assert rc == 0
    assert "no unparsed_line gaps" in out
