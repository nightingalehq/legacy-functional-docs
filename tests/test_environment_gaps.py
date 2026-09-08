"""Guards that the environment.py extractors (sql_ddl, cobol_copybook, cics_csd)
record an ``unparsed_line`` gap for input they don't recognise, instead of
silently dropping it (issue #123). All fixture content below is invented for
this test -- it is not drawn from any real DDL, copybook, or CSD listing.
"""

from __future__ import annotations

import sqlite3

from mfdoc.db import SCHEMA
from mfdoc.dialects import environment


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMEM', 'sql_ddl')")
    return conn


def _lines(text: str) -> list[tuple[int, None, str]]:
    return [(i + 1, None, t) for i, t in enumerate(text.splitlines())]


# --------------------------------------------------------------------- SQL DDL

DDL_WITH_UNRECOGNISED_COLUMN = """\
CREATE TABLE WIDGET (
    WIDGET_ID INTEGER NOT NULL,
    &BOGUS-CLAUSE!! garbage that is not a column,
    PRIMARY KEY (WIDGET_ID)
);
"""

DDL_WITH_UNRECOGNISED_STATEMENT = """\
CREATE TABLE WIDGET (
    WIDGET_ID INTEGER NOT NULL
);
CREATE VIEW WIDGET_VIEW AS SELECT * FROM WIDGET;
"""


def test_sql_ddl_unrecognised_column_records_gap():
    conn = _conn()
    environment.extract_sql_ddl(conn, 1, _lines(DDL_WITH_UNRECOGNISED_COLUMN), "FAKEMEM")
    gaps = conn.execute(
        "SELECT gap_kind, raw FROM gap WHERE member_id=1 AND gap_kind='unparsed_line'"
    ).fetchall()
    assert any("BOGUS-CLAUSE" in (g["raw"] or "") for g in gaps)


def test_sql_ddl_unrecognised_statement_records_gap():
    conn = _conn()
    environment.extract_sql_ddl(conn, 1, _lines(DDL_WITH_UNRECOGNISED_STATEMENT), "FAKEMEM")
    gaps = conn.execute(
        "SELECT gap_kind, raw FROM gap WHERE member_id=1 AND gap_kind='unparsed_line'"
    ).fetchall()
    assert any("CREATE VIEW" in (g["raw"] or "") for g in gaps)


def test_sql_ddl_recognised_input_records_no_gap():
    conn = _conn()
    clean = "CREATE TABLE WIDGET (\n    WIDGET_ID INTEGER NOT NULL\n);\n"
    environment.extract_sql_ddl(conn, 1, _lines(clean), "FAKEMEM")
    gaps = conn.execute("SELECT * FROM gap WHERE member_id=1").fetchall()
    assert gaps == []


# ---------------------------------------------------------------- COBOL copybook

COPYBOOK_WITH_UNRECOGNISED_LINE = """\
       01  WIDGET-RECORD.
           05  WIDGET-ID       PIC 9(5).
      THIS LINE IS NOT A LEVEL-NUMBER ENTRY AT ALL
           05  WIDGET-NAME     PIC X(20).
"""


def test_copybook_unrecognised_line_records_gap():
    conn = _conn()
    conn.execute("UPDATE member SET dialect='cobol_copybook' WHERE id=1")
    environment.extract_copybook(conn, 1, _lines(COPYBOOK_WITH_UNRECOGNISED_LINE), "FAKEMEM")
    gaps = conn.execute(
        "SELECT gap_kind, raw FROM gap WHERE member_id=1 AND gap_kind='unparsed_line'"
    ).fetchall()
    assert any("NOT A LEVEL-NUMBER" in (g["raw"] or "") for g in gaps)


def test_copybook_recognised_input_records_no_gap():
    conn = _conn()
    conn.execute("UPDATE member SET dialect='cobol_copybook' WHERE id=1")
    clean = "       01  WIDGET-RECORD.\n           05  WIDGET-ID       PIC 9(5).\n"
    environment.extract_copybook(conn, 1, _lines(clean), "FAKEMEM")
    gaps = conn.execute("SELECT * FROM gap WHERE member_id=1").fetchall()
    assert gaps == []


# --------------------------------------------------------------------- CICS CSD

CSD_WITH_UNRECOGNISED_LINE = """\
DEFINE TRANSACTION(WIDG) GROUP(FAKEGRP) PROGRAM(WIDGPGM1)
THIS LINE HAS NO DEFINE KEYWORD AT ALL
DEFINE PROGRAM(WIDGPGM1) GROUP(FAKEGRP) LANGUAGE(COBOL)
"""


def test_cics_csd_unrecognised_line_records_gap():
    conn = _conn()
    conn.execute("UPDATE member SET dialect='cics_csd' WHERE id=1")
    environment.extract_cics_csd(conn, 1, _lines(CSD_WITH_UNRECOGNISED_LINE), "FAKEMEM")
    gaps = conn.execute(
        "SELECT gap_kind, raw FROM gap WHERE member_id=1 AND gap_kind='unparsed_line'"
    ).fetchall()
    assert any("NO DEFINE KEYWORD" in (g["raw"] or "") for g in gaps)


def test_cics_csd_recognised_input_records_no_gap():
    conn = _conn()
    conn.execute("UPDATE member SET dialect='cics_csd' WHERE id=1")
    clean = "DEFINE PROGRAM(WIDGPGM1) GROUP(FAKEGRP) LANGUAGE(COBOL)\n"
    environment.extract_cics_csd(conn, 1, _lines(clean), "FAKEMEM")
    gaps = conn.execute("SELECT * FROM gap WHERE member_id=1").fetchall()
    assert gaps == []
