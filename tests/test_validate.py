"""Guards on the validator itself -- the traceability promise is only worth
something if it is mechanically enforced. Each of these is a case the
validator must reject, plus a positive control (the worked example) that it
must accept unchanged.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mfdoc.db import SCHEMA, insert
from mfdoc.dialects import natural
from mfdoc.validate import validate_doc, validate_tree

REPO_ROOT = Path(__file__).resolve().parent.parent

GOOD_FRONTMATTER = """---
title: Test doc
doc_type: module
system: MOM
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-01-01T00:00:00"
review_status: draft
confidence_summary:
  verified: 1
sources:
  - MMP0100
---
# Test doc
"""


def test_containing_paragraph_returns_the_full_paragraph_and_relative_offset():
    from mfdoc.validate import _containing_paragraph

    body = "First paragraph, one sentence.\n\nSecond paragraph. It has [[X:1]] a citation. And more text.\n\nThird paragraph."
    cite_start = body.index("[[X:1]]")
    cite_end = cite_start + len("[[X:1]]")

    para, rel_start = _containing_paragraph(body, cite_start, cite_end)

    assert para == "Second paragraph. It has [[X:1]] a citation. And more text."
    assert para[rel_start:rel_start + len("[[X:1]]")] == "[[X:1]]"


def test_validator_rejects_out_of_range_line(indexed_db, tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(GOOD_FRONTMATTER + "\nThe program moves the field [[MMP0100:999999]].\n")
    result = validate_doc(indexed_db, doc)
    assert not result["ok"]
    assert any("outside" in p for p in result["problems"])


def test_validator_rejects_unknown_member(indexed_db, tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(GOOD_FRONTMATTER + "\nThe program moves the field [[NOSUCHMEMBER:1]].\n")
    result = validate_doc(indexed_db, doc)
    assert not result["ok"]
    assert any("is not in the index" in p for p in result["problems"])


def test_validator_rejects_missing_frontmatter_key(indexed_db, tmp_path):
    bad_fm = GOOD_FRONTMATTER.replace("doc_type: module\n", "")
    doc = tmp_path / "doc.md"
    doc.write_text(bad_fm + "\nThe program moves the field [[MMP0100:1]].\n")
    result = validate_doc(indexed_db, doc)
    assert not result["ok"]
    assert any("doc_type" in p for p in result["problems"])


def test_validator_rejects_bad_review_status(indexed_db, tmp_path):
    bad_fm = GOOD_FRONTMATTER.replace("review_status: draft", "review_status: bogus")
    doc = tmp_path / "doc.md"
    doc.write_text(bad_fm + "\nThe program moves the field [[MMP0100:1]].\n")
    result = validate_doc(indexed_db, doc)
    assert not result["ok"]
    assert any("review_status" in p for p in result["problems"])


def test_validator_rejects_uncited_assertion(indexed_db, tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(
        GOOD_FRONTMATTER + "\nThe program validates the order status before release.\n"
    )
    result = validate_doc(indexed_db, doc)
    assert not result["ok"]
    assert any("no citation" in p for p in result["problems"])


def test_validator_accepts_inferred_as_a_hedge(indexed_db, tmp_path):
    """`(inferred)` is a first-class confidence marker per
    reference/writing-rules.md, on equal footing with `unresolved` -- an
    assertive sentence hedged this way but with no citation of its own
    (e.g. because the citation sits in the sentence before it) must not be
    flagged, the same as it would not be for `unresolved`."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        GOOD_FRONTMATTER
        + "\nThe module writes the field [[MMP0100:1]]. The program validates "
          "the order status before release, *(inferred)* from how the result "
          "is used downstream.\n"
    )
    result = validate_doc(indexed_db, doc)
    assert result["ok"], result["problems"]


