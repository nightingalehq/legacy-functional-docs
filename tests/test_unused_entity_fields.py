"""Guards on graph.referenced_entities / unused_entity_fields[_for_member].

Synthetic facts throughout -- invented entity/field/member names, not any
real site's data -- since what's under test is the join logic itself
(data_access / view / interaction / INCLUDE -> entity -> entity_field),
not any particular codebase's content.
"""

from __future__ import annotations

import sqlite3

from mfdoc import graph
from mfdoc.db import SCHEMA, insert, upsert_entity, upsert_field


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def test_referenced_entities_via_data_access():
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    eid = upsert_entity(conn, "SOMEFILE", "supra_master")
    insert(conn, "data_access", member_id=1, line_no=1, verb="GET", crud="R",
           entity_name="SOMEFILE", entity_id=eid, raw="x")
    ents = graph.referenced_entities(conn, 1)
    assert [e["name"] for e in ents] == ["SOMEFILE"]


def test_referenced_entities_via_screen_interaction_target():
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    upsert_entity(conn, "SOMESCREEN", "mantis_map")
    insert(conn, "interaction", member_id=1, line_no=1, kind="CONVERSE", target="SOMESCREEN")
    ents = graph.referenced_entities(conn, 1)
    assert [e["name"] for e in ents] == ["SOMESCREEN"]


def test_referenced_entities_ignores_an_include_edge_that_isnt_a_screen_reference():
    """A Mantis CONVERSE/SHOW/PROMPT/INPUT INCLUDE edge is always paired
    with an interaction row carrying the same target (see mantis.py) --
    already covered by the interaction join, so referenced_entities must
    not *also* join on call_kind='INCLUDE' directly. Natural's INCLUDE
    edges are also used for `DEFINE DATA ... USING` a copycode/LDA/GDA,
    which has no reason to be an entity at all -- this guards against
    treating one as referenced just because it happens to share a name
    with a real entity."""
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'natural')")
    upsert_entity(conn, "SOMECOPYCODE", "mantis_map")
    insert(conn, "call_edge", caller_id=1, callee_name="SOMECOPYCODE", call_kind="INCLUDE", line_no=1)
    ents = graph.referenced_entities(conn, 1)
    assert ents == []


def test_referenced_entities_via_converse_still_resolves_with_its_paired_include_edge():
    """The real shape mantis.py produces for CONVERSE/SHOW: an interaction
    row AND an INCLUDE call_edge with the same target, in the same
    statement -- must still resolve to exactly the one screen entity, not
    be affected by removing the direct INCLUDE join."""
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    upsert_entity(conn, "SOMESCREEN", "mantis_map")
    insert(conn, "interaction", member_id=1, line_no=1, kind="CONVERSE", target="SOMESCREEN")
    insert(conn, "call_edge", caller_id=1, callee_name="SOMESCREEN", call_kind="INCLUDE", line_no=1)
    ents = graph.referenced_entities(conn, 1)
    assert [e["name"] for e in ents] == ["SOMESCREEN"]


def test_referenced_entities_via_view_binding():
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    upsert_entity(conn, "SOMEFILE", "supra_master")
    insert(conn, "variable", member_id=1, scope="view", name="SOMEVIEW", view_of="SOMEFILE", line_no=1)
    ents = graph.referenced_entities(conn, 1)
    assert [e["name"] for e in ents] == ["SOMEFILE"]


def test_referenced_entities_deduplicates_across_sources():
    """The same entity reached via two different routes (a data_access AND
    a view over it) must appear once, not twice."""
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    eid = upsert_entity(conn, "SOMEFILE", "supra_master")
    insert(conn, "data_access", member_id=1, line_no=1, verb="GET", crud="R",
           entity_name="SOMEFILE", entity_id=eid, raw="x")
    insert(conn, "variable", member_id=1, scope="view", name="SOMEVIEW", view_of="SOMEFILE", line_no=2)
    ents = graph.referenced_entities(conn, 1)
    assert len(ents) == 1


def test_referenced_entities_ignores_view_of_on_an_unrelated_scope():
    """Only scope IN ('view', 'screen') legitimately means "this member
    touches the entity view_of names" -- some other scope happening to
    carry a view_of-shaped value (not something any current extractor
    does, but not guaranteed by the schema either) must not be treated
    as a reference."""
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    upsert_entity(conn, "SOMEFILE", "supra_master")
    insert(conn, "variable", member_id=1, scope="mantis_local", name="X", view_of="SOMEFILE", line_no=1)
    ents = graph.referenced_entities(conn, 1)
    assert ents == []


