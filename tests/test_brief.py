"""Guards on the fact-brief generator (brief.py)."""

from __future__ import annotations

from mfdoc.brief import (
    MemberFacts,
    _rule_id,
    build_member_facts,
    chunk_density_metrics,
    entity_brief,
    flag_density_outliers,
    format_density_note,
    module_brief,
    routine_aware_chunk_ranges,
    routine_for_line,
)
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


# --- chunk_density_metrics / flag_density_outliers / format_density_note --
# Synthetic fixture (issue #105): two ordinary chunks (3 rules each, packed
# tightly, shallow nesting) plus one deliberately rule-dense chunk (only 2
# rules, but its source sprawls across 41 lines with much deeper nesting) --
# same shape as the real regression the issue describes: a chunk that looks
# unremarkable by rule count alone but is genuinely harder to narrate.

_DENSE_RANGES = [(1, 3), (4, 6), (7, 8)]
_DENSE_LINE_NOS = [1, 2, 3, 10, 11, 12, 20, 60]
_DENSE_DEPTHS = [1, 1, 1, 1, 1, 2, 4, 5]


def test_chunk_density_metrics_computes_span_and_lines_per_item():
    metrics = chunk_density_metrics(_DENSE_LINE_NOS, _DENSE_RANGES, _DENSE_DEPTHS)
    assert len(metrics) == 3
    assert metrics[0]["item_count"] == 3
    assert metrics[0]["line_span"] == 3
    assert metrics[0]["lines_per_item"] == 1.0
    assert metrics[0]["avg_depth"] == 1.0
    # the dense chunk: only 2 rules, but they span 41 source lines
    assert metrics[2]["item_count"] == 2
    assert metrics[2]["line_span"] == 41
    assert metrics[2]["lines_per_item"] == 20.5
    assert metrics[2]["avg_depth"] == 4.5


def test_chunk_density_metrics_without_depths_leaves_avg_depth_none():
    metrics = chunk_density_metrics(_DENSE_LINE_NOS, _DENSE_RANGES)
    assert all(m["avg_depth"] is None for m in metrics)


def test_chunk_density_metrics_handles_unresolvable_line_nos():
    """testbatch.py's rows use a negative sentinel for items belonging to no
    rule/routine -- fewer than 2 resolvable line numbers in a chunk must not
    produce a misleading span, just None."""
    metrics = chunk_density_metrics([-1, -1, -1], [(1, 3)])
    assert metrics[0]["line_span"] is None
    assert metrics[0]["lines_per_item"] is None
    assert metrics[0]["item_count"] == 3


def test_chunk_density_metrics_mixed_resolvable_leaves_lines_per_item_none():
    """testbatch.py's chunks can mix rows tied to a rule/routine (a real
    line_no) with rows that aren't (the negative sentinel). line_span can
    still be computed from the resolvable subset, but lines_per_item must
    come back None rather than dividing that partial span by the chunk's
    full item_count -- that would understate density for a chunk that
    isn't fully resolvable (issue #107 review comment)."""
    metrics = chunk_density_metrics([10, -1, 30, -1], [(1, 4)])
    assert metrics[0]["item_count"] == 4
    assert metrics[0]["line_span"] == 21
    assert metrics[0]["lines_per_item"] is None


def test_flag_density_outliers_flags_the_rule_dense_chunk():
    metrics = chunk_density_metrics(_DENSE_LINE_NOS, _DENSE_RANGES, _DENSE_DEPTHS)
    flagged = flag_density_outliers(metrics)
    assert flagged[0]["outlier"] is False
    assert flagged[1]["outlier"] is False
    assert flagged[2]["outlier"] is True
    assert flagged[2]["outlier_reasons"], "outlier chunk must explain why"
    assert any("lines/item" in r for r in flagged[2]["outlier_reasons"])
    assert any("nesting depth" in r for r in flagged[2]["outlier_reasons"])


def test_flag_density_outliers_needs_at_least_two_chunks_to_compare():
    """A single chunk has no siblings to be an outlier relative to."""
    metrics = chunk_density_metrics(_DENSE_LINE_NOS[:2], [(1, 2)], _DENSE_DEPTHS[:2])
    flagged = flag_density_outliers(metrics)
    assert flagged[0]["outlier"] is False
    assert flagged[0]["outlier_reasons"] == []


def test_flag_density_outliers_flags_multiple_dense_chunks_against_others_median():
    """A single median across *all* chunks (including the candidate itself)
    can be pulled upward by that very candidate, so a run with more than one
    dense chunk can fail to flag any of them -- e.g. [1, 100, 100] lines/item
    has an all-inclusive median of 100, and neither dense chunk (100) clears
    factor(1.5) * 100 = 150. The median must instead be computed, per chunk,
    from its *other* chunks' values only (issue #105 review comment) -- then
    both dense chunks clear factor * median([1, 100]) = 75.75."""
    metrics = [
        {"item_count": 1, "line_span": 1, "lines_per_item": 1.0, "avg_depth": None},
        {"item_count": 1, "line_span": 100, "lines_per_item": 100.0, "avg_depth": None},
        {"item_count": 1, "line_span": 100, "lines_per_item": 100.0, "avg_depth": None},
    ]
    flagged = flag_density_outliers(metrics)
    assert flagged[0]["outlier"] is False
    assert flagged[1]["outlier"] is True
    assert flagged[2]["outlier"] is True
    assert any("lines/item" in r for r in flagged[1]["outlier_reasons"])
    assert any("lines/item" in r for r in flagged[2]["outlier_reasons"])