def test_validator_accepts_a_well_formed_document(indexed_db, tmp_path):
    """Positive control: a document with valid front matter, a properly
    cited assertion, and no uncited claims must pass clean."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        GOOD_FRONTMATTER
        + "\nThe program resets the return code at the top of processing [[MMP0100:31]].\n"
    )
    result = validate_doc(indexed_db, doc)
    assert result["ok"], result["problems"]
    assert result["invalid_citations"] == 0


def test_citation_to_a_merged_ddm_fdt_entity_resolves_via_defined_in(indexed_db, tmp_path):
    """MILL-ORDER is ingested as two `member` rows (a DDM and an FDT report,
    dialects 'ddm'/'adabas_fdt', both with no library) that graph.py merges
    into one `entity` row -- entity_brief cites the entity's own facts
    through whichever member `entity.defined_in` names. A citation to that
    same bare name must resolve the same way instead of reporting a false
    'ambiguous across libraries' (there is no library on either row to
    qualify by), or every entity_brief-derived citation for a merged
    DDM/FDT entity would be unvalidatable."""
    doc = tmp_path / "doc.md"
    doc.write_text(GOOD_FRONTMATTER + "\nThe field is defined here [[MILL-ORDER:1]].\n")
    result = validate_doc(indexed_db, doc)
    assert result["ok"], result["problems"]
    assert result["invalid_citations"] == 0


def test_validator_accepts_a_register_doc_without_review_fields(indexed_db, tmp_path):
    """`doc_type: register` docs (rules-register, test-plan-register,
    testability-advisory) are deterministic index reports, not narrative
    docs with a review workflow -- they must not be required to carry
    review_status/confidence_summary/generated_at/sources, only enough
    front matter to identify what they are."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        '---\ntitle: "System-wide rules register"\ndoc_type: register\n---\n'
        "\n| BR-ID |\n|---|\n"
        "\n[[MMP0100:1]] not part of a real sentence, just exercising citation checks.\n"
    )
    result = validate_doc(indexed_db, doc)
    assert result["ok"], result["problems"]


def test_validator_still_rejects_a_register_doc_missing_doc_type_name(indexed_db, tmp_path):
    """The reduced register contract still requires `title` -- it's not a
    blanket exemption for anything lacking full front matter."""
    doc = tmp_path / "doc.md"
    doc.write_text('---\ndoc_type: register\n---\n\nno title here.\n')
    result = validate_doc(indexed_db, doc)
    assert not result["ok"]
    assert any("title" in p for p in result["problems"])


def test_validate_tree_skips_readme_files(indexed_db, tmp_path):
    """A directory of pipeline output (e.g. examples/outputs/) may legitimately
    have its own README.md alongside real generated docs -- that file has no
    front matter and was never meant to satisfy this contract, so a tree walk
    must skip it rather than reporting a false failure."""
    (tmp_path / "README.md").write_text("# Not a pipeline document\n", encoding="utf-8")
    (tmp_path / "doc.md").write_text(
        GOOD_FRONTMATTER + "\nThe program resets the return code [[MMP0100:31]].\n"
    )
    res = validate_tree(indexed_db, tmp_path)
    assert res["documents"] == 1
    assert res["documents_ok"] == 1


def test_validator_accepts_the_worked_example_unchanged(indexed_db):
    """A false positive here trains people to ignore the validator, which is
    worse than not having one."""
    result = validate_doc(
        indexed_db, REPO_ROOT / "examples" / "outputs" / "docs" / "natural" / "MILLPROD" / "MMP0100.md"
    )
    assert result["ok"], result["problems"]
    assert result["invalid_citations"] == 0


def _member_with_return_code_if_else():
    """A minimal in-memory index with one Natural member scanned for real
    (not hand-built rule_candidate rows), mirroring the exact shape of a
    real reversed-condition finding: `IF <outcome-field> NE '<code>' ...
    ELSE ... END-IF`, where the ELSE branch is the success path."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    mid = insert(conn, "member", name="TESTCOND", dialect="natural", object_type="subprogram")
    lines = [
        "IF #RETURN-CODE NE '0000'",
        "  BACKOUT TRANSACTION",
        "ELSE",
        "  COMPRESS 'message sent successfully' INTO #MSG",
        "  END TRANSACTION",
        "END-IF",
    ]
    natural.extract(conn, mid, [(i + 1, None, line) for i, line in enumerate(lines)], "TESTCOND")
    conn.commit()
    return conn


COND_FRONTMATTER = """---
title: Test doc
doc_type: module
system: MOM
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-01-01T00:00:00"
review_status: draft
confidence_summary:
  verified: 1
