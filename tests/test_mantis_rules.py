"""Guards specific to the Mantis rule-candidate scanner."""

from __future__ import annotations


def test_continuation_fold_joins_a_condition_wrapped_with_a_quote_marker(indexed_db):
    """`IF ORDER_WT > 500` wrapping onto `'OR CUST_NO = " "` on the next line
    (ORDENQ.mantis's appended VALIDATE_CREDIT_LIMIT entry) must fold into one
    condition rather than being truncated at the first physical line. Unlike
    Natural's implicit continuation, this export style marks a continuation
    line explicitly with a leading `'`, so the fold has no ambiguity to
    resolve -- missing it would still produce a complete-looking but silently
    truncated citation.

    This example is appended after the file's original EXIT rather than
    inserted into MAIN, deliberately -- inserting mid-file would renumber
    every later line and silently stale the line citations already baked
    into the checked-in generated docs under examples/outputs/ that cite
    MAIN's statements."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT rc.condition FROM rule_candidate rc
        JOIN member m ON m.id = rc.member_id
        WHERE m.name='ORDENQ' AND rc.construct='IF' AND rc.condition LIKE '%ORDER_WT%'
        """
    ).fetchone()
    assert row is not None, "expected an IF rule candidate for ORDENQ"
    assert "CUST_NO" in row["condition"], (
        f"condition truncated at the wrap point, lost the OR clause: {row['condition']!r}"
    )


def test_continuation_line_is_still_visited_and_gapped_on_its_own(indexed_db):
    """The fold only fixes the condition it's merged into -- the continuation
    line itself must still get its own source_line row and still fail to
    stand alone as a statement, the same accepted double-visit behaviour
    Natural's own continuation fold relies on (see test_natural_rules.py).
    A regression that skips re-visiting it would silently under-count
    source_lines/code_lines for the member."""
    conn = indexed_db
    row = conn.execute(
        """
        SELECT g.raw FROM gap g
        JOIN member m ON m.id = g.member_id
        WHERE m.name='ORDENQ' AND g.gap_kind='unparsed_line' AND g.raw LIKE "%CUST_NO%"
        """
    ).fetchone()
    assert row is not None, "continuation line must still raise its own unparsed_line gap"


def test_entry_only_program_still_gets_object_type_program(indexed_db):
    """SCRNENT.mantis has no `PROGRAM "name"` self-declaration -- its only
    self-identifying statement is `ENTRY SCRNENT(...)`, the same shape as a
    real site's CICS-style online/screen program. Without this, such a
    member never gets an object_type and silently falls out of every
    batchable-member query (module docs, test-plan, test-gen) with no error
    -- a real client codebase hit exactly this."""
    conn = indexed_db
    row = conn.execute(
        "SELECT object_type FROM member WHERE name='SCRNENT'"
    ).fetchone()
    assert row is not None, "expected SCRNENT to be ingested"
    assert row["object_type"] == "program"
