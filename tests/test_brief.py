"""Guards on the fact-brief generator (brief.py)."""

from __future__ import annotations

from mfdoc.brief import _rule_id, entity_brief, module_brief, routine_aware_chunk_ranges, routine_for_line
from mfdoc.redact import NULL_REDACTOR


def test_module_brief_surfaces_only_lexicon_terms_actually_present(indexed_db, project_lexicon):
    """options.narrative.lexicon has 7 entries; MMP0100's own facts only
    mention some of them (CONF, GRADE-CODE, MILL-ORDER, PART, RLSD) -- the
    brief must show exactly those, not the whole glossary dumped in
    regardless of relevance (issue 4.9). HEAT-NO and CAST-DATE never appear
    in MMP0100's source, so they must be absent."""
    brief = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR, lexicon=project_lexicon)
    assert "## Business vocabulary" in brief
    for present in ("CONF", "GRADE-CODE", "MILL-ORDER", "PART", "RLSD"):
        assert f"`{present}` ->" in brief, f"expected lexicon entry for {present}"
    for absent in ("HEAT-NO", "CAST-DATE"):
        assert f"`{absent}` ->" not in brief, f"{absent} doesn't appear in MMP0100 and should be filtered out"


def test_module_brief_omits_vocabulary_section_when_no_lexicon_given(indexed_db):
    """Default behaviour (no lexicon passed) must be unchanged -- this is
    additive, not a required section."""
    brief = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR)
    assert "## Business vocabulary" not in brief


def test_entity_brief_also_surfaces_relevant_lexicon_terms(indexed_db, project_lexicon):
    """MILL-ORDER's own entity brief mentions GRADE-CODE among its fields --
    the same filtering applies to entity briefs, not just module briefs."""
    brief = entity_brief(indexed_db, "MILL-ORDER", redact=NULL_REDACTOR, lexicon=project_lexicon)
    assert "`GRADE-CODE` -> steel grade" in brief


def test_included_copycode_rules_surface_in_the_including_modules_brief(indexed_db):
    """MMP9100 INCLUDEs MMC0100, which has its own real business rule ('X9'
    grade check). Before this fix, that rule was attributed only to
    MMC0100's own brief -- a reader of MMP9100 never saw it, so a module doc
    could look complete and still miss a rule the module actually depends
    on (issue 4.2)."""
    brief = module_brief(indexed_db, "MMP9100", redact=NULL_REDACTOR)
    assert "MMC0100" in brief
    assert "X9" in brief, f"copycode's rule condition missing from MMP9100's brief:\n{brief}"
    assert "[[MMC0100:2]]" in brief, "copycode rule must cite the copycode's own line, not MMP9100's"


def test_copycode_briefed_directly_still_shows_its_own_rule(indexed_db):
    """Briefing the copycode member itself must be unaffected by the fix above."""
    brief = module_brief(indexed_db, "MMC0100", redact=NULL_REDACTOR)
    assert "X9" in brief
    assert "[[MMC0100:2]]" in brief


def test_included_copycode_rule_id_is_qualified_with_the_copycodes_own_name(indexed_db):
    """The rule lives in MMC0100, so its ID must be MMC0100:BR-001 in both
    MMP9100's brief (where it's inherited) and MMC0100's own brief -- the
    same rule must carry the same ID no matter which brief surfaces it."""
    including = module_brief(indexed_db, "MMP9100", redact=NULL_REDACTOR)
    direct = module_brief(indexed_db, "MMC0100", redact=NULL_REDACTOR)
    assert "MMC0100:BR-001" in including
    assert "MMC0100:BR-001" in direct
    assert "MMP9100:BR-001" not in including, "inherited rule must not be renumbered under the including module"


def test_rule_id_is_qualified_with_the_member_name():
    """A bare BR-003 would mean a different rule in every module that has
    one -- the ID must be unique system-wide, not just within one module's
    doc (issue 4.8)."""
    assert _rule_id("MMP0100", 3) == "MMP0100:BR-003"
    assert _rule_id("MMP0200", 3) == "MMP0200:BR-003"


def test_module_brief_assigns_stable_rule_ids_in_source_order(indexed_db):
    brief = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR)
    assert "MMP0100:BR-001" in brief
    # The IF NO RECORDS FOUND at line 34 is the first rule candidate by line
    # number, so it must carry BR-001, not some other position.
    idx = brief.index("MMP0100:BR-001")
    nearby = brief[idx:idx + 120]
    assert "[[MMP0100:34]]" in nearby, f"BR-001 did not land on the expected first rule:\n{nearby}"


