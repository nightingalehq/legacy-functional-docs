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
