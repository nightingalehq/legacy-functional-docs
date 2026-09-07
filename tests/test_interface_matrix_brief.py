"""Guards on brief.interface_matrix_brief -- the fact brief behind the
screen-and-key interface matrix document type (mode x panel x map x
PF-label x routine x outcome), issue #91.

Synthetic facts for the correlation-logic unit tests (invented field/member
names, not any real site's data -- see CLAUDE.md), the same style
test_structural_dispatch_map.py already uses for dispatch_edges_for_member,
since what's under test here is largely the same kind of derived-table scan.
The last two tests run the brief against the repo's own bundled
examples/inputs fixture (via the `indexed_db` fixture already used by
test_brief.py) to confirm it behaves sanely at whole-system scope.
"""

from __future__ import annotations

import re
import sqlite3

from mfdoc.brief import interface_matrix_brief
from mfdoc.db import SCHEMA, insert
from mfdoc.redact import NULL_REDACTOR


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _member(conn, mid, name, object_type=None, dialect="natural"):
    conn.execute(
        "INSERT INTO member (id, name, dialect, object_type) VALUES (?, ?, ?, ?)",
        (mid, name, dialect, object_type),
    )


def _rc(conn, member_id, line_no, construct, condition=None, fields_used=None,
        literals=None, depth=0, end_line=None, pair_line_no=None):
    return insert(
        conn, "rule_candidate", member_id=member_id, line_no=line_no, construct=construct,
        condition=condition, raw=condition or construct, depth=depth,
        fields_used=fields_used, literals=literals, end_line=end_line,
        pair_line_no=pair_line_no,
    )


def test_screen_with_no_dispatch_edges_is_omitted():
    """A screen that's displayed but never involved in a PF-key branch has
    nothing for the matrix to show -- it must not render an empty section."""
    conn = _conn()
    _member(conn, 1, "MMP0100")
    insert(conn, "interaction", member_id=1, line_no=5, kind="INPUT", target="MMM0100")

    out = interface_matrix_brief(conn, redact=NULL_REDACTOR)
    assert "MMM0100" not in out
    assert "No screen" in out


def test_screen_reachable_from_a_dispatching_module_is_surfaced():
    conn = _conn()
    _member(conn, 1, "MMP0100")
    insert(conn, "interaction", member_id=1, line_no=5, kind="INPUT", target="MMM0100")
    _rc(conn, 1, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="MMP9999", call_kind="FETCH",
           dynamic=0, line_no=11)

    out = interface_matrix_brief(conn, redact=NULL_REDACTOR)
    assert "MMM0100" in out
    assert "MMP0100" in out
    assert "PF3" in out
    assert "MMP9999" in out
    assert "[[MMP0100:5]]" in out, "the display point itself must be cited"


def test_two_modules_displaying_the_same_screen_both_listed_as_reachable_from():
    """Multiple modules displaying one screen are the closest fact-store
    proxy for "which mode(s) reach this screen" -- both must be listed,
    not just whichever module happened to have the dispatch branch."""
    conn = _conn()
    _member(conn, 1, "MMP0100")
    _member(conn, 2, "MMP0200")
    insert(conn, "interaction", member_id=1, line_no=5, kind="INPUT", target="MMM0100")
    insert(conn, "interaction", member_id=2, line_no=7, kind="INPUT", target="MMM0100")
    _rc(conn, 1, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="MMP9999", call_kind="FETCH",
           dynamic=0, line_no=11)

    out = interface_matrix_brief(conn, redact=NULL_REDACTOR)
    assert "### Reachable from" in out
    assert "`MMP0100`" in out and "`MMP0200`" in out


def test_natural_map_text_surfaces_as_a_candidate_pf_key_label():
    conn = _conn()
    _member(conn, 1, "MMP0100")
    _member(conn, 2, "MMM0100", object_type="map")
    insert(conn, "interaction", member_id=1, line_no=5, kind="INPUT", target="MMM0100")
    insert(conn, "interaction", member_id=2, line_no=20, kind="MAP_TEXT", fields="PF3=Exit")
    _rc(conn, 1, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="MMP9999", call_kind="FETCH",
           dynamic=0, line_no=11)

    out = interface_matrix_brief(conn, redact=NULL_REDACTOR)
    assert "Candidate PF-key labels" in out
    assert "PF3=Exit" in out
    assert "[[MMM0100:20]]" in out


def test_mantis_screen_heading_field_surfaces_as_a_candidate_label():
    """Mantis screens are recorded as `mantis_map` entities (dialects/screen.py),
    not as a member with its own source lines the way a Natural map is --
    the label lookup must handle both shapes."""
    conn = _conn()
    _member(conn, 1, "ORDPGM01", dialect="mantis")
    _member(conn, 2, "ORDSCR1", dialect="mantis_screen")
    insert(conn, "interaction", member_id=1, line_no=8, kind="CONVERSE", target="ORDSCR1")
    conn.execute(
        "INSERT INTO entity (name, kind, defined_in, defined_line) VALUES (?, ?, ?, ?)",
        ("ORDSCR1", "mantis_map", 2, 1),
    )
    entity_id = conn.execute("SELECT id FROM entity WHERE name='ORDSCR1'").fetchone()["id"]
    insert(conn, "entity_field", entity_id=entity_id, name="PF3=Exit", format="HEADING",
           defined_line=12)
    _rc(conn, 1, 10, "IF", condition="#MENU-OPTION = '3'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="ORDMENU", call_kind="CALL",
           dynamic=0, line_no=11)

    out = interface_matrix_brief(
        conn, redact=NULL_REDACTOR, dispatch_field=re.compile(r"#MENU-OPTION"),
    )
    assert "ORDSCR1" in out
    assert "PF3=Exit" in out
    assert "[[ORDSCR1:12]]" in out


def test_no_screens_at_all_says_so_rather_than_rendering_an_empty_document():
    conn = _conn()
    out = interface_matrix_brief(conn, redact=NULL_REDACTOR)
    assert "No screen" in out


def test_interface_matrix_brief_runs_cleanly_against_the_bundled_fixtures(indexed_db):
    """Whole-system smoke test against the repo's own examples/inputs --
    must not raise, and every citation it emits must be well-formed."""
    out = interface_matrix_brief(indexed_db, redact=NULL_REDACTOR)
    assert out.startswith("# Fact brief: screen-and-key interface matrix")
    for cite in re.findall(r"\[\[[^\]]+\]\]", out):
        assert re.match(r"\[\[[A-Za-z0-9#@$&\-_.]+(:\d+(-\d+)?)?\]\]", cite), cite


def test_interface_matrix_cli(cli_args, derive_result):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(
        config=cli_args.config, module=None, entity=None, system=False,
        executive=None, interface_matrix=True, out=None,
    )
    assert cli.cmd_brief(args) == 0
