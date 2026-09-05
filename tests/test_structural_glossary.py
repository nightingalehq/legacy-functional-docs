"""Tests for structural.glossary() renderer."""

from __future__ import annotations

from mfdoc import structural


def test_every_entity_appears_once(indexed_db):
    """A bare name is only unique together with kind (entity has no
    UNIQUE(name) -- see UNIQUE(name, kind) discipline enforced by
    upsert_entity in db.py). A name with only one kind still gets exactly
    one heading; a name shared by >1 kind (this repo's own fixture has
    `MILL-ORDER` as both `ddm` and `adabas_file`) gets one
    kind-disambiguated heading per kind instead."""
    conn = indexed_db
    expected_pairs = {
        (r["name"], r["kind"]) for r in conn.execute("SELECT DISTINCT name, kind FROM entity").fetchall()
    }
    names_with_multiple_kinds = {
        name for name, kind in expected_pairs
        if len({k for n, k in expected_pairs if n == name}) > 1
    }
    out = structural.glossary(indexed_db)
    for name, kind in expected_pairs:
        heading = f"### {name} ({kind})" if name in names_with_multiple_kinds else f"### {name}"
        assert out.count(heading) == 1, f"{heading} should appear exactly once"


def test_duplicate_named_entity_both_kinds_appear(indexed_db):
    """Regression test: glossary() used to dedupe on bare name alone
    (`seen_names`), silently dropping every entity row but the first one
    `ORDER BY name` happened to return for a name shared across kinds.
    This repo's own fixture has exactly that shape -- `MILL-ORDER` exists
    both as a `ddm` (with notes and DDM-style field names) and as an
    `adabas_file` (with Adabas short-name fields, no notes) -- so both
    must appear, not just whichever kind sorted first."""
    conn = indexed_db
    kinds = {
        r["kind"] for r in conn.execute(
            "SELECT DISTINCT kind FROM entity WHERE name='MILL-ORDER'"
        ).fetchall()
    }
    assert len(kinds) > 1, "fixture must have MILL-ORDER under >1 kind for this test to be meaningful"

    out = structural.glossary(indexed_db)
    for kind in kinds:
        assert f"### MILL-ORDER ({kind})" in out, f"MILL-ORDER ({kind}) missing from glossary"
    # the ddm block's distinguishing notes/fields must survive alongside
    # the adabas_file block's, not be dropped in favour of it
    assert "default sequence: AA" in out
    assert "ORDER-NO" in out and "AA" in out


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
