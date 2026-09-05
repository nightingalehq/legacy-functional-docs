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


def test_ambiguous_member_not_silently_omitted():
    """Regression test: thematic_rules_register used to compute
    resolved_names (names mapping to exactly one member) and silently
    exclude every other name from the whole document -- an ambiguous
    member's real rule_candidate rows just vanished. Two members sharing
    a name across libraries, each with real rule_candidate rows, must
    both surface -- as an explicit ambiguous row under a trailing
    `## (ambiguous)` section, mirroring brief.rules_register()'s own
    refusal-row format for the identical case -- not disappear."""
    import sqlite3

    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    conn.execute(
        "INSERT INTO member (name, dialect, library, object_type) "
        "VALUES ('DUPTHEME', 'natural', 'LIBA', 'program')"
    )
    conn.execute(
        "INSERT INTO member (name, dialect, library, object_type) "
        "VALUES ('DUPTHEME', 'natural', 'LIBB', 'program')"
    )
    mid_a = conn.execute("SELECT id FROM member WHERE library='LIBA'").fetchone()["id"]
    mid_b = conn.execute("SELECT id FROM member WHERE library='LIBB'").fetchone()["id"]
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, depth, construct, raw, condition) "
        "VALUES (?, 10, 1, 'IF', 'IF X', 'X > 1')", (mid_a,)
    )
    conn.execute(
        "INSERT INTO rule_candidate (member_id, line_no, depth, construct, raw, condition) "
        "VALUES (?, 20, 1, 'IF', 'IF Y', 'Y > 1')", (mid_b,)
    )
    conn.commit()

    out = structural.thematic_rules_register(conn)
    assert "DUPTHEME" in out, "ambiguous member must not silently vanish from the document"
    assert "## (ambiguous)" in out
    assert "LIBA" in out and "LIBB" in out
    ambiguous_section = out.split("## (ambiguous)", 1)[1]
    assert "| — | `DUPTHEME` | — | — | ambiguous |" in ambiguous_section
    # The Total line must make clear ambiguous members' rules are excluded
    # from the numeric count, not silently folded in or silently ignored.
    total_line = next(line for line in out.splitlines() if line.startswith("Total:"))
    assert "ambiguous" in total_line.lower()


def test_theme_section_shows_provenance_breakdown():
    """Finding 4: thematic_rules_register() classifies each rule via
    rule_theme.source (keyword/llm/structural) but used to never surface
    that provenance anywhere in the rendered output. Three rules under one
    theme, one classified via each source, must produce a per-theme
    summary line naming all three sources and their counts."""
    import sqlite3

    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    conn.execute(
        "INSERT INTO member (name, dialect, library, object_type) "
        "VALUES ('PROVMOD', 'natural', 'LIBA', 'program')"
    )
    mid = conn.execute("SELECT id FROM member WHERE name='PROVMOD'").fetchone()["id"]

    rc_ids = []
    for line_no in (10, 20, 30):
        conn.execute(
            "INSERT INTO rule_candidate (member_id, line_no, depth, construct, raw, condition) "
            "VALUES (?, ?, 1, 'IF', 'IF X', 'X > 1')",
            (mid, line_no),
        )
        rc_ids.append(
            conn.execute(
                "SELECT id FROM rule_candidate WHERE member_id=? AND line_no=?", (mid, line_no)
            ).fetchone()["id"]
        )

    for rc_id, source in zip(rc_ids, ("keyword", "llm", "structural")):
        conn.execute(
            "INSERT INTO rule_theme (rule_candidate_id, theme, source) VALUES (?, 'posting', ?)",
            (rc_id, source),
        )
    conn.commit()

    out = structural.thematic_rules_register(conn)
    section = out.split("## posting", 1)[1].split("## ", 1)[0]
    summary_line = next(line for line in section.splitlines() if "rule(s)" in line)
    assert "3 rule(s)" in summary_line
    assert "1 keyword" in summary_line
    assert "1 llm" in summary_line
    assert "1 structural" in summary_line


def test_rules_theme_register_cli(cli_args, derive_result):
    from types import SimpleNamespace
    from mfdoc import cli

    args = SimpleNamespace(config=cli_args.config, out=None)
    assert cli.cmd_rules_theme_register(args) == 0