def test_flag_density_outliers_flags_depth_against_a_flat_zero_median():
    """avg_depth is 0-based -- top-level depth is often 0 -- so a run whose
    other chunks are all flat (median 0) must still flag a genuinely nested
    chunk; the usual factor-times-median rule can never fire against a 0
    median (issue #105 review comment)."""
    metrics = [
        {"item_count": 1, "line_span": 1, "lines_per_item": 1.0, "avg_depth": 0.0},
        {"item_count": 1, "line_span": 1, "lines_per_item": 1.0, "avg_depth": 0.0},
        {"item_count": 1, "line_span": 1, "lines_per_item": 1.0, "avg_depth": 3.0},
    ]
    flagged = flag_density_outliers(metrics)
    assert flagged[0]["outlier"] is False
    assert flagged[1]["outlier"] is False
    assert flagged[2]["outlier"] is True
    assert any("nesting depth" in r for r in flagged[2]["outlier_reasons"])


def test_flag_density_outliers_does_not_flag_flat_chunk_against_flat_median():
    """A chunk with avg_depth 0 compared against an all-flat median of 0
    must not be flagged (there's nothing deeper about it)."""
    metrics = [
        {"item_count": 1, "line_span": 1, "lines_per_item": 1.0, "avg_depth": 0.0},
        {"item_count": 1, "line_span": 1, "lines_per_item": 1.0, "avg_depth": 0.0},
        {"item_count": 1, "line_span": 1, "lines_per_item": 1.0, "avg_depth": 0.0},
    ]
    flagged = flag_density_outliers(metrics)
    assert all(m["outlier"] is False for m in flagged)


def test_format_density_note_reports_metrics_and_outlier_flag():
    metrics = chunk_density_metrics(_DENSE_LINE_NOS, _DENSE_RANGES, _DENSE_DEPTHS)
    flagged = flag_density_outliers(metrics)
    normal_note = format_density_note(flagged[0])
    assert "density:" in normal_note
    assert "OUTLIER" not in normal_note

    dense_note = format_density_note(flagged[2])
    assert "20.5 lines/item" in dense_note
    assert "avg depth 4.5" in dense_note
    assert "OUTLIER" in dense_note


def test_format_density_note_handles_missing_fields_gracefully():
    """No exception on a partial entry (e.g. line_span unresolvable, no
    depth data supplied) -- the diagnostic must never itself fail."""
    note = format_density_note({"item_count": 2, "line_span": None, "lines_per_item": None, "avg_depth": None})
    assert note == "density: 2 item(s)"


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
        "  IF RECORD_NOT_FOUND = 1\n"
        '    MSG="no matching record found"\n'
        "  ELSE\n"
        "    GET WIDGETFILE01(SCHED_KEY)FIRST\n"
        "    DELETE WIDGETFILE02(SCHED_KEY)\n"
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
    assert "GET" in else_line and "WIDGETFILE01" in else_line and "[[TESTMOD:6]]" in else_line
    assert "DELETE" in else_line and "WIDGETFILE02" in else_line and "[[TESTMOD:7]]" in else_line


# --- issue #141: screen field / program variable / DB view field kinds -----

def test_mantis_brief_tags_screen_local_and_view_fields_with_distinct_kinds():
    """A Mantis module mixing a SCREEN-bound field, a plain working-storage
    declaration, and a VIEW field must render each with a distinguishable
    kind label -- before this fix the "Inputs"/"Data used" narrative had no
    signal telling these three namespaces apart at all."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import mantis

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'WGTMOD', 'mantis')")
    src = (
        'PROGRAM "WGTMOD"\n'
        "ENTRY MAIN\n"
        "VIEW WIDGET-VIEW OF WIDGET-MASTER\n"
        "TEXT WIDGET-NOTE(30)\n"
        'SCREEN SHIFT-CODE("SHIFT-CODE-FLD")\n'
        "EXIT\n"
    )
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    mantis.extract(conn, 1, lines, "WGTMOD")

    brief = module_brief(conn, "WGTMOD", redact=NULL_REDACTOR)
    assert "## Program variables and screen/MAP fields" in brief
    section = brief.split("## Program variables and screen/MAP fields", 1)[1]
    local_line = [l for l in section.splitlines() if "WIDGET-NOTE" in l][0]
    screen_line = [l for l in section.splitlines() if "SHIFT-CODE" in l][0]
    assert "program variable" in local_line and "screen field" not in local_line
    assert "screen field" in screen_line and "program variable" not in screen_line
    # the view field stays under its own pre-existing section, not duplicated here
    assert "## Data views declared" in brief
    assert "WIDGET-VIEW" not in section


def test_mantis_brief_keeps_interface_call_target_bindings_out_of_the_variable_section():
    """A Mantis `INTERFACE handle("LITERAL",...)` binding (scope=
    'mantis_interface', see `graph.resolve_interface_literal_calls`) is
    call-target metadata, not a field a program reads or writes -- it must
    never appear in the "Program variables and screen/MAP fields" section
    mislabeled as one."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import mantis

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'WGTMOD', 'mantis')")
    src = (
        'PROGRAM "WGTMOD"\n'
        "ENTRY MAIN\n"
        '  INTERFACE MYHANDLE("REALPROG",PASSWORD)\n'
        "TEXT WIDGET-NOTE(30)\n"
        "EXIT\n"
    )
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    mantis.extract(conn, 1, lines, "WGTMOD")

    brief = module_brief(conn, "WGTMOD", redact=NULL_REDACTOR)
    assert "## Program variables and screen/MAP fields" in brief
    section = brief.split("## Program variables and screen/MAP fields", 1)[1]
    section = section.split("## ", 1)[0]
    assert "MYHANDLE" not in section
    assert "WIDGET-NOTE" in section


