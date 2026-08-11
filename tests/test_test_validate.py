"""Guards for the generated-test validation pass (validate_test_doc,
mfdoc test-validate)."""

from __future__ import annotations

from mfdoc import testplan
from mfdoc.validate import validate_test_doc, validate_tests_tree

VALID_DOC = """---
title: "MMP0100 -- generated tests (python)"
doc_type: generated_test
system: MOM
module: MMP0100
language: python
framework: pytest
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-01-01"
review_status: draft
reviewers: []
confidence_summary:
  verified: 1
  inferred: 0
  unresolved: 0
sources: ["MMP0100"]
---

# MMP0100 -- generated tests

```python
def test_rejects_unconfirmed_order():
    # MMP0100:BR-004 [[MMP0100:38-40]]
    ...
```
"""


def test_valid_doc_with_real_scenario_ref_passes(indexed_db, tmp_path):
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    path.write_text(VALID_DOC, encoding="utf-8")
    result = validate_test_doc(conn, path)
    assert result["ok"], result["problems"]
    assert result["invalid_scenario_refs"] == 0


def test_invented_scenario_id_is_flagged(indexed_db, tmp_path):
    """MMP0100:BR-999 doesn't exist -- a model inventing or renumbering a
    scenario id must be caught, the same way an invalid [[MEMBER:LINE]]
    citation is."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    bad = VALID_DOC.replace("MMP0100:BR-004", "MMP0100:BR-999")
    path = tmp_path / "MMP0100.md"
    path.write_text(bad, encoding="utf-8")
    result = validate_test_doc(conn, path)
    assert not result["ok"]
    assert result["invalid_scenario_refs"] == 1
    assert any("BR-999" in p for p in result["problems"])


def test_missing_language_or_framework_front_matter_is_flagged(indexed_db, tmp_path):
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    bad = VALID_DOC.replace("language: python\n", "").replace("framework: pytest\n", "")
    path = tmp_path / "MMP0100.md"
    path.write_text(bad, encoding="utf-8")
    result = validate_test_doc(conn, path)
    assert not result["ok"]
    assert any("language" in p for p in result["problems"])
    assert any("framework" in p for p in result["problems"])


def test_validate_tests_tree_aggregates_across_files(indexed_db, tmp_path):
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    (tmp_path / "good.md").write_text(VALID_DOC, encoding="utf-8")
    (tmp_path / "bad.md").write_text(
        VALID_DOC.replace("MMP0100:BR-004", "MMP0100:BR-999"), encoding="utf-8"
    )
    res = validate_tests_tree(conn, tmp_path)
    assert res["documents"] == 2
    assert res["documents_ok"] == 1
    assert res["invalid_scenario_refs"] == 1
