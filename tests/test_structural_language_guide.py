"""Tests for structural.language_guide() -- the basic-tier renderer for
the language-guide document type (issue #64)."""

from __future__ import annotations

import re

import pytest

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
    assert "mfdoc calibrate --config project.yml --dialect natural" in appendix


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


def test_regeneration_hints_include_config_flag(indexed_db):
    """Both regeneration commands suggested in the document body must be
    directly copy-pasteable -- `mfdoc lang-guide` and `mfdoc calibrate` both
    require `--config`, same as every other regenerate-with hint in this
    repo's own generated docs (see README/CLAUDE.md's command examples)."""
    out = structural.language_guide(indexed_db, "natural")
    assert "mfdoc lang-guide --config project.yml --dialect natural" in out
    assert "mfdoc calibrate --config project.yml --dialect natural" in out


def test_appendix_sample_is_redacted():
    """The 'not yet recognized' appendix's sample line comes straight from
    a gap's raw source text -- the same class of sensitive literal the
    rest of the language guide redacts -- so it must go through the given
    Redactor too, not render verbatim."""
    import sqlite3

    from mfdoc.db import SCHEMA, add_gap

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (name, dialect) VALUES ('SECRETPROG', 'natural')")
    member_id = conn.execute("SELECT id FROM member WHERE name='SECRETPROG'").fetchone()["id"]
    add_gap(
        conn, "unparsed_line", "unparsed",
        member_id=member_id, raw="FOOKW ACCTNO '999-88-7777'",
    )
    conn.commit()

    redact = Redactor(patterns=[r"\d{3}-\d{2}-\d{4}"], enabled=True)
    out = structural.language_guide(conn, "natural", redact=redact)
    assert "999-88-7777" not in out
    assert "[REDACTED]" in out
    assert "`FOOKW`" in out


def test_language_guide_cli(cli_args, derive_result):
    from types import SimpleNamespace

    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, dialect="natural", out=None)
    assert cli.cmd_lang_guide(args) == 0


def test_language_guide_rejects_non_dialect_shaped_input(indexed_db):
    """`dialect` is interpolated directly into YAML front matter/headings,
    with no citation or Redactor step of its own -- a value containing a
    quote or newline must be rejected outright, not passed through into
    the generated document's front matter."""
    for bad in ('natural"\ntitle: injected', "natural\nkey: value", "", "Natural", "natural-1"):
        with pytest.raises(ValueError):
            structural.language_guide(indexed_db, bad)


def test_lang_guide_cli_rejects_unknown_dialect(capsys):
    """The CLI's own --dialect must reject an unknown dialect id at parse
    time (same known set ingest/derive use), not just at render time."""
    from mfdoc import cli

    with pytest.raises(SystemExit):
        cli.main(["lang-guide", "--config", "x.yml", "--dialect", "not-a-real-dialect"])
    assert "invalid choice" in capsys.readouterr().err