def test_natural_brief_resolves_a_local_variable_as_a_screen_field_via_using_map():
    """Natural declares a screen's fields as ordinary DEFINE DATA LOCAL
    variables bound only by naming convention to a `USING MAP` target --
    the brief must cross-reference the map member's own MAP_FIELD facts
    (already recorded by natural._match_map_body) against this program's
    call_edge INCLUDE (`USING MAP`) row to resolve that #NEXT-FLD, though
    declared LOCAL, is actually screen-scoped (issue #141)."""
    import sqlite3

    from mfdoc.db import SCHEMA, insert
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    map_id = insert(conn, "member", name="WGTMAP", dialect="natural", object_type="map")
    map_src = (
        "1 T 'Next item:' 01/01\n"
        "1 F #NEXT-FLD (A8) 01/16\n"
    )
    map_lines = [(i + 1, None, t) for i, t in enumerate(map_src.splitlines())]
    natural.extract(conn, map_id, map_lines, "WGTMAP")

    conn.execute("INSERT INTO member (id, name, dialect) VALUES (99, 'WGTPGM', 'natural')")
    pgm_src = (
        "DEFINE DATA LOCAL\n"
        "1 #NEXT-FLD (A8)\n"
        "1 #NEXT-ID (A4)\n"
        "END-DEFINE\n"
        "INPUT USING MAP 'WGTMAP'\n"
        "#NEXT-ID := SUBSTR(#NEXT-FLD,1,4)\n"
    )
    pgm_lines = [(i + 1, None, t) for i, t in enumerate(pgm_src.splitlines())]
    natural.extract(conn, 99, pgm_lines, "WGTPGM")
    conn.execute("UPDATE call_edge SET callee_id=? WHERE callee_name='WGTMAP'", (map_id,))

    brief = module_brief(conn, "WGTPGM", redact=NULL_REDACTOR)
    section = brief.split("## Program variables and screen/MAP fields", 1)[1]
    screen_line = [l for l in section.splitlines() if "#NEXT-FLD" in l][0]
    plain_line = [l for l in section.splitlines() if "#NEXT-ID" in l][0]
    assert "screen field" in screen_line
    assert "program variable" in plain_line and "screen field" not in plain_line