sources:
  - TESTCOND
---
# Test doc
"""


def test_validator_flags_reversed_success_condition(tmp_path):
    """The IF's own condition is `#RETURN-CODE NE '0000'` (true => failure,
    handled by BACKOUT), so the success path -- the ELSE branch -- runs when
    `#RETURN-CODE` *equals* '0000'. A sentence describing the successful case
    as `#RETURN-CODE` being *not* '0000' has the direction backwards."""
    conn = _member_with_return_code_if_else()
    doc = tmp_path / "doc.md"
    doc.write_text(
        COND_FRONTMATTER
        + "\nAfter a successful transmission (`#RETURN-CODE` not `'0000'`), "
          "the message is shown [[TESTCOND:1-6]].\n"
    )
    result = validate_doc(conn, doc)
    assert not result["ok"]
    assert any("comparison direction may be reversed" in p for p in result["problems"])
    assert any("0000" in p for p in result["problems"])


def test_validator_accepts_correctly_stated_success_condition(tmp_path):
    conn = _member_with_return_code_if_else()
    doc = tmp_path / "doc.md"
    doc.write_text(
        COND_FRONTMATTER
        + "\nAfter a successful transmission (`#RETURN-CODE` equals `'0000'`), "
          "the message is shown [[TESTCOND:1-6]].\n"
    )
    result = validate_doc(conn, doc)
    assert not any("comparison direction may be reversed" in p for p in result["problems"])


def test_validator_accepts_correctly_stated_failure_condition(tmp_path):
    """No success/failure hint spans both branches here -- the citation only
    covers the IF line itself, so the check compares directly against the
    IF's own (uninverted) condition."""
    conn = _member_with_return_code_if_else()
    doc = tmp_path / "doc.md"
    doc.write_text(
        COND_FRONTMATTER
        + "\nIf the transmission fails (`#RETURN-CODE` is not `'0000'`), "
          "the transaction is backed out [[TESTCOND:1-2]].\n"
    )
    result = validate_doc(conn, doc)
    assert not any("comparison direction may be reversed" in p for p in result["problems"])


def test_validator_ignores_reversed_wording_on_a_non_outcome_field(tmp_path):
    """The check is scoped to outcome-shaped fields (return/response codes,
    status, flags) -- an unrelated field must never trigger it, reversed
    wording or not."""
    conn = _member_with_return_code_if_else()
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES "
        "((SELECT id FROM member WHERE name='TESTCOND'), 100, 'IF #COIL-ID = ''12345''')"
    )
    insert(
        conn, "rule_candidate",
        member_id=conn.execute("SELECT id FROM member WHERE name='TESTCOND'").fetchone()["id"],
        line_no=100, construct="IF", condition="IF #COIL-ID = '12345'",
        raw="IF #COIL-ID = '12345'",
    )
    conn.commit()
    doc = tmp_path / "doc.md"
    doc.write_text(
        COND_FRONTMATTER
        + "\nThe coil is accepted when the id is not `'12345'` [[TESTCOND:100]].\n"
    )
    result = validate_doc(conn, doc)
    assert not any("comparison direction may be reversed" in p for p in result["problems"])


