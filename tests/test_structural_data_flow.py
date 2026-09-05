from __future__ import annotations

from mfdoc import structural


def test_every_crud_matrix_row_becomes_an_edge(indexed_db):
    from mfdoc import graph

    conn = indexed_db
    rows = graph.crud_matrix(conn)
    out = structural.data_flow_diagram(conn)
    assert "```mermaid" in out and "graph LR" in out
    for row in rows:
        module_node = row["module"].replace(" ", "_")
        entity_node = row["entity"].replace(" ", "_")
        assert f"{module_node}" in out and f"{entity_node}" in out


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