def test_natural_brief_keeps_using_data_area_includes_out_of_program_variables():
    """`DEFINE DATA LOCAL USING <LDA/PDA/GDA>` records a synthetic `variable`
    row named `USING <NAME>` alongside the call_edge INCLUDE row (natural.py)
    -- that's a data-area include, not a program variable, and must not be
    mislabeled as one in the "Program variables and screen/MAP fields"
    section (issue #141 follow-up)."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'MMP0100', 'natural')")
    src = (
        "DEFINE DATA\n"
        "PARAMETER USING PDAWGT01\n"
        "LOCAL USING LDAWGT01\n"
        "LOCAL\n"
        "1 #TALLY (N4)\n"
        "END-DEFINE\n"
        "#TALLY := #TALLY + 1\n"
    )
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    natural.extract(conn, 1, lines, "MMP0100")

    brief = module_brief(conn, "MMP0100", redact=NULL_REDACTOR)

    assert "## Interface (parameters)" not in brief
    var_section = brief.split("## Program variables and screen/MAP fields", 1)[1]
    var_section = var_section.split("## ", 1)[0]
    assert "LDAWGT01" not in var_section
    assert "USING" not in var_section

    tally_line = [l for l in var_section.splitlines() if "#TALLY" in l][0]
    assert "program variable" in tally_line

    assert "## Data areas included" in brief
    includes_section = brief.split("## Data areas included", 1)[1]
    include_line = [l for l in includes_section.splitlines() if "LDAWGT01" in l][0]
    assert "data area include" in include_line
    assert "program variable" not in include_line
    parameter_include_line = [
        l for l in includes_section.splitlines() if "PDAWGT01" in l
    ][0]
    assert "data area include" in parameter_include_line


# --- issue #148: FIND/READ/HISTOGRAM found-body extent must reach the brief

def test_module_brief_summarizes_single_callers_guard_chain():
    """Issue #151: a subroutine reachable from exactly one call site should
    have its "Inbound callers" section summarize the caller's own preceding
    guard/validation call sequence (already recorded, in order, as
    `call_edge` rows) rather than citing only the call line itself. Fixture:
    `ORDER-CTRL` validates a customer and confirms a balance, each gated by
    its own IF, before ever reaching the single call to `SCHEDULE-RESET`.

    The first IF also has a literal-bearing `MOVE` statement ahead of its
    `CALLNAT`, deliberately -- a statement-shaped `rule_candidate` has no
    block of its own and must never be mistaken for the enclosing guard
    (regression coverage for `_enclosing_condition`/`_opens_a_block`)."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'ORDER-CTRL', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'SCHEDULE-RESET', 'natural')")

    caller_src = (
        "IF #CUST-VALID\n"                    # line 1
        "  MOVE 'Y' TO #CUST-FLAG\n"           # line 2 -- statement, not a guard
        "  CALLNAT 'VALIDATE-CUSTOMER'\n"      # line 3
        "END-IF\n"                             # line 4
        "IF #BAL-CONFIRMED\n"                  # line 5
        "  CALLNAT 'CONFIRM-BALANCE'\n"        # line 6
        "END-IF\n"                             # line 7
        "CALLNAT 'SCHEDULE-RESET'\n"           # line 8
    )
    caller_lines = [(i + 1, None, t) for i, t in enumerate(caller_src.splitlines())]
    natural.extract(conn, 1, caller_lines, "ORDER-CTRL")

    callee_src = (
        "FIND (1) SCHED-VIEW WITH SCHED-KEY = 'RESET'\n"
        "  MOVE ' ' TO SCHED-VIEW.SCHED-STATUS\n"
        "END-FIND\n"
    )
    callee_lines = [(i + 1, None, t) for i, t in enumerate(callee_src.splitlines())]
    natural.extract(conn, 2, callee_lines, "SCHEDULE-RESET")

    brief = module_brief(conn, "SCHEDULE-RESET", redact=NULL_REDACTOR)
    inbound_section = brief.split("## Inbound callers", 1)[1].split("## ", 1)[0]

    assert "the only known call site" in inbound_section
    assert "VALIDATE-CUSTOMER" in inbound_section
    assert "CONFIRM-BALANCE" in inbound_section
    assert "#CUST-VALID" in inbound_section
    assert "#BAL-CONFIRMED" in inbound_section
    assert "[[ORDER-CTRL:1]]" in inbound_section     # IF #CUST-VALID
    assert "[[ORDER-CTRL:3]]" in inbound_section      # CALLNAT VALIDATE-CUSTOMER
    assert "[[ORDER-CTRL:5]]" in inbound_section      # IF #BAL-CONFIRMED
    assert "[[ORDER-CTRL:6]]" in inbound_section      # CALLNAT CONFIRM-BALANCE

    # The MOVE on line 2 must never be reported as VALIDATE-CUSTOMER's guard
    # -- the guard is the enclosing IF's own condition, not the last
    # statement-shaped rule_candidate that happens to precede the call.
    assert "#CUST-FLAG" not in inbound_section
    assert "when `MOVE" not in inbound_section
    assert "[[ORDER-CTRL:2]]" not in inbound_section


