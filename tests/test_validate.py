"""Guards on the validator itself -- the traceability promise is only worth
something if it is mechanically enforced. Each of these is a case the
validator must reject, plus a positive control (the worked example) that it
must accept unchanged.
"""

from __future__ import annotations

from pathlib import Path

from mfdoc.validate import validate_doc

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
"""


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


def test_validator_accepts_the_worked_example_unchanged(indexed_db):
    """A false positive here trains people to ignore the validator, which is
    worse than not having one."""
    result = validate_doc(indexed_db, REPO_ROOT / "examples" / "MMP0100-worked-example.md")
    assert result["ok"], result["problems"]
    assert result["invalid_citations"] == 0