def _member_with_n_rules(name: str, n: int):
    """An in-memory index with `name` having `n` trivial rule_candidate rows
    (real member row via `insert`, not a full source scan -- the
    completeness check only cares about `rule_candidate` count/order and
    `_rule_id`'s numbering, not any particular construct shape)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    insert(conn, "member", name=name, dialect="mantis", object_type="program")
    mid = conn.execute("SELECT id FROM member WHERE name=?", (name,)).fetchone()["id"]
    for i in range(n):
        insert(
            conn, "rule_candidate", member_id=mid, line_no=(i + 1) * 10,
            construct="IF", condition="X = 1", raw="IF X = 1",
        )
    conn.commit()
    return conn


def _module_result(member: str, body: str) -> dict:
    return {"_fm": {"doc_type": "module", "sources": [member]}, "_body": body}


def test_module_completeness_flags_a_rule_never_cited(tmp_path):
    from mfdoc.validate import module_completeness_problems

    conn = _member_with_n_rules("TESTMOD", 3)
    results = [_module_result("TESTMOD", "See TESTMOD:BR-001 and TESTMOD:BR-003 here.")]
    problems = module_completeness_problems(conn, results)
    assert len(problems) == 1
    assert "TESTMOD" in problems[0]
    assert "1/3" in problems[0]
    assert "TESTMOD:BR-002" in problems[0]


def test_module_completeness_accepts_full_coverage():
    from mfdoc.validate import module_completeness_problems

    conn = _member_with_n_rules("TESTMOD", 3)
    results = [_module_result(
        "TESTMOD", "TESTMOD:BR-001, TESTMOD:BR-002, and TESTMOD:BR-003 are all here."
    )]
    assert module_completeness_problems(conn, results) == []


def test_module_completeness_unions_coverage_across_chunk_documents():
    """A chunked member's rules are only ever complete in aggregate across
    its several chunk documents -- one chunk covering BR-001/002 and another
    covering BR-003 must not be flagged as incomplete."""
    from mfdoc.validate import module_completeness_problems

    conn = _member_with_n_rules("TESTMOD", 3)
    results = [
        _module_result("TESTMOD", "TESTMOD:BR-001 and TESTMOD:BR-002 are here."),
        _module_result("TESTMOD", "TESTMOD:BR-003 is here."),
    ]
    assert module_completeness_problems(conn, results) == []


def test_module_completeness_ignores_non_module_docs():
    """A generated-test or register doc citing a subset of BR-ids must not
    make a member look covered when no `doc_type: module` doc exists for it
    at all -- absence of a module doc is a different problem this check
    isn't meant to catch."""
    from mfdoc.validate import module_completeness_problems

    conn = _member_with_n_rules("TESTMOD", 3)
    results = [{
        "_fm": {"doc_type": "generated_test", "sources": ["TESTMOD"]},
        "_body": "TESTMOD:BR-001",
    }]
    assert module_completeness_problems(conn, results) == []


def test_validator_accepts_a_citation_placed_after_the_sentence_ending_period(indexed_db, tmp_path):
    """A citation placed right after the period that ends the claim it
    supports -- rather than before it, inside the same sentence -- must not
    make that claim look uncited just because the sentence-boundary regex
    treats a citation as a valid next-sentence opener."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        GOOD_FRONTMATTER
        + "\nThe program resets the return code at the top of processing. "
          "[[MMP0100:31]] A second, unrelated sentence follows.\n"
    )
    result = validate_doc(indexed_db, doc)
    assert not any("no citation" in p for p in result["problems"]), result["problems"]


def test_validator_still_rejects_a_genuinely_uncited_assertion_before_a_cited_one(indexed_db, tmp_path):
    """The rescue above must not swallow every uncited assertion -- only one
    immediately followed by a unit that opens with a citation."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        GOOD_FRONTMATTER
        + "\nThe program validates the order status before release. "
          "A second sentence with nothing to do with a citation follows.\n"
    )
    result = validate_doc(indexed_db, doc)
    assert not result["ok"]
    assert any("no citation" in p for p in result["problems"])