def test_module_brief_guard_chain_does_not_claim_unconditional_inside_uncaptured_construct():
    """Copilot review on PR #151: a call inside a real block whose own
    `rule_candidate.condition` wasn't captured (e.g. `DECIDE FOR FIRST
    CONDITION`, which `natural.py` records with `condition=None`) is still
    control-flow scoped -- rendering it as "unconditionally calls" would be
    factually wrong, not merely uninformative. It must name the enclosing
    construct and say the guard condition wasn't captured, never claim no
    guard exists at all."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'ORDER-CTRL', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'SCHEDULE-RESET', 'natural')")

    caller_src = (
        "DECIDE FOR FIRST CONDITION\n"   # line 1 -- opens a block, condition=None
        "  CALLNAT 'AUDIT-LOG'\n"        # line 2
        "END-DECIDE\n"                   # line 3
        "CALLNAT 'SCHEDULE-RESET'\n"     # line 4
    )
    caller_lines = [(i + 1, None, t) for i, t in enumerate(caller_src.splitlines())]
    natural.extract(conn, 1, caller_lines, "ORDER-CTRL")

    callee_src = "FIND (1) SCHED-VIEW WITH SCHED-KEY = 'RESET'\n  MOVE ' ' TO SCHED-VIEW.SCHED-STATUS\nEND-FIND\n"
    callee_lines = [(i + 1, None, t) for i, t in enumerate(callee_src.splitlines())]
    natural.extract(conn, 2, callee_lines, "SCHEDULE-RESET")

    brief = module_brief(conn, "SCHEDULE-RESET", redact=NULL_REDACTOR)
    inbound_section = brief.split("## Inbound callers", 1)[1].split("## ", 1)[0]

    assert "unconditionally" not in inbound_section
    assert "AUDIT-LOG" in inbound_section
    assert "guard condition not captured" in inbound_section
    assert "DECIDE FOR FIRST CONDITION" in inbound_section
    assert "[[ORDER-CTRL:1]]" in inbound_section


def test_module_brief_guard_chain_stops_at_a_closed_unresolved_extent_block():
    """Copilot review on PR #151: DECIDE/FOR/REPEAT/ON ERROR/AT-EVENT never
    get their own `end_line` backfilled (only IF/ELSE do), so a call *after*
    such a block's real `END-DECIDE`/`END-FOR`/etc. must not be reported as
    still scoped inside it just because `end_line` is NULL. Fixture: a call
    sits after the DECIDE's own END-DECIDE, before the single call to
    `SCHEDULE-RESET` -- it must be reported as its own, unscoped bullet, not
    attributed to the already-closed DECIDE."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'ORDER-CTRL', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'SCHEDULE-RESET', 'natural')")

    caller_src = (
        "DECIDE FOR FIRST CONDITION\n"   # line 1 -- opens a block, condition=None
        "  MOVE 'X' TO #FLAG\n"          # line 2
        "END-DECIDE\n"                   # line 3 -- block actually closes here
        "CALLNAT 'AUDIT-LOG'\n"          # line 4 -- outside the DECIDE
        "CALLNAT 'SCHEDULE-RESET'\n"     # line 5
    )
    caller_lines = [(i + 1, None, t) for i, t in enumerate(caller_src.splitlines())]
    natural.extract(conn, 1, caller_lines, "ORDER-CTRL")

    callee_src = "FIND (1) SCHED-VIEW WITH SCHED-KEY = 'RESET'\n  MOVE ' ' TO SCHED-VIEW.SCHED-STATUS\nEND-FIND\n"
    callee_lines = [(i + 1, None, t) for i, t in enumerate(callee_src.splitlines())]
    natural.extract(conn, 2, callee_lines, "SCHEDULE-RESET")

    brief = module_brief(conn, "SCHEDULE-RESET", redact=NULL_REDACTOR)
    inbound_section = brief.split("## Inbound callers", 1)[1].split("## ", 1)[0]

    assert "- calls `AUDIT-LOG` (`CALLNAT`) [[ORDER-CTRL:4]]" in inbound_section
    assert "DECIDE" not in inbound_section
    assert "guard condition not captured" not in inbound_section


