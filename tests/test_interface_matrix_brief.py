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


def test_ambiguous_natural_map_name_across_libraries_omits_labels_rather_than_guessing():
    """Two distinct map members sharing a bare name (only unique together
    with library+dialect, see db.py) must not have either one's MAP_TEXT
    silently attributed to this screen."""
    conn = _conn()
    _member(conn, 1, "MMP0100")
    conn.execute(
        "INSERT INTO member (id, name, dialect, object_type, library) "
        "VALUES (2, 'MMM0100', 'natural', 'map', 'LIBA')"
    )
    conn.execute(
        "INSERT INTO member (id, name, dialect, object_type, library) "
        "VALUES (3, 'MMM0100', 'natural', 'map', 'LIBB')"
    )
    insert(conn, "interaction", member_id=1, line_no=5, kind="INPUT", target="MMM0100")
    insert(conn, "interaction", member_id=2, line_no=20, kind="MAP_TEXT", fields="PF3=Exit (LIBA)")
    insert(conn, "interaction", member_id=3, line_no=20, kind="MAP_TEXT", fields="PF3=Quit (LIBB)")
    _rc(conn, 1, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="MMP9999", call_kind="FETCH",
           dynamic=0, line_no=11)

    out = interface_matrix_brief(conn, redact=NULL_REDACTOR)
    assert "MMM0100" in out
    assert "### Candidate PF-key labels" not in out
    assert "LIBA" not in out and "LIBB" not in out


def test_reachable_from_citation_only_considers_display_interaction_rows():
    """A non-display interaction row (e.g. MAP_TEXT) sharing the same
    target name as a display point must never supply the "reachable from"
    citation -- only INPUT/CONVERSE/SHOW rows describe an actual display."""
    conn = _conn()
    _member(conn, 1, "MMP0100")
    # An earlier, unrelated interaction row that happens to share the
    # screen's name as its own `target` but isn't a display kind.
    insert(conn, "interaction", member_id=1, line_no=1, kind="MAP_TEXT", target="MMM0100",
           fields="not a display point")
    insert(conn, "interaction", member_id=1, line_no=5, kind="INPUT", target="MMM0100")
    _rc(conn, 1, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="MMP9999", call_kind="FETCH",
           dynamic=0, line_no=11)

    out = interface_matrix_brief(conn, redact=NULL_REDACTOR)
    assert "[[MMP0100:5]]" in out
    assert "[[MMP0100:1]]" not in out


def test_a_pipe_in_a_dispatch_literal_does_not_corrupt_the_markdown_table():
    """A literal value containing `|` must not be read as an extra column
    delimiter, and source-derived text must be redacted like every other
    fact this brief hands over."""
    conn = _conn()
    _member(conn, 1, "MMP0100")
    insert(conn, "interaction", member_id=1, line_no=5, kind="INPUT", target="MMM0100")
    _rc(conn, 1, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=12)
    insert(conn, "call_edge", caller_id=1, callee_name="MMP9999", call_kind="FETCH",
           dynamic=0, line_no=11)
    _rc(conn, 1, 12, "ASSIGN", fields_used="#MODE", literals="'A|B'", depth=1)

    out = interface_matrix_brief(conn, redact=NULL_REDACTOR)
    assert "\\|" in out, "the literal's own pipe must be escaped, not left to break the table"
    table_lines = [l for l in out.splitlines() if l.startswith("| `MMP0100`")]
    assert table_lines, "expected a rendered matrix row"
    # 6 columns (module/trigger value/mechanism/branch/calls/fields set) ->
    # 7 real column-delimiter pipes; an escaped `\|` inside a cell's own
    # text must not be counted as one of them.
    unescaped = re.findall(r"(?<!\\)\|", table_lines[0])
    assert len(unescaped) == 7, f"unescaped pipe corrupted the row: {table_lines[0]!r}"


