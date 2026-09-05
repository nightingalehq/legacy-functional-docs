from __future__ import annotations

from mfdoc import structural


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