def test_module_brief_guard_chain_nested_block_close_does_not_close_the_outer_block():
    """Copilot review on PR #151: a nested block's own close (an inner IF's
    `END-IF`) must not be mistaken for closing an outer, unresolved-extent
    block (a `DECIDE FOR FIRST CONDITION` with no `end_line` of its own).
    Fixture nests an IF inside a DECIDE:

    - a call still inside the DECIDE, after the inner IF's own END-IF,
      must still be attributed to the DECIDE (not treated as unscoped);
    - a call after the DECIDE's own END-DECIDE must be unscoped, not
      attributed to the DECIDE just because an END-* line preceded it."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'ORDER-CTRL', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'SCHEDULE-RESET', 'natural')")

    caller_src = (
        "DECIDE FOR FIRST CONDITION\n"  # line 1 -- opens DECIDE, condition=None
        "  IF #X\n"                     # line 2 -- opens nested IF, condition="#X"
        "    CALLNAT 'INNER-CALL'\n"    # line 3 -- inside the nested IF
        "  END-IF\n"                    # line 4 -- closes the nested IF only
        "  CALLNAT 'STILL-IN-DECIDE'\n"  # line 5 -- back inside the DECIDE
        "END-DECIDE\n"                  # line 6 -- closes the DECIDE
        "CALLNAT 'AFTER-DECIDE'\n"      # line 7 -- outside the DECIDE
        "CALLNAT 'SCHEDULE-RESET'\n"    # line 8 -- the single call site
    )
    caller_lines = [(i + 1, None, t) for i, t in enumerate(caller_src.splitlines())]
    natural.extract(conn, 1, caller_lines, "ORDER-CTRL")

    callee_src = "FIND (1) SCHED-VIEW WITH SCHED-KEY = 'RESET'\n  MOVE ' ' TO SCHED-VIEW.SCHED-STATUS\nEND-FIND\n"
    callee_lines = [(i + 1, None, t) for i, t in enumerate(callee_src.splitlines())]
    natural.extract(conn, 2, callee_lines, "SCHEDULE-RESET")

    brief = module_brief(conn, "SCHEDULE-RESET", redact=NULL_REDACTOR)
    inbound_section = brief.split("## Inbound callers", 1)[1].split("## ", 1)[0]
    bullet_lines = [line for line in inbound_section.splitlines() if line.startswith("- ")]

    def bullet_for(callee: str) -> str:
        matches = [line for line in bullet_lines if f"`{callee}`" in line]
        assert len(matches) == 1, f"expected exactly one bullet for {callee}, got {matches}"
        return matches[0]

    inner_call = bullet_for("INNER-CALL")
    assert "when `#X` holds" in inner_call

    still_in_decide = bullet_for("STILL-IN-DECIDE")
    assert "DECIDE FOR FIRST CONDITION" in still_in_decide
    assert "guard condition not captured" in still_in_decide
    assert "[[ORDER-CTRL:1]]" in still_in_decide

    after_decide = bullet_for("AFTER-DECIDE")
    # Exact equality: no "scoped inside ..." / "when ... holds" wrapper --
    # a bare call bullet is the only correct rendering once the DECIDE has
    # actually closed.
    assert after_decide == "- calls `AFTER-DECIDE` (`CALLNAT`) [[ORDER-CTRL:7]]"


def test_module_brief_guard_chain_ignores_a_data_access_loops_own_end_line():
    """Copilot review on PR #151: `_BLOCK_CLOSE_LINE_RE` must not match
    `END-FIND`/`END-READ`/`END-HISTOGRAM` (or `END-WORK`/`END-ALL`/
    `END-SUBROUTINE`/`END-BEFORE`/`END-PROCESS`) -- none of those pop
    `open_blocks` in natural.py (`_END_TO_OPENERS`'s own keys), and
    FIND/READ/HISTOGRAM never push onto it at all. A FIND...END-FIND data
    access loop entirely inside a still-open DECIDE must not be mistaken
    for closing that DECIDE."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'ORDER-CTRL', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'SCHEDULE-RESET', 'natural')")

    caller_src = (
        "DECIDE FOR FIRST CONDITION\n"                       # line 1
        "  FIND (1) WIDGET-VIEW WITH WIDGET-KEY = 'X'\n"      # line 2
        "    MOVE 'Y' TO #FLAG\n"                             # line 3
        "  END-FIND\n"                                        # line 4 -- not a block close
        "  CALLNAT 'STILL-IN-DECIDE'\n"                        # line 5
        "END-DECIDE\n"                                         # line 6
        "CALLNAT 'SCHEDULE-RESET'\n"                           # line 7
    )
    caller_lines = [(i + 1, None, t) for i, t in enumerate(caller_src.splitlines())]
    natural.extract(conn, 1, caller_lines, "ORDER-CTRL")

    callee_src = "FIND (1) SCHED-VIEW WITH SCHED-KEY = 'RESET'\n  MOVE ' ' TO SCHED-VIEW.SCHED-STATUS\nEND-FIND\n"
    callee_lines = [(i + 1, None, t) for i, t in enumerate(callee_src.splitlines())]
    natural.extract(conn, 2, callee_lines, "SCHEDULE-RESET")

    brief = module_brief(conn, "SCHEDULE-RESET", redact=NULL_REDACTOR)
    inbound_section = brief.split("## Inbound callers", 1)[1].split("## ", 1)[0]

    still_in_decide = [
        line for line in inbound_section.splitlines() if "`STILL-IN-DECIDE`" in line
    ]
    assert len(still_in_decide) == 1
    assert "DECIDE FOR FIRST CONDITION" in still_in_decide[0]
    assert "guard condition not captured" in still_in_decide[0]


def test_module_brief_redacts_guard_chain_condition_text():
    """The guard-chain condition text synthesized by `_caller_guard_chain` is
    raw source (`rule_candidate.condition`), same as every other condition
    rendering in this module -- it must go through `redact` before landing
    in the brief, not be pasted in verbatim."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'ORDER-CTRL', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'SCHEDULE-RESET', 'natural')")

    caller_src = (
        "IF #CUST-VALID\n"
        "  CALLNAT 'VALIDATE-CUSTOMER'\n"
        "END-IF\n"
        "CALLNAT 'SCHEDULE-RESET'\n"
    )
    caller_lines = [(i + 1, None, t) for i, t in enumerate(caller_src.splitlines())]
    natural.extract(conn, 1, caller_lines, "ORDER-CTRL")

    callee_src = "FIND (1) SCHED-VIEW WITH SCHED-KEY = 'RESET'\n  MOVE ' ' TO SCHED-VIEW.SCHED-STATUS\nEND-FIND\n"
    callee_lines = [(i + 1, None, t) for i, t in enumerate(callee_src.splitlines())]
    natural.extract(conn, 2, callee_lines, "SCHEDULE-RESET")

    redact = lambda text: text.replace("#CUST-VALID", "[REDACTED]") if text else text
    brief = module_brief(conn, "SCHEDULE-RESET", redact=redact)
    inbound_section = brief.split("## Inbound callers", 1)[1].split("## ", 1)[0]

    assert "#CUST-VALID" not in inbound_section
    assert "[REDACTED]" in inbound_section


def test_module_brief_guard_chain_never_claims_unconditional():
    """Copilot review on PR #151: finding no enclosing `rule_candidate`
    block for a preceding call is evidence it sits in the caller's main
    line of execution, not proof -- a scanner gap or an unrecognised
    control-flow shape could still be scoping it. The fallback bullet must
    report the call itself without asserting "unconditionally", which
    claims more than this brief can back."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'ORDER-CTRL', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'SCHEDULE-RESET', 'natural')")

    caller_src = "CALLNAT 'VALIDATE-CUSTOMER'\nCALLNAT 'SCHEDULE-RESET'\n"
    caller_lines = [(i + 1, None, t) for i, t in enumerate(caller_src.splitlines())]
    natural.extract(conn, 1, caller_lines, "ORDER-CTRL")

    callee_src = "FIND (1) SCHED-VIEW WITH SCHED-KEY = 'RESET'\n  MOVE ' ' TO SCHED-VIEW.SCHED-STATUS\nEND-FIND\n"
    callee_lines = [(i + 1, None, t) for i, t in enumerate(callee_src.splitlines())]
    natural.extract(conn, 2, callee_lines, "SCHEDULE-RESET")

    brief = module_brief(conn, "SCHEDULE-RESET", redact=NULL_REDACTOR)
    inbound_section = brief.split("## Inbound callers", 1)[1].split("## ", 1)[0]

    assert "unconditionally" not in inbound_section
    assert "- calls `VALIDATE-CUSTOMER` (`CALLNAT`) [[ORDER-CTRL:1]]" in inbound_section


