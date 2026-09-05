"""Guards for the system-wide rules register (issue #16 / 4.10)."""

from __future__ import annotations

import re

from mfdoc import brief
from mfdoc.validate import CITATION


def test_lists_a_known_rule_with_its_module_id(indexed_db):
    """MMP0100:BR-001 is the first rule candidate module_brief would show for
    MMP0100 -- the register must carry the exact same ID, not a different
    numbering scheme."""
    conn = indexed_db
    out = brief.rules_register(conn)
    assert "**MMP0100:BR-001**" in out
    assert "`MMP0100`" in out


def test_every_citation_resolves(indexed_db):
    """The register's whole value is a human being able to click a citation
    and land on the right line -- if one doesn't resolve, this doc is worse
    than useless, since it looks authoritative."""
    conn = indexed_db
    out = brief.rules_register(conn)
    cites = list(CITATION.finditer(out))
    assert cites, "expected at least one citation in the register"
    for m in cites:
        member = m.group("member").upper()
        line = int(m.group("from"))
        row = conn.execute(
            "SELECT (SELECT MAX(line_no) FROM source_line WHERE member_id=member.id) AS maxline "
            "FROM member WHERE UPPER(name)=?", (member,)
        ).fetchone()
        assert row is not None, f"citation to unknown member {member}"
        assert 1 <= line <= row["maxline"], f"citation {m.group(0)} out of range"


def test_covers_every_rule_candidate_in_batchable_modules(indexed_db):
    """No rule candidate belonging to a batchable module may be silently
    dropped -- the register's count must match the fact store's, not an
    approximation."""
    from mfdoc.batch import select_batch_members

    conn = indexed_db
    members = select_batch_members(conn)
    placeholders = ",".join("?" * len(members))
    expected = conn.execute(
        f"SELECT COUNT(*) FROM rule_candidate rc JOIN member m ON m.id = rc.member_id "
        f"WHERE m.name IN ({placeholders})",
        members,
    ).fetchone()[0]

    out = brief.rules_register(conn)
    total_line = next(line for line in out.splitlines() if line.startswith("Total:"))
    actual = int(re.search(r"Total: (\d+)", total_line).group(1))
    assert actual == expected


def test_regeneration_is_byte_identical(indexed_db):
    """Re-running against unchanged source must reproduce the same string --
    the same guarantee `_rule_id` already gives per-module, extended to the
    whole document. No timestamp is embedded, on purpose, so this holds
    regardless of when it's regenerated."""
    conn = indexed_db
    assert brief.rules_register(conn) == brief.rules_register(conn)


def test_total_line_unchanged_when_no_ambiguous_names(indexed_db):
    """The fixture's batchable members are all unambiguous -- the Total
    line's wording must stay exactly what it was before this disclosure was
    added, so unrelated fixtures (this repo's checked-in
    examples/outputs/docs/rules-register.md included) keep regenerating
    byte-identically."""
    conn = indexed_db
    out = brief.rules_register(conn)
    total_line = next(line for line in out.splitlines() if line.startswith("Total:"))
    assert re.fullmatch(r"Total: \d+ rule candidate\(s\) across \d+ batchable module\(s\)\.", total_line)


def test_total_line_discloses_ambiguous_exclusion():
    """An ambiguous-named member's rule_candidate rows are rendered as an
    explicit `ambiguous` row (see the loop above) but never counted into
    `total` -- the Total line must say so, mirroring the disclosure
    `structural.thematic_rules_register()` already makes for the identical
    case, rather than silently under-reporting the system's rule count."""
    import sqlite3

    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    conn.execute(
        "INSERT INTO member (name, dialect, library, object_type) "
        "VALUES ('DUPMOD', 'natural', 'LIBA', 'program')"
    )
    conn.execute(
        "INSERT INTO member (name, dialect, library, object_type) "
        "VALUES ('DUPMOD', 'natural', 'LIBB', 'program')"
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

    out = brief.rules_register(conn)
    assert "DUPMOD" in out
    total_line = next(line for line in out.splitlines() if line.startswith("Total:"))
    assert "ambiguous" in total_line.lower()
    # Both of DUPMOD's real rule_candidate rows must be counted as excluded,
    # not just the module -- a bare "N ambiguous-named module(s)" count
    # alone would hide how many rules that actually costs the register.
    assert "2 rule candidate(s)" in total_line
    assert "1 ambiguous-named module(s)" in total_line
    assert "Total: 0 rule candidate(s) across 0 batchable module(s);" in total_line
