"""Tests for structural.glossary() renderer."""

from __future__ import annotations

from mfdoc import structural


def test_every_entity_appears_once(indexed_db):
    conn = indexed_db
    expected_names = {
        r["name"] for r in conn.execute("SELECT DISTINCT name FROM entity").fetchall()
    }
    out = structural.glossary(indexed_db)
    for name in expected_names:
        assert out.count(f"### {name}") == 1, f"{name} should appear exactly once"


def test_field_rows_nested_under_their_entity(indexed_db):
    conn = indexed_db
    row = conn.execute("SELECT id, name FROM entity LIMIT 1").fetchone()
    field = conn.execute(
        "SELECT name FROM entity_field WHERE entity_id=? LIMIT 1", (row["id"],)
    ).fetchone()
    out = structural.glossary(conn)
    if field is not None:
        entity_section = out.split(f"### {row['name']}")[1].split("### ", 1)[0]
        assert field["name"] in entity_section


def test_glossary_cli(cli_args, derive_result):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, out=None)
    assert cli.cmd_glossary(args) == 0
