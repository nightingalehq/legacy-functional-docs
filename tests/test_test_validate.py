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


SIDECAR_DOC = """---
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

See [`MMP0100.py`](./MMP0100.py) for the generated test source.

## Scenarios covered

- MMP0100:BR-004
"""


def test_stale_sidecar_from_renumbering_is_treated_as_absent(indexed_db, tmp_path):
    """Issue #195: `write_test_doc_with_sidecar` only overwrites the on-disk
    sidecar after a *successful* validation. If a `classify-rules`/`derive`
    rebuild renumbers `rule_candidate` rows (and therefore `test_case`'s
    BR-ids) after that sidecar was last written, the sidecar's own BR-ids no
    longer match anything current -- a freshly generated manifest citing the
    *new*, correct numbering must not be flagged as "not found in the
    sidecar" just because the sidecar on disk predates the renumbering. The
    sidecar's BR-ids (BR-999) matching nothing in `test_case` is exactly the
    signal that lets `validate_test_doc` tell an actually-stale sidecar
    apart from a real mismatch."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    path.write_text(SIDECAR_DOC, encoding="utf-8")
    # Simulates the old numbering the sidecar was written under, before a
    # rule_candidate renumbering -- BR-999 does not exist in test_case.
    sidecar.write_text(
        "def test_rejects_unconfirmed_order():\n"
        "    # MMP0100:BR-999\n"
        "    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path)
    assert result["sidecar_stale"] is True
    assert result["ok"], result["problems"]
    assert result["invalid_scenario_refs"] == 0
    assert not any("BR-999" in p for p in result["problems"])
    assert not any("not found in" in p for p in result["problems"])


def test_partial_positional_shift_sidecar_is_still_treated_as_stale(tmp_path):
    """Copilot review follow-up on issue #195: a partial positional
    renumbering (only some ids move) must still trip the staleness guard.
    If the check used `any(...)` instead of `all(...)`, an id that happens
    to still match by coincidence would make the whole sidecar look
    "current" and the cross-check would report a false "not found"/
    "missing from manifest" for every id that genuinely shifted, exactly
    the systemic false-positive issue #195 exists to eliminate -- just for
    a subset of ids instead of all of them."""
    import sqlite3

    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    # Current numbering, as if a renumbering shifted every id up by one --
    # BR-002/BR-003 coincidentally still exist under the new numbering too.
    for n in (2, 3, 4):
        insert(
            conn, "test_case", member_id=1, kind="unit", scenario_name=f"FAKEMOD:BR-{n:03d}",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
            status="characterization", citation="FAKEMOD:1", confidence="verified",
        )
    conn.commit()

    path = tmp_path / "FAKEMOD.md"
    sidecar = tmp_path / "FAKEMOD.py"
    # The manifest reflects a freshly generated document under the *new*
    # numbering (BR-002..BR-004); the sidecar on disk is the *old* one
    # (BR-001..BR-003), written before the renumbering.
    path.write_text(
        """---
title: "FAKEMOD -- generated tests (python)"
doc_type: generated_test
system: MOM
module: FAKEMOD
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
sources: ["FAKEMOD"]
---

# FAKEMOD -- generated tests

See [`FAKEMOD.py`](./FAKEMOD.py) for the generated test source.

## Scenarios covered

- FAKEMOD:BR-002
- FAKEMOD:BR-003
- FAKEMOD:BR-004
""",
        encoding="utf-8",
    )
    sidecar.write_text(
        "def test_one():\n    # FAKEMOD:BR-001\n    ...\n\n"
        "def test_two():\n    # FAKEMOD:BR-002\n    ...\n\n"
        "def test_three():\n    # FAKEMOD:BR-003\n    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path)
    assert result["sidecar_stale"] is True
    assert result["ok"], result["problems"]
    assert result["invalid_scenario_refs"] == 0
    assert not any("BR-001" in p or "BR-004" in p for p in result["problems"])


def test_current_sidecar_still_cross_checked_against_manifest(indexed_db, tmp_path):
    """A sidecar whose BR-ids *do* all match current `test_case` rows is
    still authoritative -- the staleness guard must not swallow a genuine
    manifest/sidecar mismatch. `MMP0100:BR-001` is a real, current
    `test_case` scenario, distinct from the manifest's `BR-004` -- so the
    `all(...)` staleness check finds a fully-resolving (not stale) sidecar
    whose content genuinely disagrees with the manifest, exercising the
    cross-check branch rather than the `not code_ids` short-circuit."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    # Manifest claims BR-004; sidecar's real content only has BR-001 -- a
    # genuine drift (e.g. hand-edited manifest), not a renumbering, since
    # both ids are real and current.
    path.write_text(SIDECAR_DOC, encoding="utf-8")
    sidecar.write_text(
        "def test_something_else():\n    # MMP0100:BR-001\n    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path)
    assert result["sidecar_stale"] is False
    assert not result["ok"]
    assert any(
        "BR-004" in p and "not found in" in p for p in result["problems"]
    )
    assert any(
        "BR-001" in p and "missing from" in p for p in result["problems"]
    )


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


OTHER_PROJECT_DOC = """---
title: "OTHERSYS-MOD1 -- generated tests (python)"
doc_type: generated_test
system: OTHERSYS
module: OTHERSYS-MOD1
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
sources: ["OTHERSYS-MOD1"]
---

# OTHERSYS-MOD1 -- generated tests

```python
def test_something():
    # OTHERSYS-MOD1:BR-001 [[OTHERSYS-MOD1:1]]
    ...
```
"""


def test_validate_tests_tree_skips_a_document_belonging_to_a_different_project(indexed_db, tmp_path):
    """A shared parent `--docs` directory holding more than one project's
    generated-test output (see issue #130) must not have a scenario id from
    project B's test tree reported as "not a known test_case scenario"
    against project A's `test_case` store -- `sources` naming only members
    this fact store never ingested is a strong signal the document belongs
    to a different project, and must be skipped rather than cross-checked
    against the wrong one."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    (tmp_path / "other-project.md").write_text(OTHER_PROJECT_DOC, encoding="utf-8")
    (tmp_path / "good.md").write_text(VALID_DOC, encoding="utf-8")
    res = validate_tests_tree(conn, tmp_path)
    assert res["documents"] == 1
    assert res["documents_ok"] == 1
    assert res["invalid_scenario_refs"] == 0
    assert len(res["out_of_scope_documents"]) == 1
    assert "other-project.md" in res["out_of_scope_documents"][0]
    assert "OTHERSYS-MOD1" in res["out_of_scope_documents"][0]


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