def test_module_brief_omits_guard_chain_when_more_than_one_caller():
    """With more than one known call site there is no single guard chain to
    point to -- each caller may gate the call differently, or not at all --
    so the summary is deliberately scoped to the single-caller case, per
    issue #151's suggested fix."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'ORDER-CTRL', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'BATCH-CTRL', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (3, 'SCHEDULE-RESET', 'natural')")

    for mid, mname, src in (
        (1, "ORDER-CTRL", "IF #CUST-VALID\n  CALLNAT 'VALIDATE-CUSTOMER'\nEND-IF\nCALLNAT 'SCHEDULE-RESET'\n"),
        (2, "BATCH-CTRL", "CALLNAT 'SCHEDULE-RESET'\n"),
    ):
        lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
        natural.extract(conn, mid, lines, mname)

    callee_src = "FIND (1) SCHED-VIEW WITH SCHED-KEY = 'RESET'\n  MOVE ' ' TO SCHED-VIEW.SCHED-STATUS\nEND-FIND\n"
    callee_lines = [(i + 1, None, t) for i, t in enumerate(callee_src.splitlines())]
    natural.extract(conn, 3, callee_lines, "SCHEDULE-RESET")

    brief = module_brief(conn, "SCHEDULE-RESET", redact=NULL_REDACTOR)
    inbound_section = brief.split("## Inbound callers", 1)[1].split("## ", 1)[0]

    assert "the only known call site" not in inbound_section
    assert "VALIDATE-CUSTOMER" not in inbound_section


def test_module_brief_surfaces_find_found_body_extent_next_to_the_access():
    """The exact defect reported: a single-record `FIND (1) ... WITH
    <sentinel>` existence check with no IF/ELSE in sight, immediately
    followed by field assignments and an ESCAPE ROUTINE, must carry an
    explicit "found-body extent" fact in the brief's Data access bullet --
    a real fact the narrative stage can read, not proximity it has to
    guess from and can invert (as happened: the assignment block got
    narrated as the not-found default, the exact logical inverse of what
    FIND...END-FIND does)."""
    import sqlite3

    from mfdoc.db import SCHEMA
    from mfdoc.dialects import natural

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'TESTMOD', 'natural')")
    src = (
        "FIND (1) WIDGET-VIEW WITH WIDGET-KEY = 'SENTINEL'\n"
        "  MOVE 'DEFAULT' TO #RESULT\n"
        "  ADD 1 TO #COUNT\n"
        "END-FIND\n"
        "ESCAPE ROUTINE\n"
    )
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    natural.extract(conn, 1, lines, "TESTMOD")

    brief = module_brief(conn, "TESTMOD", redact=NULL_REDACTOR)
    access_section = brief.split("## Data access", 1)[1].split("## ", 1)[0]
    find_line = [l for l in access_section.splitlines() if "FIND" in l and "WIDGET-VIEW" in l][0]
    assert "found-body extent" in find_line
    assert "[[TESTMOD:1-4]]" in find_line


# --- issue #183: build_member_facts() lets a chunked member's per-chunk
# module_brief() calls share one member-level fact-gather instead of each
# re-running the same whole-member queries (interface, data access, calls,
# inbound callers, gaps, ...) that module_brief's own docstring says are
# unaffected by rule_range.


class _CountingConn:
    """Wraps a real sqlite3 connection, counting every `execute()` call --
    including ones made by helpers module_brief/build_member_facts call
    internally (fetch_routines, _natural_screen_field_names,
    unused_entity_fields_for_member, _caller_guard_chain,
    _copycode_rule_candidates, resolve_member_by_name), not just calls made
    directly by module_brief's own body. Delegates everything else
    (row_factory, commit, ...) to the wrapped connection unchanged."""

    def __init__(self, real):
        self._real = real
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        return self._real.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_build_member_facts_returns_every_whole_member_section_module_brief_needs(indexed_db):
    """A sanity check that build_member_facts() actually gathered the same
    facts module_brief renders sections from -- MMP0100 has params/other
    vars/routines/data access/calls/rules/gaps in its own fixture source, so
    a MemberFacts built from it should carry all of them, not an
    accidentally-empty subset."""
    facts = build_member_facts(indexed_db, "MMP0100")
    assert isinstance(facts, MemberFacts)
    assert facts.name == "MMP0100"
    assert facts.rules, "MMP0100's fixture source has rule_candidate rows"
    assert facts.routines, "MMP0100's fixture source has internal routines"


def test_build_member_facts_reused_across_chunks_makes_no_further_queries(indexed_db):
    """The whole point of issue #183: once a member's MemberFacts is built,
    every one of that member's per-chunk module_brief() calls must make
    zero additional fact-store queries for the whole-member sections --
    reusing raw rows already fetched, not re-querying them under a
    different rule_range each time."""
    counting = _CountingConn(indexed_db)
    facts = build_member_facts(counting, "MMP0100")
    assert counting.calls > 5, "sanity: building facts should run several whole-member queries"

    counting.calls = 0
    for i, rule_range in enumerate([(1, 6), (7, 12), (13, 18)], start=1):
        module_brief(
            counting, "MMP0100", redact=NULL_REDACTOR,
            rule_range=rule_range, chunk_info=(i, 3), facts=facts,
        )
    assert counting.calls == 0, (
        "module_brief() must not touch the fact store at all when a MemberFacts "
        "for this member is already given"
    )


def test_module_brief_output_identical_whether_facts_are_shared_or_rebuilt_per_chunk(indexed_db):
    """Proves Part 1 is a pure efficiency refactor: rendering the same
    member's chunks from one shared MemberFacts must produce byte-identical
    output to the old behaviour of letting each chunk's own module_brief()
    call rebuild its facts from scratch (facts=None, the default)."""
    facts = build_member_facts(indexed_db, "MMP0100")
    ranges = [(1, 6), (7, 12), (13, 18)]
    for i, rule_range in enumerate(ranges, start=1):
        chunk_info = (i, len(ranges))
        shared = module_brief(
            indexed_db, "MMP0100", redact=NULL_REDACTOR,
            rule_range=rule_range, chunk_info=chunk_info, facts=facts,
        )
        rebuilt = module_brief(
            indexed_db, "MMP0100", redact=NULL_REDACTOR,
            rule_range=rule_range, chunk_info=chunk_info,
        )
        assert shared == rebuilt, f"chunk {i}: shared-facts output diverged from a fresh per-chunk fetch"


def test_module_brief_output_identical_with_chunk_map_whether_facts_are_shared(indexed_db):
    """Same identity guarantee as above, but with chunk_map given too (the
    "documented in chunk N" annotation on the Internal routines section) --
    that annotation depends on chunk_info's current-chunk position, not on
    anything cached in MemberFacts, so it must still vary correctly per
    chunk even when every chunk shares one MemberFacts."""
    facts = build_member_facts(indexed_db, "MMP0100")
    routines = facts.routines
    assert routines, "sanity: MMP0100 must have routines for this test to mean anything"
    chunk_map = {r["name"].upper(): (idx % 3) + 1 for idx, r in enumerate(routines)}
    ranges = [(1, 6), (7, 12), (13, 18)]
    for i, rule_range in enumerate(ranges, start=1):
        chunk_info = (i, len(ranges))
        shared = module_brief(
            indexed_db, "MMP0100", redact=NULL_REDACTOR,
            rule_range=rule_range, chunk_info=chunk_info, chunk_map=chunk_map, facts=facts,
        )
        rebuilt = module_brief(
            indexed_db, "MMP0100", redact=NULL_REDACTOR,
            rule_range=rule_range, chunk_info=chunk_info, chunk_map=chunk_map,
        )
        assert shared == rebuilt, f"chunk {i}: shared-facts output diverged with chunk_map set"


def test_build_member_facts_returns_not_found_markdown_string_like_module_brief_did(indexed_db):
    """build_member_facts() must preserve module_brief's own graceful
    "no such member" early return -- callers (batch.py's chunked loop) treat
    a str result as a complete brief rather than a MemberFacts to render
    from."""
    facts = build_member_facts(indexed_db, "NO-SUCH-MEMBER-AT-ALL")
    assert isinstance(facts, str)
    assert "No such member in the index" in facts
    # module_brief() itself must return the identical text for the same lookup.
    assert module_brief(indexed_db, "NO-SUCH-MEMBER-AT-ALL", redact=NULL_REDACTOR) == facts


def test_build_member_facts_returns_ambiguous_markdown_string_like_module_brief_did():
    """Same early-return preservation as the not-found case above, but for
    a genuinely ambiguous name -- two distinct members sharing one bare
    name across different libraries (member.name is only unique together
    with library+dialect; see db.py's UNIQUE constraint)."""
    import sqlite3

    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO member (id, name, dialect, library) VALUES (1, 'DUPMOD', 'natural', 'LIBA')"
    )
    conn.execute(
        "INSERT INTO member (id, name, dialect, library) VALUES (2, 'DUPMOD', 'natural', 'LIBB')"
    )
    conn.commit()

    facts = build_member_facts(conn, "DUPMOD")
    assert isinstance(facts, str)
    assert "ambiguous across libraries" in facts
    # module_brief() itself must return the identical text for the same lookup.
    assert module_brief(conn, "DUPMOD", redact=NULL_REDACTOR) == facts