def test_validator_rejects_module_doc_not_opening_with_a_heading(indexed_db, tmp_path):
    """A response that narrates commentary (e.g. restating its own scope)
    before the actual document content, rather than opening with the
    template's required top-level heading, must be caught even when its
    front matter is otherwise well-formed -- this is the self-narrating-
    response failure shape, distinct from a dropped front-matter block."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        GOOD_FRONTMATTER.replace("# Test doc\n", "")
        + "\nI'll now document the module as instructed.\n\n"
          "The program resets the return code [[MMP0100:31]].\n"
    )
    result = validate_doc(indexed_db, doc)
    assert not result["ok"]
    assert any("does not open with a top-level" in p for p in result["problems"])


def test_validator_accepts_a_lower_level_heading_as_the_opening_line(indexed_db, tmp_path):
    """The check only requires *a* markdown heading to open the body, not
    specifically an H1 -- a stricter level requirement isn't what this
    guards against."""
    doc = tmp_path / "doc.md"
    doc.write_text(
        GOOD_FRONTMATTER.replace("# Test doc\n", "")
        + "\n## A section heading\n\nThe program resets the return code [[MMP0100:31]].\n"
    )
    result = validate_doc(indexed_db, doc)
    assert not any("does not open with a top-level" in p for p in result["problems"])


def test_validator_accepts_a_sentence_that_quotes_the_raw_ne_condition_before_explaining_it(tmp_path):
    """Regression: a sentence describing an IF's own condition often quotes
    it verbatim right next to the citation, then explains it in English
    afterward -- "`IF #RETURN-CODE NE '0000'` [[TESTCOND:1]] (the condition
    tests inequality...)". The first occurrence of the literal is the raw
    quotation; its own `NE` must be read as negation, not overridden by
    treating the sentence as claiming equality by default."""
    conn = _member_with_return_code_if_else()
    doc = tmp_path / "doc.md"
    doc.write_text(
        COND_FRONTMATTER
        + "\nThe routine checks `IF #RETURN-CODE NE '0000'` [[TESTCOND:1]] "
          "(the condition tests inequality -- not equal to '0000' -- so the "
          "failure branch below fires when a real error code has been set).\n"
    )
    result = validate_doc(conn, doc)
    assert not any("comparison direction may be reversed" in p for p in result["problems"]), result["problems"]


def test_validator_reads_the_real_sentence_when_a_trailing_citation_is_split_into_its_own_unit(tmp_path):
    """Regression: a numbered-list rule bullet with no blank line before the
    next item, whose only citation trails right after the closing period,
    gets that citation split into its own near-empty unit by SENTENCE_SPLIT
    -- the reversed-condition check must still read the real preceding
    sentence, not just the orphaned citation fragment, or every such
    citation looks like it has no polarity claim to check at all and a real
    reversal there would go undetected."""
    conn = _member_with_return_code_if_else()
    doc = tmp_path / "doc.md"
    doc.write_text(
        COND_FRONTMATTER
        + "\n31. **Entries are skipped unless the code equals `'0000'`** -- "
          "entries not in code `'0000'` are skipped. [[TESTCOND:1-6]]\n"
          "32. Something unrelated follows immediately, no blank line above.\n"
    )
    result = validate_doc(conn, doc)
    assert not any("comparison direction may be reversed" in p for p in result["problems"]), result["problems"]


def test_name_mentioned_finds_a_whole_token_match():
    from mfdoc.validate import _name_mentioned

    assert _name_mentioned("The program calls PGMX02 to continue.", "PGMX02")


def test_name_mentioned_is_case_insensitive():
    from mfdoc.validate import _name_mentioned

    assert _name_mentioned("the program calls pgmx02 to continue.", "PGMX02")


def test_name_mentioned_rejects_a_substring_match():
    """PGMX02 must not match inside PGMX023 -- a longer identifier that
    happens to share a prefix is not a real mention."""
    from mfdoc.validate import _name_mentioned

    assert not _name_mentioned("The program calls PGMX023 to continue.", "PGMX02")


def test_name_mentioned_matches_a_name_containing_special_charset_characters():
    """Member/program/file names legitimately contain #@$&-_. -- these are
    non-word characters that a plain \\b boundary would mishandle."""
    from mfdoc.validate import _name_mentioned

    assert _name_mentioned("See #GS-WKAREA for the shared area.", "#GS-WKAREA")
    assert not _name_mentioned("See #GS-WKAREA-EXT for the shared area.", "#GS-WKAREA")


def test_name_mentioned_returns_false_when_absent():
    from mfdoc.validate import _name_mentioned

    assert not _name_mentioned("The program calls another routine.", "PGMX02")


def _member_with_statements(**extra_rows):
    """A minimal in-memory index with one member (TESTSTMT) and, per
    `extra_rows`, a `call_edge`/`interaction`/`data_access` row at line 692
    inside a 691-693 source range -- mirrors the real DECIDE/FETCH/ESCAPE
    case from issue #59 without needing the dialect scanner to parse it.

    `extra_rows` keys: "call_edge", "interaction", "data_access", each a
    dict of column overrides merged onto a minimal valid row for that table.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    mid = insert(conn, "member", name="TESTSTMT", dialect="natural", object_type="subprogram")
    for line_no in range(691, 694):
        conn.execute(
            "INSERT INTO source_line (member_id, line_no, text) VALUES (?, ?, ?)",
            (mid, line_no, f"line {line_no}"),
        )
    if "call_edge" in extra_rows:
        row = {"caller_id": mid, "callee_name": "PGMX02", "call_kind": "FETCH",
               "dynamic": 0, "line_no": 692}
        row.update(extra_rows["call_edge"])
        insert(conn, "call_edge", **row)
    if "interaction" in extra_rows:
        row = {"member_id": mid, "target": "MAPX02", "kind": "CONVERSE", "line_no": 692}
        row.update(extra_rows["interaction"])
        insert(conn, "interaction", **row)
    if "data_access" in extra_rows:
        row = {"member_id": mid, "entity_name": "CUSTOMER-FILE", "verb": "READ",
               "crud": "R", "raw": "READ CUSTOMER-FILE", "line_no": 692}
        row.update(extra_rows["data_access"])
        insert(conn, "data_access", **row)
    conn.commit()
    return conn