def test_dispatch_edges_are_computed_once_per_member_across_multiple_screens(monkeypatch):
    """A module displaying more than one screen must not re-scan its own
    rule_candidate set once per screen (dispatch_edges_for_member scans the
    whole member) -- the per-member cache must make this a single call."""
    import mfdoc.structural as structural_mod

    conn = _conn()
    _member(conn, 1, "MMP0100")
    insert(conn, "interaction", member_id=1, line_no=5, kind="INPUT", target="MMM0100")
    insert(conn, "interaction", member_id=1, line_no=7, kind="INPUT", target="MMM0200")
    _rc(conn, 1, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="MMP9999", call_kind="FETCH",
           dynamic=0, line_no=11)

    calls = []
    real = structural_mod.dispatch_edges_for_member

    def _counting(conn_, member_id, dispatch_field=None):
        calls.append(member_id)
        return real(conn_, member_id, dispatch_field=dispatch_field)

    monkeypatch.setattr(structural_mod, "dispatch_edges_for_member", _counting)

    out = interface_matrix_brief(conn, redact=NULL_REDACTOR)
    assert "MMM0100" in out and "MMM0200" in out
    assert calls == [1], f"expected exactly one scan of member 1, got {calls}"


def test_mode_field_branch_with_no_subroutine_call_is_surfaced_and_tagged():
    """Issue #129: a central mode/panel-keyed dispatch branch that acts
    inline (sets a field directly, no PERFORM/CALL of its own) is an
    entirely different mechanism from a PF-key branch that PERFORMs a
    named subroutine -- both must appear in the matrix, each tagged with
    which mechanism it came from, when `mode_field` is supplied."""
    conn = _conn()
    _member(conn, 1, "MMP0100")
    insert(conn, "interaction", member_id=1, line_no=5, kind="INPUT", target="MMM0100")

    # Subroutine-invoked PF-key branch (existing mechanism).
    _rc(conn, 1, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="MMP9999", call_kind="FETCH",
           dynamic=0, line_no=11)

    # Inline mode/panel dispatch branch: no call at all, just sets a field.
    _rc(conn, 1, 20, "IF", condition="#MODE = 'INQUIRE'", depth=0, end_line=21)
    _rc(conn, 1, 21, "ASSIGN", fields_used="#PANEL-STATE", literals="'LOCKED'", depth=1)

    out = interface_matrix_brief(
        conn, redact=NULL_REDACTOR, mode_field=re.compile(r"#MODE\b"),
    )

    assert "| PF-key dispatch |" in out
    assert "| mode/panel dispatch |" in out
    assert "INQUIRE" in out
    assert "#PANEL-STATE" in out and "LOCKED" in out
    assert "[[MMP0100:20" in out, "the inline mode/panel branch itself must be cited"


def test_mode_field_omitted_when_not_configured():
    """Without `mode_field`, a mode/panel-keyed branch on a field other than
    the PF-key dispatch field must not appear -- no built-in guess at what a
    project's mode/panel field is named (see conditions.mode_field_from_options)."""
    conn = _conn()
    _member(conn, 1, "MMP0100")
    insert(conn, "interaction", member_id=1, line_no=5, kind="INPUT", target="MMM0100")
    _rc(conn, 1, 10, "IF", condition="*PF-KEY = 'PF3'", depth=0, end_line=11)
    insert(conn, "call_edge", caller_id=1, callee_name="MMP9999", call_kind="FETCH",
           dynamic=0, line_no=11)
    _rc(conn, 1, 20, "IF", condition="#MODE = 'INQUIRE'", depth=0, end_line=21)
    _rc(conn, 1, 21, "ASSIGN", fields_used="#PANEL-STATE", literals="'LOCKED'", depth=1)

    out = interface_matrix_brief(conn, redact=NULL_REDACTOR)
    assert "| PF-key dispatch |" in out
    assert "| mode/panel dispatch |" not in out
    assert "INQUIRE" not in out