def test_unused_fields_ignores_a_name_appearing_only_in_a_comment():
    """A field name mentioned only inside a comment line (is_comment=1)
    is not actually referenced by the code -- must still count as
    unused, not be masked by the comment's mention of it."""
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text, is_comment) VALUES "
        "(1, 1, '* TODO: use FIELD_UNUSED here', 1)"
    )
    eid = upsert_entity(conn, "SOMESCREEN", "mantis_map")
    upsert_field(conn, eid, "FIELD_UNUSED", format="TEXT")
    insert(conn, "interaction", member_id=1, line_no=1, kind="CONVERSE", target="SOMESCREEN")

    unused = graph.unused_entity_fields_for_member(conn, 1)
    assert {f["field_name"] for f in unused} == {"FIELD_UNUSED"}


def test_unused_fields_matches_identifiers_with_non_word_leading_chars():
    """A field name starting with `#`/`&` (both valid Natural/Mantis
    identifier characters) must still be recognised as used when it
    genuinely appears -- plain `\\b` can't fire between two non-word
    characters (e.g. a space then `#`), so this specifically guards
    against reverting to that."""
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'IF #COUNTER = 1')"
    )
    eid = upsert_entity(conn, "SOMESCREEN", "mantis_map")
    upsert_field(conn, eid, "#COUNTER", format="TEXT")
    insert(conn, "interaction", member_id=1, line_no=1, kind="CONVERSE", target="SOMESCREEN")

    unused = graph.unused_entity_fields_for_member(conn, 1)
    assert unused == []


def test_unused_fields_excludes_referenced_and_heading_fields():
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES "
        "(1, 1, 'IF FIELD_USED = 1'), (1, 2, 'END')"
    )
    eid = upsert_entity(conn, "SOMESCREEN", "mantis_map")
    upsert_field(conn, eid, "FIELD_USED", format="TEXT")
    upsert_field(conn, eid, "FIELD_UNUSED", format="TEXT")
    upsert_field(conn, eid, "SOME LITERAL HEADING", format="HEADING")
    insert(conn, "interaction", member_id=1, line_no=1, kind="CONVERSE", target="SOMESCREEN")

    unused = graph.unused_entity_fields_for_member(conn, 1)
    names = {f["field_name"] for f in unused}
    assert names == {"FIELD_UNUSED"}, "FIELD_USED (referenced) and the HEADING row must both be excluded"


def test_unused_fields_whole_word_match_not_substring():
    """A field name that's a substring of a longer identifier in the
    program's source (e.g. FIELD_A inside OTHER_FIELD_ABC) must not count
    as a match -- only a real whole-word occurrence does."""
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'X = OTHER_FIELD_ABC')"
    )
    eid = upsert_entity(conn, "SOMESCREEN", "mantis_map")
    upsert_field(conn, eid, "FIELD_ABC", format="TEXT")
    insert(conn, "interaction", member_id=1, line_no=1, kind="CONVERSE", target="SOMESCREEN")

    unused = graph.unused_entity_fields_for_member(conn, 1)
    assert {f["field_name"] for f in unused} == {"FIELD_ABC"}


def test_unused_entity_fields_adds_a_gap_per_finding():
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    eid = upsert_entity(conn, "SOMESCREEN", "mantis_map")
    upsert_field(conn, eid, "FIELD_UNUSED", format="TEXT")
    insert(conn, "interaction", member_id=1, line_no=1, kind="CONVERSE", target="SOMESCREEN")

    found = graph.unused_entity_fields(conn)
    assert len(found) == 1
    gap = conn.execute(
        "SELECT gap_kind, severity, member_id FROM gap WHERE gap_kind='unused_field'"
    ).fetchone()
    assert gap is not None
    assert gap["severity"] == "low"
    assert gap["member_id"] == 1


def test_unused_entity_fields_is_idempotent_across_reruns():
    """run_all() clears gap_kind='unused_field' before re-deriving (see
    graph.DERIVED_GAP_KINDS) -- calling unused_entity_fields twice without
    that clear would double every gap row, same failure class the
    DERIVED_GAP_KINDS mechanism exists to prevent for every other derived
    gap kind."""
    conn = _conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'PROG1', 'mantis')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    eid = upsert_entity(conn, "SOMESCREEN", "mantis_map")
    upsert_field(conn, eid, "FIELD_UNUSED", format="TEXT")
    insert(conn, "interaction", member_id=1, line_no=1, kind="CONVERSE", target="SOMESCREEN")

    graph.unused_entity_fields(conn)
    conn.execute(
        f"DELETE FROM gap WHERE gap_kind IN ({','.join('?' * len(graph.DERIVED_GAP_KINDS))})",
        graph.DERIVED_GAP_KINDS,
    )
    graph.unused_entity_fields(conn)
    n = conn.execute("SELECT COUNT(*) AS n FROM gap WHERE gap_kind='unused_field'").fetchone()["n"]
    assert n == 1