STMT_FRONTMATTER = """---
title: Test doc
doc_type: module
system: MOM
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-01-01T00:00:00"
review_status: draft
confidence_summary:
  verified: 1
sources:
  - TESTSTMT
---
# Test doc
"""


def test_validator_flags_an_omitted_call_target(tmp_path):
    conn = _member_with_statements(call_edge={})
    doc = tmp_path / "doc.md"
    doc.write_text(
        STMT_FRONTMATTER
        + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert result["ok"], result["problems"]  # non-blocking: must not fail the doc
    assert any("PGMX02" in p and "FETCH" in p for p in result["omitted_statement_targets"])


def test_validator_ignores_a_call_target_named_elsewhere_in_the_paragraph(tmp_path):
    conn = _member_with_statements(call_edge={})
    doc = tmp_path / "doc.md"
    doc.write_text(
        STMT_FRONTMATTER
        + "\nThe branch first transfers control to PGMX02. "
          "It then exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert result["omitted_statement_targets"] == []


def test_validator_never_flags_a_dynamic_call_target(tmp_path):
    conn = _member_with_statements(call_edge={"dynamic": 1, "callee_name": "*PGM-NAME"})
    doc = tmp_path / "doc.md"
    doc.write_text(
        STMT_FRONTMATTER
        + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert result["omitted_statement_targets"] == []


def test_validator_flags_an_omitted_interaction_target(tmp_path):
    conn = _member_with_statements(interaction={})
    doc = tmp_path / "doc.md"
    doc.write_text(
        STMT_FRONTMATTER
        + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert any("MAPX02" in p and "CONVERSE" in p for p in result["omitted_statement_targets"])


def test_validator_flags_an_omitted_data_access_target(tmp_path):
    conn = _member_with_statements(data_access={})
    doc = tmp_path / "doc.md"
    doc.write_text(
        STMT_FRONTMATTER
        + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert any("CUSTOMER-FILE" in p and "READ" in p for p in result["omitted_statement_targets"])


def test_validator_scopes_statement_completeness_to_module_docs(tmp_path):
    """A register doc echoes source syntax/field-inventory phrasing verbatim
    -- same reasoning as why the reversed-condition check is module-only."""
    conn = _member_with_statements(call_edge={})
    doc = tmp_path / "doc.md"
    doc.write_text(
        "---\ntitle: Register\ndoc_type: register\n---\n"
        "# Register\n\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    result = validate_doc(conn, doc)
    assert result["omitted_statement_targets"] == []


def test_validate_tree_aggregates_omitted_statement_targets_across_documents(tmp_path):
    conn = _member_with_statements(call_edge={})
    (tmp_path / "doc1.md").write_text(
        STMT_FRONTMATTER + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    (tmp_path / "doc2.md").write_text(
        STMT_FRONTMATTER + "\nThe branch exits the transaction [[TESTSTMT:691-693]].\n"
    )
    res = validate_tree(conn, tmp_path)
    assert len(res["omitted_statement_targets"]) == 2
    # Advisory only -- must never affect pass/fail.
    assert res["documents_ok"] == res["documents"] == 2
