from __future__ import annotations

from mfdoc import structural


def test_crud_matrix_never_merges_two_different_members_sharing_a_name():
    """member.name is only unique together with (library, dialect) -- two
    distinct members can share a bare name across libraries. Grouping by
    name alone would silently merge their CRUD stats into one row (and
    make the selected dialect/library an arbitrary pick among the merged
    rows); grouping by member id must keep them as two separate rows with
    their own, correct dialect/library."""
    import sqlite3

    from mfdoc import graph
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    insert(conn, "member", name="SHARED", dialect="natural", library="LIBA")
    insert(conn, "member", name="SHARED", dialect="mantis", library="LIBB")
    mid_a = conn.execute(
        "SELECT id FROM member WHERE library='LIBA'"
    ).fetchone()["id"]
    mid_b = conn.execute(
        "SELECT id FROM member WHERE library='LIBB'"
    ).fetchone()["id"]
    insert(conn, "data_access", member_id=mid_a, line_no=10, verb="READ",
           crud="R", entity_name="ENTITY-X", raw="READ ENTITY-X")
    insert(conn, "data_access", member_id=mid_b, line_no=20, verb="STORE",
           crud="C", entity_name="ENTITY-X", raw="STORE ENTITY-X")
    conn.commit()

    rows = graph.crud_matrix(conn)
    assert len(rows) == 2
    by_library = {r["library"]: r for r in rows}
    assert by_library["LIBA"]["dialect"] == "natural"
    assert by_library["LIBA"]["crud"] == "R"
    assert by_library["LIBA"]["member_id"] == mid_a
    assert by_library["LIBB"]["dialect"] == "mantis"
    assert by_library["LIBB"]["crud"] == "C"
    assert by_library["LIBB"]["member_id"] == mid_b


def test_every_crud_matrix_row_becomes_an_edge(indexed_db):
    from mfdoc import graph

    conn = indexed_db
    rows = graph.crud_matrix(conn)
    out = structural.data_flow_diagram(conn)
    assert "```mermaid" in out and "graph LR" in out
    for row in rows:
        mod_id = structural._mermaid_id(row["module"])
        ent_id = structural._mermaid_id(row["entity"])
        assert f'{mod_id}[' in out and f'{ent_id}[' in out


def test_mermaid_id_is_injective_across_punctuation_collisions():
    """`MILL-CERT` and `MILL_CERT` (this repo's own fixture has exactly
    this collision shape: a DDM-derived entity and a SQL-derived one)
    must not collapse to the same node id -- the naive alnum-or-
    underscore substitution alone maps both to `n_MILL_CERT`, silently
    merging two distinct nodes in the rendered diagram."""
    id_a = structural._mermaid_id("MILL-CERT")
    id_b = structural._mermaid_id("MILL_CERT")
    assert id_a != id_b
    # Deterministic: same name always yields the same id, so regeneration
    # stays byte-identical.
    assert structural._mermaid_id("MILL-CERT") == id_a


def test_data_flow_diagram_has_no_gaps_when_no_data_access(tmp_path):
    from mfdoc.db import connect

    conn = connect(tmp_path / "index.db")
    out = structural.data_flow_diagram(conn)
    assert "No data access recorded" in out


def test_data_flow_cli(cli_args, derive_result):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, out=None)
    assert cli.cmd_data_flow(args) == 0
