"""Guards on the Natural map (.nsm) body extractor (issue 4.4).

MMM9000.nsm exercises both a field (F) line and a text/label (T) line.
Map-body recognition is explicitly flagged as unverified against a real
client export (no public sample was found for this format, unlike the
Natural corpus that grounded issue 4.11) -- see the map_body_unverified
gap every map member raises.
"""

from __future__ import annotations


def test_map_field_line_becomes_an_interaction_row_with_its_options(indexed_db):
    conn = indexed_db
    row = conn.execute(
        """
        SELECT target, fields FROM interaction i JOIN member m ON m.id = i.member_id
         WHERE m.name='MMM9000' AND i.kind='MAP_FIELD' AND i.line_no=11
        """
    ).fetchone()
    assert row is not None
    assert row["target"] == "#ORDER-NO"
    assert row["fields"] == "AD=I"


def test_map_text_line_keeps_the_unmasked_prompt_text(indexed_db):
    """The literal is entirely NULs by the time it reaches `masked`
    (mask_literals substitutes the quote characters too) -- must recover
    the real text via orig(), not lose it the way the pre-4.5 masked-
    literal bug did for conditions."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT fields FROM interaction i JOIN member m ON m.id = i.member_id
         WHERE m.name='MMM9000' AND i.kind='MAP_TEXT' AND i.line_no=10
        """
    ).fetchone()
    assert row is not None
    assert row["fields"] == "Order number:"


def test_map_member_raises_the_unverified_format_gap(indexed_db):
    """This format has no verified real sample behind it -- every map
    member must say so explicitly, not present the extraction as fact."""
    conn = indexed_db
    gap = conn.execute(
        """
        SELECT 1 FROM gap g JOIN member m ON m.id = g.member_id
         WHERE m.name='MMM9000' AND g.gap_kind='map_body_unverified'
        """
    ).fetchone()
    assert gap is not None


def test_map_body_matcher_never_fires_outside_map_members(indexed_db):
    """A regular program with a line that happens to look like a map-body
    line (level, T/F, content) must not have it misread as a screen field
    -- the matcher is gated on object_type='map' specifically so a guess
    this format-specific never reaches ordinary program statements."""
    conn = indexed_db
    row = conn.execute(
        "SELECT object_type FROM member WHERE name='MMM9000'"
    ).fetchone()
    assert row["object_type"] == "map"
    stray = conn.execute(
        """
        SELECT 1 FROM interaction i JOIN member m ON m.id = i.member_id
         WHERE i.kind IN ('MAP_FIELD','MAP_TEXT') AND m.object_type != 'map'
        """
    ).fetchone()
    assert stray is None
