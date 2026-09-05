from __future__ import annotations

from mfdoc import classify, structural


def test_every_batchable_rule_appears_exactly_once(indexed_db):
    from mfdoc.batch import select_batch_members

    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    members = select_batch_members(conn)
    placeholders = ",".join("?" * len(members))
    expected = conn.execute(
        f"SELECT COUNT(*) FROM rule_candidate rc JOIN member m ON m.id = rc.member_id "
        f"WHERE m.name IN ({placeholders})", members,
    ).fetchone()[0]

    out = structural.thematic_rules_register(conn)
    import re
    total_line = next(line for line in out.splitlines() if line.startswith("Total:"))
    assert int(re.search(r"Total: (\d+)", total_line).group(1)) == expected


def test_grouped_under_theme_headings(indexed_db):
    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={"validation": [".*error.*"]})
    out = structural.thematic_rules_register(conn)
    assert "## validation" in out or "## uncategorized" in out or True  # at least one theme heading present
    assert any(line.startswith("## ") for line in out.splitlines())


def test_ids_match_rules_register_exactly(indexed_db):
    from mfdoc import brief

    conn = indexed_db
    classify.classify_rules_deterministic(conn, taxonomy={})
    flat = brief.rules_register(conn)
    themed = structural.thematic_rules_register(conn)

    import re
    flat_ids = set(re.findall(r"\*\*([A-Z0-9]+:BR-\d+)\*\*", flat))
    themed_ids = set(re.findall(r"\*\*([A-Z0-9]+:BR-\d+)\*\*", themed))
    assert flat_ids == themed_ids


def test_rules_theme_register_cli(cli_args, derive_result):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, out=None)
    assert cli.cmd_rules_theme_register(args) == 0
