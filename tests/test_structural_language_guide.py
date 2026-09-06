"""Tests for structural.language_guide() -- the basic-tier renderer for
the language-guide document type (issue #64)."""

from __future__ import annotations

import re

from mfdoc import structural
from mfdoc.redact import Redactor


def test_front_matter_and_section_order(indexed_db):
    out = structural.language_guide(indexed_db, "natural")
    assert out.startswith("---\n")
    assert 'title: "natural — language guide"' in out
    assert "doc_type: register" in out

    headings = [
        "## Structure / declarations", "## Control flow", "## Data access (DML)",
        "### Entity relationships", "## Screen interaction", "## Transactions",
        "## Calling conventions", "## Not yet recognized",
    ]
    positions = [out.index(h) for h in headings]
    assert positions == sorted(positions), "sections must render in the documented fixed order"


def test_populated_section_renders_keyword_count_and_citation(indexed_db):
    out = structural.language_guide(indexed_db, "natural")
    control_flow = out.split("## Control flow", 1)[1].split("## Data access", 1)[0]
    assert "| keyword | count | example |" in control_flow
    assert re.search(r"\|\s*`IF`\s*\|\s*\d+\s*\|\s*\[\[", control_flow)


def test_empty_section_says_none_recorded():
    """A dialect with no member.dialect rows at all still renders every
    section, each saying 'None recorded.' rather than omitting the
    section or raising."""
    import sqlite3

    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    out = structural.language_guide(conn, "cobol_copybook")
    # 6 top-level sections + the entity_relationships subsection + the appendix
    assert out.count("None recorded.") == 8


def test_appendix_reuses_unparsed_line_shapes(indexed_db):
    out = structural.language_guide(indexed_db, "natural")
    appendix = out.split("## Not yet recognized", 1)[1]
    assert "mfdoc calibrate --dialect natural" in appendix


def test_redactor_applied_to_example_text_only():
    import sqlite3

    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (name, dialect) VALUES ('SECRETPROG', 'natural')")
    member_id = conn.execute("SELECT id FROM member WHERE name='SECRETPROG'").fetchone()["id"]
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES (?, 5, ?)",
        (member_id, "IF ACCTNO EQ '999-88-7777'"),
    )
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, construct, raw) VALUES (?, 5, 'IF', ?)",
        (member_id, "IF ACCTNO EQ '999-88-7777'"),
    )
    conn.commit()

    redact = Redactor(patterns=[r"\d{3}-\d{2}-\d{4}"], enabled=True)
    out = structural.language_guide(conn, "natural", redact=redact)
    assert "999-88-7777" not in out
    assert "[REDACTED]" in out
    # the keyword itself is never redacted
    assert "`IF`" in out


def test_language_guide_cli(cli_args, derive_result):
    from types import SimpleNamespace

    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, dialect="natural", out=None)
    assert cli.cmd_lang_guide(args) == 0