def test_module_brief_rule_ids_are_stable_across_regeneration(indexed_db):
    """Re-briefing the same unchanged member must reproduce the same IDs --
    that stability is the entire point of the feature."""
    first = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR)
    second = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR)
    assert first == second


def test_module_brief_tags_rules_and_data_access_with_their_routine(indexed_db):
    """MMP0100's WRITE-AUDIT subroutine must show up both as its own
    "Internal routines" entry and tagged onto any rule candidate whose
    line falls inside it -- see reference/writing-rules.md's expectation
    that generated docs group by routine, not a flat rule list."""
    brief = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR)
    assert "## Internal routines" in brief
    assert "`WRITE-AUDIT` (natural_subroutine)" in brief


# --- routine_for_line / routine_aware_chunk_ranges -------------------------

_ROUTINES = [
    {"name": "A", "start_line": 1, "end_line": 10},
    {"name": "B", "start_line": 20, "end_line": None},  # unresolved -- extends to EOF (last routine)
]


def test_routine_for_line_finds_containing_routine():
    assert routine_for_line(_ROUTINES, 5)["name"] == "A"
    assert routine_for_line(_ROUTINES, 25)["name"] == "B"


def test_routine_for_line_returns_none_outside_every_routine():
    assert routine_for_line(_ROUTINES, 15) is None


def test_routine_for_line_unresolved_end_extends_to_next_start_not_eof():
    routines = [
        {"name": "A", "start_line": 1, "end_line": None},
        {"name": "B", "start_line": 10, "end_line": 20},
    ]
    assert routine_for_line(routines, 8)["name"] == "A"
    assert routine_for_line(routines, 10)["name"] == "B"


def test_chunk_ranges_never_splits_a_routine_even_when_oversized():
    """A -> lines 1-3 (routine X), lines 4-5 (routine Y), lines 6-11
    (routine Z, 6 rules -- bigger than chunk_size) -- Z must become its own
    oversized chunk rather than being cut at the nominal size."""
    routines = [
        {"name": "X", "start_line": 1, "end_line": 3},
        {"name": "Y", "start_line": 4, "end_line": 5},
        {"name": "Z", "start_line": 6, "end_line": 11},
    ]
    line_nos = list(range(1, 12))  # one rule per line, 11 rules total
    ranges = routine_aware_chunk_ranges(line_nos, routines, chunk_size=3)
    assert ranges == [(1, 3), (4, 5), (6, 11)]


def test_chunk_ranges_splits_main_body_rules_by_count():
    """Rules with no enclosing routine at all (a member with no internal
    subroutines) must still chunk by the nominal size -- otherwise the
    members most likely to need chunking (no structure to protect) would
    never split."""
    ranges = routine_aware_chunk_ranges(list(range(1, 6)), [], chunk_size=2)
    assert ranges == [(1, 2), (3, 4), (5, 5)]


def test_chunk_ranges_empty_input():
    assert routine_aware_chunk_ranges([], [], chunk_size=3) == []


def test_module_brief_surfaces_else_branch_data_access_next_to_the_rule():
    """The exact defect reported: an IF's error branch got documented but
    the ELSE's GET/DELETE silently disappeared. The brief must put those
    accesses right on the ELSE's own bullet, and flag the IF as having a
    paired branch that needs documenting too -- not leave a narrator to
    correlate line numbers across two separate brief sections by hand."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import mantis

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'TESTMOD', 'mantis')")
    src = (
        'PROGRAM "TESTMOD"\n'
        "ENTRY MAIN\n"
        "  IF NO_SCHEDULE_FOUND = 1\n"
        '    MSG="no active schedules for this unit"\n'
        "  ELSE\n"
        "    GET TTMTTR01(SCHED_KEY)FIRST\n"
        "    DELETE TTMTTR02(SCHED_KEY)\n"
        "  END\n"
        "EXIT\n"
    )
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    mantis.extract(conn, 1, lines, "TESTMOD")

    brief = module_brief(conn, "TESTMOD", redact=NULL_REDACTOR)
    assert "has a paired ELSE at [[TESTMOD:5]]" in brief
    assert "document what happens on BOTH branches" in brief
    else_line = [l for l in brief.splitlines() if l.startswith("- **TESTMOD:BR-003**")][0]
    assert "pairs with the IF at [[TESTMOD:3]]" in else_line
    assert "GET" in else_line and "TTMTTR01" in else_line and "[[TESTMOD:6]]" in else_line
    assert "DELETE" in else_line and "TTMTTR02" in else_line and "[[TESTMOD:7]]" in else_line
