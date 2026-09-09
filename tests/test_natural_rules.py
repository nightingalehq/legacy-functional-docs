"""Guards specific to the Natural rule-candidate scanner."""

from __future__ import annotations

import sqlite3

from mfdoc.db import SCHEMA
from mfdoc.dialects import natural


def _extract(src: str, member_name: str = "TESTMOD"):
    """Isolated in-memory index for a hand-written snippet -- same pattern
    as test_mantis_rules.py's helper of the same name, used here for the
    IF/ELSE branch-extent shape, which isn't present in the shared,
    line-number-sensitive MMP0100.nsp fixture."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, ?, 'natural')", (member_name,))
    lines = [(i + 1, None, t) for i, t in enumerate(src.splitlines())]
    natural.extract(conn, 1, lines, member_name)
    return conn


def test_if_else_branch_extent_and_pairing_is_recorded():
    """Same defect this exists to prevent as mantis.py's identical test:
    an IF's error/hold branch reads as the interesting one, but the
    ELSE's database operations must not silently disappear from the
    generated document. end_line on the IF must stop at the ELSE, and
    pair_line_no/end_line on the ELSE must resolve correctly."""
    conn = _extract(
        "IF NO-SCHEDULE-FOUND\n"
        "  MOVE 'HOLD' TO STATUS\n"
        "ELSE\n"
        "  READ SCHED-VIEW BY SCHED-KEY\n"
        "  DELETE\n"
        "END-IF\n"
    )
    if_row = conn.execute("SELECT line_no, end_line FROM rule_candidate WHERE construct='IF'").fetchone()
    else_row = conn.execute(
        "SELECT line_no, pair_line_no, end_line FROM rule_candidate WHERE construct='ELSE'"
    ).fetchone()
    assert if_row["line_no"] == 1
    assert if_row["end_line"] == 6
    assert else_row["line_no"] == 3
    assert else_row["pair_line_no"] == 1
    assert else_row["end_line"] == 6


def test_masked_literal_is_not_lost_from_the_condition(indexed_db):
    """`IF ORDER-VIEW.ORDER-STATUS NE 'CONF'` must keep CONF in the stored
    condition. Literal masking exists so keyword matching ignores text inside
    quotes; it must never cost the business value the rule actually turns on."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT rc.condition FROM rule_candidate rc
        JOIN member m ON m.id = rc.member_id
        WHERE m.name='MMP0100' AND rc.construct='IF' AND rc.condition LIKE '%ORDER-STATUS%'
        """
    ).fetchone()
    assert row is not None, "expected an IF rule on ORDER-STATUS for MMP0100"
    assert "CONF" in row["condition"], (
        f"masked literal leaked out of the stored condition: {row['condition']!r}"
    )


def test_write_audit_is_internal_subroutine_not_missing_module(indexed_db):
    """WRITE-AUDIT is DEFINE SUBROUTINE'd inside MMP0100 and PERFORM'd from
    within it. It must resolve as PERFORM_INTERNAL, not surface as an
    unresolved external call -- that false positive wastes real SME review
    time chasing a module that was never missing."""
    conn = indexed_db
    edge = conn.execute(
        """
        SELECT ce.call_kind, ce.resolved FROM call_edge ce
        JOIN member m ON m.id = ce.caller_id
        WHERE m.name='MMP0100' AND ce.callee_name='WRITE-AUDIT'
        """
    ).fetchone()
    assert edge is not None
    assert edge["call_kind"] == "PERFORM_INTERNAL"
    assert edge["resolved"] == 1

    gap = conn.execute(
        "SELECT COUNT(*) AS n FROM gap WHERE gap_kind='unresolved_call' AND detail LIKE '%WRITE-AUDIT%'"
    ).fetchone()
    assert gap["n"] == 0


def test_write_audit_routine_boundary_is_recorded(indexed_db):
    """WRITE-AUDIT's DEFINE SUBROUTINE/END-SUBROUTINE span must land in the
    `routine` table with the right start/end lines -- this is what lets
    module_brief group rules by routine and batch.py chunk by routine
    rather than an arbitrary rule count."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT r.name, r.kind, r.start_line, r.end_line FROM routine r
        JOIN member m ON m.id = r.member_id
        WHERE m.name='MMP0100' AND r.name='WRITE-AUDIT'
        """
    ).fetchone()
    assert row is not None
    assert row["kind"] == "natural_subroutine"
    assert row["end_line"] is not None, "END-SUBROUTINE exists in the fixture; boundary must resolve"
    assert row["start_line"] < row["end_line"]


def test_literal_bearing_moves_are_captured_as_rule_candidates(indexed_db):
    """`MOVE 'RLSD' TO ORDER-VIEW.ORDER-STATUS` assigns a business status
    code and must surface as a rule candidate -- previously MOVE/ADD/etc.
    were matched and silently discarded regardless of content."""
    conn = indexed_db
    rows = {
        r["line_no"]: r["literals"]
        for r in conn.execute(
            """
            SELECT rc.line_no, rc.literals FROM rule_candidate rc
            JOIN member m ON m.id = rc.member_id
            WHERE m.name='MMP0100' AND rc.construct='MOVE'
            """
        ).fetchall()
    }
    assert rows.get(54) == "RLSD"
    assert rows.get(56) == "PART"
    assert rows.get(35) == "10"


def test_continuation_folds_a_condition_that_wraps_before_the_connective(indexed_db):
    """`IF ORDER-VIEW.ORDER-STATUS = 'CONF'` wrapping onto `AND
    ORDER-VIEW.CUSTOMER-NO = 'C00123'` on the next line -- with no AND/OR/etc
    trailing the first line -- must still fold into one condition. The old
    heuristic only continued when the *current* line ended in a connective;
    real Natural just as often wraps before it, and the first line here ends
    in a closing quote. Missing this silently truncates the rule while still
    producing what looks like a complete, well-formed citation (MMP9000.nsp)."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT rc.condition FROM rule_candidate rc
        JOIN member m ON m.id = rc.member_id
        WHERE m.name='MMP9000' AND rc.construct='IF'
        """
    ).fetchone()
    assert row is not None, "expected an IF rule candidate for MMP9000"
    assert "CONF" in row["condition"], "first (pre-wrap) literal missing"
    assert "C00123" in row["condition"], (
        f"condition truncated at the wrap point, lost the AND clause: {row['condition']!r}"
    )
    assert "CUSTOMER-NO" in row["condition"]


def test_pure_accumulation_without_a_literal_is_captured_at_low_confidence(indexed_db):
    """`ADD STOCK-VIEW.AVAIL-WEIGHT TO #AVAIL-TOTAL` has no literal operand --
    it's an accumulator, not a business threshold, so it must not be
    captured at full ('verified') confidence, the same tier as a real
    literal-bearing decision (that would bury the moves that do matter).
    But per issue #149, it must no longer vanish with zero trace either --
    previously this fell through extract()'s last-resort matcher, which
    marked it matched without recording anything at all (no rule_candidate,
    no unparsed_line gap). It's now captured as a rule_candidate tagged
    confidence='low', distinguishable from a real decision downstream."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT confidence FROM rule_candidate rc JOIN member m ON m.id = rc.member_id
        WHERE m.name='MMP0100' AND rc.construct='ADD' AND rc.condition LIKE '%AVAIL-TOTAL%'
        """
    ).fetchone()
    assert row is not None, "expected the accumulator to be captured, just at low confidence"
    assert row["confidence"] == "low"
    gap = conn.execute(
        """
        SELECT 1 FROM gap g JOIN member m ON m.id = g.member_id
        WHERE g.gap_kind='unparsed_line' AND m.name='MMP0100' AND g.raw LIKE '%AVAIL-TOTAL%'
        """
    ).fetchone()
    assert gap is None, "must not also raise an unparsed_line gap now that it's captured"


def test_bare_variable_to_variable_assignment_with_no_literal_is_captured_at_low_confidence():
    """Core regression for issue #149: a pure variable/array-element
    assignment with no literal anywhere in the statement (e.g. re-anchoring
    a working variable to the value at a computed array position) fell
    through _match_arithmetic's literal gate *and* extract()'s last-resort
    matcher swallowed it silently -- matched=True, no rule_candidate, no
    unparsed_line gap. Zero trace anywhere. It must now surface as a
    low-confidence rule_candidate instead."""
    conn = _extract("#REF := #ARRAY(#INDEX)\n")
    row = conn.execute(
        "SELECT construct, confidence, fields_used FROM rule_candidate WHERE line_no=1"
    ).fetchone()
    assert row is not None, "expected #REF := #ARRAY(#INDEX) to be captured, even at low confidence"
    assert row["construct"] == "ASSIGN"
    assert row["confidence"] == "low"
    assert row["fields_used"] is not None and "REF" in row["fields_used"]
    gap = conn.execute("SELECT 1 FROM gap WHERE gap_kind='unparsed_line' AND line_no=1").fetchone()
    assert gap is None, "must not also raise an unparsed_line gap now that it's captured"


def test_loop_counter_idiom_still_has_zero_trace():
    """The classic loop-counter idiom (`ADD 1 TO x`/`SUBTRACT 1 FROM x`) is
    excluded from _match_arithmetic on purpose (RE_LOOP_COUNTER) -- almost
    never a business decision. That exclusion must survive the low-
    confidence fallback too: still no rule_candidate at all, same as
    before issue #149's fix (and still no unparsed_line gap, since the
    idiom is a recognised, just deliberately uncaptured, construct)."""
    conn = _extract("ADD 1 TO #COUNT\n")
    row = conn.execute("SELECT 1 FROM rule_candidate WHERE line_no=1").fetchone()
    assert row is None, "loop-counter idiom must still not become a rule_candidate"
    gap = conn.execute("SELECT 1 FROM gap WHERE gap_kind='unparsed_line' AND line_no=1").fetchone()
    assert gap is None, "loop-counter idiom must still not raise an unparsed_line gap"


def test_reset_is_recognised_and_not_an_unparsed_line(indexed_db):
    """MMP0100:31's `RESET #RETURN-CODE` was the one pre-existing
    unparsed_line gap in this fixture set (issue 4.11, found via a smoke
    test against SoftwareAG/adabas-natural-code-samples -- 36 occurrences
    of RESET in that corpus alone). It's structural, not a business
    decision, so it must be recognised without producing a rule_candidate."""
    conn = indexed_db
    gap = conn.execute(
        """
        SELECT 1 FROM gap WHERE gap_kind='unparsed_line' AND member_id=(
            SELECT id FROM member WHERE name='MMP0100'
        ) AND line_no=31
        """
    ).fetchone()
    assert gap is None, "RESET #RETURN-CODE at MMP0100:31 must no longer be an unparsed_line gap"
    rule = conn.execute(
        """
        SELECT 1 FROM rule_candidate rc JOIN member m ON m.id = rc.member_id
        WHERE m.name='MMP0100' AND rc.line_no=31
        """
    ).fetchone()
    assert rule is None, "RESET is structural, not a business rule -- must not become a rule_candidate"


def test_ignore_is_recognised_and_not_an_unparsed_line(indexed_db):
    """IGNORE (a real Natural no-op, 68 occurrences in the same corpus) must
    be recognised the same way -- structural, no rule_candidate."""
    conn = indexed_db
    gap = conn.execute(
        """
        SELECT 1 FROM gap g JOIN member m ON m.id = g.member_id
        WHERE g.gap_kind='unparsed_line' AND m.name='MMP9000' AND g.line_no=20
        """
    ).fetchone()
    assert gap is None, "IGNORE at MMP9000:20 must no longer be an unparsed_line gap"
    rule = conn.execute(
        """
        SELECT 1 FROM rule_candidate rc JOIN member m ON m.id = rc.member_id
        WHERE m.name='MMP9000' AND rc.line_no=20
        """
    ).fetchone()
    assert rule is None, "IGNORE is structural, not a business rule -- must not become a rule_candidate"


def test_bare_assignment_without_assign_keyword_is_captured(indexed_db):
    """`#FLAG := 1` (MMP9800:13) has no ASSIGN keyword -- valid, common
    Natural short form. Previously RE_COMPUTE only anchored on the keyword,
    so every bare assignment fell through as an unparsed_line gap instead of
    being captured the same way a literal-bearing MOVE/COMPUTE already is."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT rc.construct, rc.literals FROM rule_candidate rc
        JOIN member m ON m.id = rc.member_id
        WHERE m.name='MMP9800' AND rc.line_no=13
        """
    ).fetchone()
    assert row is not None, "expected #FLAG := 1 to be captured as a rule_candidate"
    assert row["construct"] == "ASSIGN"
    assert row["literals"] == "1"
    gap = conn.execute(
        """
        SELECT 1 FROM gap g JOIN member m ON m.id = g.member_id
        WHERE g.gap_kind='unparsed_line' AND m.name='MMP9800' AND g.line_no=13
        """
    ).fetchone()
    assert gap is None, "bare assignment must no longer be an unparsed_line gap"


def test_set_control_is_recognised_and_not_an_unparsed_line(indexed_db):
    """SET CONTROL (MMP9800:14) sends terminal/printer control codes --
    presentation, not a business decision -- so it's recognised the same way
    as RESET/IGNORE: no rule_candidate, no unparsed_line gap."""
    conn = indexed_db
    gap = conn.execute(
        """
        SELECT 1 FROM gap g JOIN member m ON m.id = g.member_id
        WHERE g.gap_kind='unparsed_line' AND m.name='MMP9800' AND g.line_no=14
        """
    ).fetchone()
    assert gap is None, "SET CONTROL must no longer be an unparsed_line gap"
    rule = conn.execute(
        """
        SELECT 1 FROM rule_candidate rc JOIN member m ON m.id = rc.member_id
        WHERE m.name='MMP9800' AND rc.line_no=14
        """
    ).fetchone()
    assert rule is None, "SET CONTROL is structural -- must not become a rule_candidate"


def test_input_window_folds_slash_prefixed_colspec_continuations(indexed_db):
    """An INPUT WINDOW block (MMP9800:15-17) whose column-spec continuation
    lines are prefixed with "/"/"//" -- Natural's own next-line marker --
    must fold into one INPUT interaction row, the same way WRITE/DISPLAY/
    PRINT operand lists already do (issue #24). Previously the colspec fold
    was scoped to WRITE/DISPLAY/PRINT only and didn't allow a leading slash,
    so this fell through entirely -- three lines, three unparsed_line gaps,
    and a truncated INPUT with no idea what the window actually asked for."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT i.kind, i.fields FROM interaction i JOIN member m ON m.id = i.member_id
        WHERE m.name='MMP9800' AND i.kind='INPUT' AND i.line_no=15
        """
    ).fetchone()
    assert row is not None, "expected one folded INPUT interaction row at line 15"
    assert "1X" in row["fields"], f"column-spec continuation not folded in: {row['fields']!r}"


def test_compress_folds_into_clause_on_its_own_continuation_line(indexed_db):
    """`COMPRESS 'A' 'B' \\n INTO #MESSAGE` (MMP9800:18-19) -- the INTO
    clause commonly wraps onto its own line and wasn't a recognised
    continuation lead, so it fell through as its own unparsed_line gap and
    the COMPRESS rule_candidate never recorded where the result went."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT rc.condition FROM rule_candidate rc
        JOIN member m ON m.id = rc.member_id
        WHERE m.name='MMP9800' AND rc.construct='COMPRESS' AND rc.line_no=18
        """
    ).fetchone()
    assert row is not None, "expected one folded COMPRESS rule_candidate at line 18"
    assert "INTO #MESSAGE" in row["condition"], (
        f"INTO clause not folded into the COMPRESS condition: {row['condition']!r}"
    )


def test_bare_boolean_assignment_is_captured_as_a_rule_candidate():
    """`#FLAG := TRUE` has no quoted string or number -- `TRUE`/`FALSE` are
    bare Natural keywords, invisible to mask_literals/NUMLIT alike -- but a
    boolean flag assignment is exactly the fixed-value business decision
    _match_arithmetic exists to capture, the same as a status code or a
    return code would be."""
    conn = _extract("#FLAG := TRUE\n")
    row = conn.execute(
        "SELECT construct, literals, fields_used FROM rule_candidate WHERE line_no=1"
    ).fetchone()
    assert row is not None, "expected #FLAG := TRUE to be captured as a rule_candidate"
    assert row["construct"] == "ASSIGN"
    assert row["literals"] == "TRUE"
    assert row["fields_used"] == "FLAG"


def test_bare_boolean_false_assignment_is_captured():
    conn = _extract("#FLAG := FALSE\n")
    row = conn.execute(
        "SELECT literals FROM rule_candidate WHERE line_no=1"
    ).fetchone()
    assert row is not None
    assert row["literals"] == "FALSE"


def test_if_condition_against_true_records_true_as_a_literal():
    """An `IF #FLAG = TRUE` condition previously recorded `literals=None` --
    the same gap as the ASSIGN case, just on the read side rather than the
    write side. Fixing _condition_facts fixes both from one place."""
    conn = _extract("IF #FLAG = TRUE\n  IGNORE\nEND-IF\n")
    row = conn.execute(
        "SELECT literals, fields_used FROM rule_candidate WHERE line_no=1 AND construct='IF'"
    ).fetchone()
    assert row is not None
    assert row["literals"] == "TRUE"
    assert row["fields_used"] == "FLAG"


def test_boolean_literal_match_does_not_fire_inside_an_unrelated_identifier():
    """`TRUE`/`FALSE` must only match as their own token -- not as a
    substring of a longer field/variable name that happens to contain one
    (e.g. a field literally named `#TRUEUP-FLAG`)."""
    conn = _extract("#TRUEUP-FLAG := 1\n")
    row = conn.execute(
        "SELECT literals FROM rule_candidate WHERE line_no=1"
    ).fetchone()
    assert row is not None
    assert row["literals"] == "1"


def test_input_using_map_literal_is_not_dynamic():
    """`INPUT USING MAP 'MMM0200'` (a quoted literal) is the static, common
    case -- both the interaction row and its paired INCLUDE call_edge must
    record dynamic=0, and no dynamic_target gap should be raised."""
    conn = _extract("INPUT USING MAP 'MMM0200'\n")
    row = conn.execute(
        "SELECT target, dynamic FROM interaction WHERE kind='INPUT' AND line_no=1"
    ).fetchone()
    assert row is not None
    assert row["target"] == "MMM0200"
    assert row["dynamic"] == 0
    edge = conn.execute(
        "SELECT dynamic FROM call_edge WHERE callee_name='MMM0200' AND call_kind='INCLUDE'"
    ).fetchone()
    assert edge["dynamic"] == 0
    assert conn.execute("SELECT 1 FROM gap WHERE gap_kind='dynamic_target'").fetchone() is None


def test_input_using_map_variable_target_is_dynamic():
    """`INPUT USING MAP #MAP-NAME` holds the map name in a field, not a
    literal -- the exact case `_clean_target` already distinguishes for
    CALLNAT/PERFORM/FETCH targets (issue: interaction had no equivalent).
    Both the interaction row and its INCLUDE call_edge must record
    dynamic=1, and a dynamic_target gap must be raised."""
    conn = _extract("INPUT USING MAP #MAP-NAME\n")
    row = conn.execute(
        "SELECT target, dynamic FROM interaction WHERE kind='INPUT' AND line_no=1"
    ).fetchone()
    assert row is not None
    assert row["dynamic"] == 1
    edge = conn.execute(
        "SELECT dynamic FROM call_edge WHERE call_kind='INCLUDE' AND line_no=1"
    ).fetchone()
    assert edge["dynamic"] == 1
    gap = conn.execute(
        "SELECT detail FROM gap WHERE gap_kind='dynamic_target'"
    ).fetchone()
    assert gap is not None
    assert "#MAP-NAME" in gap["detail"]


def test_bare_update_and_delete_with_no_view_are_recognised():
    """`UPDATE`/`DELETE` with nothing after them at all -- the common form
    that acts on whatever record the enclosing FIND/READ loop currently
    holds -- must not read as an unparsed_line gap just because there is no
    trailing view name to anchor a `\\s+` after the verb. With no loop label
    in play, the target genuinely can't be resolved from this snippet alone,
    so it must still land as an honest unresolved data_access row (the same
    outcome the labelled-but-unopened case gets in
    test_loop_label_resolution.py) rather than disappearing entirely."""
    conn = _extract(
        "FIND ORDER-VIEW WITH ORDER-KEY = #KEY\n"
        "  UPDATE\n"
        "END-FIND\n"
        "FIND LOG-VIEW WITH LOG-KEY = #KEY\n"
        "  DELETE\n"
        "END-FIND\n"
    )
    assert conn.execute("SELECT 1 FROM gap WHERE gap_kind='unparsed_line' AND line_no IN (2, 5)").fetchone() is None
    rows = {r["verb"]: r["confidence"] for r in conn.execute("SELECT verb, confidence FROM data_access")}
    assert rows["UPDATE"] == "unresolved"
    assert rows["DELETE"] == "unresolved"


def test_find_and_read_with_no_space_before_occurrence_count():
    """`FIND(1) VIEW WITH ...` / `READ(1) VIEW ...` -- some exports drop the
    space before the occurrence-count parenthesis. Must still resolve the
    view, and a longer identifier that merely starts with the same letters
    (`READY`) must not be misread as the READ verb."""
    conn = _extract(
        "FIND(1) ORDER-VIEW WITH ORDER-KEY = #KEY\n"
        "READ(1) ORDER-VIEW BY ORDER-KEY STARTING FROM #KEY\n"
        "READY := TRUE\n"
    )
    assert conn.execute("SELECT 1 FROM gap WHERE gap_kind='unparsed_line' AND line_no IN (1, 2)").fetchone() is None
    verbs = {r["verb"] for r in conn.execute("SELECT verb FROM data_access")}
    assert "FIND" in verbs
    assert "READ" in verbs
    assert conn.execute("SELECT 1 FROM data_access WHERE line_no=3").fetchone() is None


def test_set_key_is_a_recognised_no_op():
    """`SET KEY ALL` governs which PF-keys are active on the next input
    statement -- presentation, not a business decision, recognised the same
    way as the existing `SET CONTROL` case so it doesn't read as an
    unparsed_line gap."""
    conn = _extract("SET KEY ALL\n")
    assert conn.execute("SELECT 1 FROM gap WHERE gap_kind='unparsed_line'").fetchone() is None


def test_reject_if_is_recorded_as_a_rule_candidate():
    """`REJECT IF <cond>` drops the current FIND/READ loop iteration -- a
    real filtering decision, unlike SET/RESET/IGNORE, so it must land in
    rule_candidate with its condition intact, not just be swallowed."""
    conn = _extract("FIND ORDER-VIEW WITH ORDER-KEY = #KEY\n  REJECT IF ORDER-STATUS NE 'OPEN'\n")
    row = conn.execute(
        "SELECT condition FROM rule_candidate WHERE construct='REJECT IF'"
    ).fetchone()
    assert row is not None
    assert "OPEN" in row["condition"]


def test_find_condition_wrapped_with_sorted_by_or_where_is_folded():
    """A `FIND ... WITH` condition commonly wraps onto its own line with a
    `SORTED BY`/`WHERE` clause. Both must fold into the FIND's own recorded
    condition text, not just get individually swallowed."""
    conn = _extract(
        "FIND ORDER-VIEW WITH ORDER-KEY = #KEY\n"
        "    SORTED BY ORDER-DATE\n"
    )
    row = conn.execute("SELECT raw FROM data_access WHERE verb='FIND'").fetchone()
    assert row is not None
    assert "SORTED BY ORDER-DATE" in row["raw"]


def test_hash_prefixed_relabelled_statement_label_is_recognised():
    """Some export/conversion tools renumber statement labels with a
    leading '#'/'&' (e.g. '##L100.'), which collides with nothing else since
    a bare '#'/'&' is never itself a legal label start."""
    conn = _extract("##L100. FIND ORDER-VIEW WITH ORDER-KEY = #KEY\n")
    assert conn.execute("SELECT 1 FROM gap WHERE gap_kind='unparsed_line' AND line_no=1").fetchone() is None
    assert conn.execute("SELECT 1 FROM data_access WHERE verb='FIND' AND line_no=1").fetchone() is not None


def test_bare_label_line_with_nothing_after_it_is_not_a_gap():
    """A label alone on its own line (the statement it targets is on the
    next line) must not read as an unparsed_line gap."""
    conn = _extract("MYLABEL.\nRESET #FLAG\n")
    assert conn.execute("SELECT 1 FROM gap WHERE gap_kind='unparsed_line' AND line_no=1").fetchone() is None


def test_define_window_and_its_attribute_lines_are_not_a_gap():
    """DEFINE WINDOW and its attribute clauses (SIZE/BASE/FRAMED/FORMAT,
    routinely one per line) are presentation, not a business decision --
    recognised as a no-op the same way SET CONTROL/SET KEY are."""
    conn = _extract(
        "DEFINE WINDOW WIN1\n"
        "  SIZE 12*46\n"
        "  BASE 7/18\n"
        "  FRAMED ON POSITION TEXT MORE\n"
        "  FORMAT CD=BL\n"
    )
    assert conn.execute("SELECT 1 FROM gap WHERE gap_kind='unparsed_line'").fetchone() is None


def test_a_line_folded_into_a_preceding_statement_does_not_also_gap_on_its_own():
    """A continuation line that already folded successfully into the
    statement it continues (here: the IF's wrapped `AND` clause) must not
    also be flagged as its own unparsed_line gap -- its content wasn't
    lost, so a second, independent gap for the same line is just noise."""
    conn = _extract(
        "IF ORDER-STATUS NE 'A'\n"
        "  AND ORDER-STATUS NE 'B'\n"
        "  MOVE 'HOLD' TO STATUS\n"
        "END-IF\n"
    )
    assert conn.execute("SELECT 1 FROM gap WHERE gap_kind='unparsed_line' AND line_no=2").fetchone() is None
    row = conn.execute("SELECT condition FROM rule_candidate WHERE construct='IF'").fetchone()
    assert row is not None
    assert "ORDER-STATUS NE 'B'" in row["condition"]
