"""Guards on the Mantis screen/map painter-export parser (dialects/screen.py).

Uses a synthetic export shaped like the real report format (header line,
column-header rows, a field table, a trailing ASCII picture of the laid-out
screen) with invented field/screen names -- not any real site's export --
since the format itself, not any particular screen's content, is what this
parser needs to survive.
"""

from __future__ import annotations

import sqlite3

from mfdoc.db import SCHEMA
from mfdoc.dialects import screen

SAMPLE = '''  NAME: FAKESCR1                          DESCRIPTION: SAMPLE TEST SCREEN                              PASSWORD: APPTT
  FRMT: NEW   MASK: #   BLANK FILL: |   FULL DISPLAY: YES   PROT BOT LINE: NO    ALARM: NO    OPAQUE: NO     DOMAIN: (24,80)
                                 DATA     FLD-POS  FLD  VER-REP HOR-REP  PRO AUT INS MOD PEN REV                 UPP       --BOX--
  ---------FIELD NAME----------- TYPE---  ROW COL  LEN  OCC DIS OCC DIS  TCT SKP CUR TAG DET VID BLI HIG INT CLR CAS SO/SI O L R U
  MESSAGE                        TEXT       1   2   10                   YES                             BRI NO
  "WIDGET STATUS SCREEN HEADING"  HEADING    1  40   23                   YES                             BRI NEU                 Y
  WIDGET_WEIGHT                  NUMERIC    5  15    5                       YES                         NOR GRE                 Y
  NEXT_ITEM                      TEXT       8   6   10   12   1          YES                             NOR TUR
  UNUSED_FIELD_A                 TEXT       9   2    5                                                   NOR GRE                 Y
  UNUSED_FIELD_B                 NUMERIC   10   2    5                                                   NOR GRE                 Y

    ....+....1....+....2....+....3....+....4....+....5....+....6....+....7....+....8
  .  ##########       ####################  WIDGET STATUS SCREEN HEADING            .
  +  # ##### ## # ##.## # #### # ##.## ##.## # ########## ##.## # #### # ##.## ##.## +
'''


def _extract(src: str = SAMPLE, member_name: str = "FAKESCR1"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, ?, 'mantis_screen')", (member_name,))
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    stats = screen.extract(conn, 1, lines, member_name)
    return conn, stats


def test_header_description_becomes_entity_notes():
    conn, _ = _extract()
    row = conn.execute("SELECT notes FROM entity WHERE name='FAKESCR1'").fetchone()
    assert row is not None
    assert row["notes"] == "SAMPLE TEST SCREEN"


def test_data_fields_are_recorded_with_position_and_type():
    conn, stats = _extract()
    eid = conn.execute("SELECT id FROM entity WHERE name='FAKESCR1'").fetchone()["id"]
    row = conn.execute(
        "SELECT format, length, remark FROM entity_field WHERE entity_id=? AND name='MESSAGE'", (eid,)
    ).fetchone()
    assert row is not None
    assert row["format"] == "TEXT"
    assert row["length"] == "10"
    assert row["remark"] == "row 1 col 2"
    assert stats["fields"] == 5  # MESSAGE, WIDGET_WEIGHT, NEXT_ITEM, UNUSED_FIELD_A/B
    assert stats["headings"] == 1


def test_repeating_field_captures_occurrence_count():
    conn, _ = _extract()
    eid = conn.execute("SELECT id FROM entity WHERE name='FAKESCR1'").fetchone()["id"]
    row = conn.execute(
        "SELECT occurrences FROM entity_field WHERE entity_id=? AND name='NEXT_ITEM'", (eid,)
    ).fetchone()
    assert row["occurrences"] == "12"


def test_heading_literal_recorded_but_distinct_from_data_fields():
    """A HEADING row's "name" is the literal text itself (quotes stripped,
    even when the export truncated the closing quote) -- present in the
    inventory for completeness, but format='HEADING' is what lets
    graph.unused_entity_fields_for_member exclude it from "unused field"
    analysis (it isn't a referenceable identifier)."""
    conn, _ = _extract()
    eid = conn.execute("SELECT id FROM entity WHERE name='FAKESCR1'").fetchone()["id"]
    row = conn.execute(
        "SELECT format FROM entity_field WHERE entity_id=? AND name='WIDGET STATUS SCREEN HEADING'",
        (eid,),
    ).fetchone()
    assert row is not None
    assert row["format"] == "HEADING"


def test_truncated_closing_quote_still_parses_the_name():
    """The real export format truncates a HEADING literal longer than the
    fixed-width name column, sometimes losing the closing quote entirely --
    anchoring on the TYPE keyword (not fixed columns) must still recover
    the name up to that point rather than failing to match at all."""
    src = (
        '  NAME: FAKESCR2                          DESCRIPTION: X                    PASSWORD: X\n'
        '  "<-----TRUNCATED HEADING WITH NO CLOSE   HEADING    3   2   40                   YES\n'
    )
    conn, stats = _extract(src, "FAKESCR2")
    eid = conn.execute("SELECT id FROM entity WHERE name='FAKESCR2'").fetchone()["id"]
    row = conn.execute("SELECT name FROM entity_field WHERE entity_id=?", (eid,)).fetchone()
    assert row is not None
    assert row["name"] == "<-----TRUNCATED HEADING WITH NO CLOSE"
    assert stats["headings"] == 1


def test_ascii_layout_picture_lines_are_skipped_without_gapping():
    """The trailing ASCII picture of the laid-out screen carries no
    field-level fact -- must not raise a gap per line (that would drown
    the "0 fields recognised" signal that actually matters in noise)."""
    conn, _ = _extract()
    gaps = conn.execute("SELECT COUNT(*) AS n FROM gap WHERE member_id=1").fetchone()["n"]
    assert gaps == 0


def test_no_fields_recognised_raises_a_high_severity_gap():
    conn, stats = _extract(
        '  NAME: EMPTYSCR                          DESCRIPTION: X                    PASSWORD: X\n'
        '  nothing recognisable here at all\n',
        "EMPTYSCR",
    )
    assert stats["fields"] == 0
    gap = conn.execute(
        "SELECT severity FROM gap WHERE member_id=1 AND gap_kind='unparsed_line'"
    ).fetchone()
    assert gap is not None
    assert gap["severity"] == "high"
