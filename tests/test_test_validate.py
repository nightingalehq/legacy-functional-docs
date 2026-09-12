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


def test_non_mapping_front_matter_is_flagged_not_crashed(indexed_db, tmp_path):
    """Copilot review follow-up on issue #195's fix: `mfdoc test-validate`
    reaches `validate_doc` (via `validate_test_doc`) before any of this
    function's own fingerprint/sidecar logic runs, so a document whose
    front matter parses as syntactically valid but non-mapping YAML must
    be reported as malformed here too, not crash `AttributeError` out of
    `doc_rule_fingerprint`/`fm.get`."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    path.write_text("---\n- a\n- b\n---\n\n# MMP0100 -- generated tests\n", encoding="utf-8")
    result = validate_test_doc(conn, path)  # must not raise
    assert not result["ok"]
    assert any("front matter is not a mapping" in p for p in result["problems"])


def test_malformed_language_front_matter_does_not_crash_the_sidecar_lookup(indexed_db, tmp_path):
    """Copilot review follow-up on issue #195's fix: `language` can be any
    YAML scalar shape a document's own (malformed/hand-edited) front matter
    allows, not just a string -- `sidecar_path_for`'s `LANGUAGE_EXTENSIONS.
    get(language)` used to raise `TypeError: unhashable type: 'list'` for
    `language: [python]`, crashing `mfdoc test-validate` outright. Treated
    the same as any other language `sidecar_path_for` doesn't recognise (no
    sidecar to cross-check against, falling back to scanning the body
    directly) -- not itself a validation failure, just no longer a crash."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    bad = VALID_DOC.replace("language: python\n", "language: [python]\n")
    path = tmp_path / "MMP0100.md"
    path.write_text(bad, encoding="utf-8")
    result = validate_test_doc(conn, path)  # must not raise
    assert result["ok"], result["problems"]


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
    # Not surfaced as a failure, but not silently dropped either.
    assert result["sidecar_unresolved_ids"] == ["MMP0100:BR-999"]


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
    # BR-001 is the one id that didn't survive the (simulated) renumbering
    # -- still visible here even though it isn't in `problems`.
    assert result["sidecar_unresolved_ids"] == ["FAKEMOD:BR-001"]


def test_stale_sidecar_whose_old_ids_remain_a_subset_after_an_insertion(indexed_db, tmp_path):
    """Copilot review follow-up on issue #195: an ID-overlap/subset check
    alone cannot detect a rule inserted *after* the sidecar's own BR-range.
    Old sidecar `{BR-001, BR-002, BR-003}` stays a literal subset of the
    current valid set `{BR-001, BR-002, BR-003, BR-004}` once a fourth rule
    is derived -- `code_ids <= valid_scenarios()` alone would read that as
    "still current" and cross-check the stale sidecar against a freshly
    generated manifest anyway, reproducing the exact false "not found"
    failure #195 exists to eliminate. The `test_case_fingerprint` this
    sidecar was written with (from the member's `rule_candidate` ordering
    at that time, via `testplan.doc_rule_fingerprint`) no longer matches
    the current one now that a fourth `rule_candidate` row exists -- this
    is the signal that actually catches this case, where ID-overlap alone
    cannot."""
    import sqlite3

    from mfdoc import testplan
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")

    def seed_rule_and_test_case(n: int, line_no: int) -> None:
        rc_id = insert(
            conn, "rule_candidate", member_id=1, line_no=line_no, construct="IF",
            condition=f"COND-{n}", raw=f"IF COND-{n}",
        )
        insert(
            conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc_id,
            scenario_name=f"FAKEMOD:BR-{n:03d}",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
            status="characterization", citation="FAKEMOD:1", confidence="verified",
        )

    for n, line_no in ((1, 10), (2, 20), (3, 30)):
        seed_rule_and_test_case(n, line_no)
    conn.commit()

    # Fingerprint the corpus *before* the insertion -- this is what
    # write_test_doc_with_sidecar would have stamped into the document's
    # front matter at the time this (soon to be stale) sidecar was written.
    fingerprint_before_insertion = testplan.doc_rule_fingerprint(conn, ["FAKEMOD"])

    path = tmp_path / "FAKEMOD.md"
    sidecar = tmp_path / "FAKEMOD.py"
    path.write_text(
        f"""---
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
test_case_fingerprint: "{fingerprint_before_insertion}"
---

# FAKEMOD -- generated tests

See [`FAKEMOD.py`](./FAKEMOD.py) for the generated test source.

## Scenarios covered

- FAKEMOD:BR-001
- FAKEMOD:BR-002
- FAKEMOD:BR-003
""",
        encoding="utf-8",
    )
    sidecar.write_text(
        "def test_one():\n    # FAKEMOD:BR-001\n    ...\n\n"
        "def test_two():\n    # FAKEMOD:BR-002\n    ...\n\n"
        "def test_three():\n    # FAKEMOD:BR-003\n    ...\n",
        encoding="utf-8",
    )

    # Now insert a fourth rule -- every old id is still a literal subset of
    # the now-larger valid set, which the pre-fingerprint id-overlap-only
    # check would have wrongly read as "sidecar still current".
    seed_rule_and_test_case(4, 25)  # between BR-002 and BR-003's line numbers
    conn.commit()

    # Confirm the premise: without the fingerprint, this really would look
    # "usable" to a bare subset check -- otherwise this test wouldn't be
    # proving what it claims to.
    valid_now = {
        r["scenario_name"].upper() for r in conn.execute("SELECT scenario_name FROM test_case")
    }
    old_ids = {"FAKEMOD:BR-001", "FAKEMOD:BR-002", "FAKEMOD:BR-003"}
    assert old_ids <= valid_now, "test setup didn't reproduce the subset scenario"

    result = validate_test_doc(conn, path)
    assert result["sidecar_stale"] is True, (
        "fingerprint mismatch must catch this even though every old id "
        "still resolves"
    )
    assert result["ok"], result["problems"]
    assert result["invalid_scenario_refs"] == 0
    assert not any("not found in" in p or "missing from" in p for p in result["problems"])


def test_stale_sidecar_with_no_body_fallback_is_reported_as_untraceable(indexed_db, tmp_path):
    """Copilot review follow-up on issue #195: when a stale sidecar is
    ignored, the fallback scans the document `body` for `MEMBER:BR-nnn`
    references -- but if the body's own manifest is empty (or missing),
    there's nothing left to check at all. Without a guard, `scan_ids` would
    come back empty, `bad_refs` would stay 0 for lack of anything to flag,
    and this would silently validate `ok=True` even though the document is
    now completely unverifiable -- a stale sidecar tolerated into a
    structural failure, not a clean pass."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    # Same shape as SIDECAR_DOC, but the manifest section is empty -- no
    # MEMBER:BR-nnn reference anywhere in the body to fall back on.
    path.write_text(
        """---
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

(none)
""",
        encoding="utf-8",
    )
    # Old numbering -- BR-999 doesn't exist, so the sidecar is stale.
    sidecar.write_text(
        "def test_rejects_unconfirmed_order():\n    # MMP0100:BR-999\n    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path)
    assert result["sidecar_stale"] is True
    assert not result["ok"]
    assert any("cannot be verified" in p for p in result["problems"])


def test_hand_edited_sidecar_with_a_fingerprint_still_flags_an_invented_id(indexed_db, tmp_path):
    """Copilot review follow-up on issue #195: a hand-edited sidecar with
    one valid id and one invented id (e.g. `{BR-004, BR-999}`) must not
    validate clean. For a document carrying a `test_case_fingerprint` that
    still matches the current corpus (i.e. genuinely not stale), the
    fingerprint check makes the sidecar authoritative *without* going
    through the id-overlap `all(...)` check that would otherwise treat any
    unresolved id as "the whole sidecar is stale" and quietly fall back to
    the body -- so the invented id still reaches the ordinary
    manifest/bad_refs checks and gets reported, the same as it always
    would for a sidecar that was never stale at all."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    fingerprint = testplan.doc_rule_fingerprint(conn, ["MMP0100"])
    path.write_text(
        f"""---
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
test_case_fingerprint: "{fingerprint}"
---

# MMP0100 -- generated tests

See [`MMP0100.py`](./MMP0100.py) for the generated test source.

## Scenarios covered

- MMP0100:BR-004
- MMP0100:BR-999
""",
        encoding="utf-8",
    )
    sidecar.write_text(
        "def test_one():\n    # MMP0100:BR-004\n    ...\n\n"
        "def test_two():\n    # MMP0100:BR-999\n    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path)
    assert result["sidecar_stale"] is False, "a matching fingerprint must not be reported as stale"
    assert not result["ok"]
    assert result["invalid_scenario_refs"] == 1
    assert any("BR-999" in p for p in result["problems"])


def test_malformed_sources_does_not_crash_the_fingerprint_lookup(indexed_db, tmp_path):
    """Copilot review follow-up on issue #195: `sources` containing a
    non-string element (e.g. `[123]`, a genuine front-matter contract
    violation `validate_doc`'s own checks already flag separately) must
    not make `doc_rule_fingerprint`'s `sorted(..., key=str.upper)` raise
    and crash `mfdoc test-validate`. A sidecar present with a stored
    (now-unusable) fingerprint exercises the actual code path the guard
    protects -- the malformed `sources` value must simply leave the
    fingerprint unavailable and fall through to the ID-overlap fallback,
    not propagate an exception."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    path.write_text(
        """---
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
sources: [123]
test_case_fingerprint: "deadbeefcafef00d"
---

# MMP0100 -- generated tests

See [`MMP0100.py`](./MMP0100.py) for the generated test source.

## Scenarios covered

- MMP0100:BR-004
""",
        encoding="utf-8",
    )
    sidecar.write_text("def test_one():\n    # MMP0100:BR-004\n    ...\n", encoding="utf-8")

    result = validate_test_doc(conn, path)  # must not raise
    # validate_doc's own front-matter check correctly flags the malformed
    # `sources` shape itself -- this test's actual concern is that
    # *reaching* that result didn't require crashing inside the
    # fingerprint lookup along the way.
    assert any("sources must be a list of strings" in p for p in result["problems"])
    assert result["sidecar_stale"] is False


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
    assert result["sidecar_unresolved_ids"] == []


def test_validate_tests_tree_scans_test_case_at_most_once_for_the_whole_tree(indexed_db, tmp_path):
    """Copilot review follow-up on issue #195's fix: `validate_tests_tree`
    shares one lazily-memoized `test_case` scenario-name scan across every
    document it validates, instead of each sidecar-bearing/BR-referencing
    document re-running `SELECT scenario_name FROM test_case` on its own
    (otherwise O(document_count * corpus_size) for a tree validation).
    Two documents here each reference a real `MMP0100:BR-nnn` id, which is
    exactly what makes `validate_test_doc`'s own lazy `valid_scenarios()`
    fire at all -- the scan must still only run once between them."""
    real_conn = indexed_db
    testplan.run_all(real_conn, member_name="MMP0100")
    (tmp_path / "a.md").write_text(VALID_DOC, encoding="utf-8")
    (tmp_path / "b.md").write_text(VALID_DOC.replace("BR-004", "BR-001"), encoding="utf-8")

    class _CountingConn:
        """`sqlite3.Connection.execute` is a read-only attribute -- can't be
        monkeypatched directly -- so this wraps the real connection and
        forwards everything else through `__getattr__`."""

        def __init__(self, real):
            self._real = real
            self.calls: list[str] = []

        def execute(self, sql, *args):
            if "test_case" in sql and "scenario_name" in sql:
                self.calls.append(sql)
            return self._real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._real, name)

    conn = _CountingConn(real_conn)
    result = validate_tests_tree(conn, tmp_path)
    assert result["documents"] == 2
    assert len(conn.calls) == 1, "two documents in the same tree must share one test_case scan, not one each"


def test_fingerprint_cache_avoids_recomputing_the_same_members_fingerprint(indexed_db, tmp_path, monkeypatch):
    """Copilot review follow-up on issue #195's fix: `_fingerprint_cache`
    lets several `validate_test_doc` calls for the same document `sources`
    (e.g. every chunk of one member) share one `doc_rule_fingerprint`
    computation instead of each re-querying and re-hashing that member's
    entire `rule_candidate` set from scratch. `_prior_fingerprint` (a
    render/retry-loop stand-in, see that parameter's own docstring) is
    what makes the fingerprint check run at all here -- `SIDECAR_DOC`
    itself carries no stamped `test_case_fingerprint` of its own."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    path.write_text(SIDECAR_DOC, encoding="utf-8")
    sidecar.write_text("def test_x():\n    # MMP0100:BR-004\n    ...\n", encoding="utf-8")

    import mfdoc.validate as validate_module

    calls = []
    real_fingerprint = validate_module.doc_rule_fingerprint

    def counting_fingerprint(conn, member_names):
        calls.append(tuple(member_names))
        return real_fingerprint(conn, member_names)

    monkeypatch.setattr(validate_module, "doc_rule_fingerprint", counting_fingerprint)
    shared: dict = {}
    validate_test_doc(conn, path, _prior_fingerprint="dummy", _fingerprint_cache=shared)
    validate_test_doc(conn, path, _prior_fingerprint="dummy", _fingerprint_cache=shared)
    assert len(calls) == 1, "a shared cache must compute this member's fingerprint at most once"


RENDER_TIME_CANDIDATE_MISSING_A_SCENARIO = """---
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

RENDER_TIME_CANDIDATE_WITH_A_NEW_SCENARIO = """---
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
  verified: 2
  inferred: 0
  unresolved: 0
sources: ["MMP0100"]
---

# MMP0100 -- generated tests

```python
def test_rejects_unconfirmed_order():
    # MMP0100:BR-004 [[MMP0100:38-40]]
    ...

def test_something_new():
    # MMP0100:BR-001 [[MMP0100:1]]
    ...
```
"""


def test_render_time_legacy_sidecar_bypass_still_catches_a_dropped_scenario(indexed_db, tmp_path):
    """Copilot review follow-up on issue #195: `_render_time=True`'s legacy-
    sidecar bypass (no `test_case_fingerprint` anywhere -- a sidecar
    predating that field) treats the old sidecar as absent entirely, to
    avoid the id-overlap heuristic's deadlock (see `validate_test_doc`'s
    own docstring). That bypass must only waive the veto over *newly
    introduced* ids -- not the completeness direction: a freshly-generated
    candidate that silently drops a scenario the old (legacy) sidecar
    covered, while that scenario is still a real, current `test_case` row,
    must still be flagged. `MMP0100:BR-001` is real and current; the old
    sidecar covers it and `BR-004`, but the new candidate's body only
    references `BR-004`."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    path.write_text(RENDER_TIME_CANDIDATE_MISSING_A_SCENARIO, encoding="utf-8")
    # A legacy sidecar (no fingerprint field ever stamped) that covered both
    # scenarios -- the new candidate above only re-renders BR-004.
    sidecar.write_text(
        "def test_rejects_unconfirmed_order():\n"
        "    # MMP0100:BR-004\n"
        "    ...\n"
        "def test_something_else():\n"
        "    # MMP0100:BR-001\n"
        "    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path, _render_time=True)
    assert not result["ok"], "a candidate that silently drops a still-valid scenario must not validate clean"
    assert any(
        "BR-001" in p and "no longer referenced" in p for p in result["problems"]
    ), result["problems"]
    # The bypass itself must still be in effect: BR-004 alone must not be
    # reported as "missing from the sidecar" or similar -- only the
    # completeness direction is preserved, not the original veto.
    assert not any("not found in" in p for p in result["problems"])


def test_render_time_legacy_sidecar_bypass_does_not_flag_a_legitimately_new_scenario(indexed_db, tmp_path):
    """The other half of the same fix: a *new* scenario the candidate
    introduces that the old legacy sidecar never had must still pass --
    this is exactly the deadlock case `_render_time=True`'s bypass exists
    to prevent (see `validate_test_doc`'s docstring), and the completeness
    check added alongside it must not reintroduce that deadlock. `BR-001`
    is real and current but absent from the old sidecar below (which only
    ever covered `BR-004`) -- Copilot review follow-up: an earlier version
    of this test used a candidate/sidecar pair that both only had `BR-004`,
    which exercised no new id at all."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    path.write_text(RENDER_TIME_CANDIDATE_WITH_A_NEW_SCENARIO, encoding="utf-8")
    # Legacy sidecar covers only BR-004 -- the candidate's BR-001 is
    # genuinely new, not previously in the sidecar at all.
    sidecar.write_text(
        "def test_rejects_unconfirmed_order():\n"
        "    # MMP0100:BR-004\n"
        "    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path, _render_time=True)
    assert result["ok"], result["problems"]


def test_render_time_legacy_sidecar_bypass_accepts_a_candidate_with_no_br_refs_at_all(indexed_db, tmp_path):
    """Copilot review follow-up: a fresh, otherwise-valid candidate whose
    code fence has *no* `MEMBER:BR-nnn` references at all -- next to an
    old (legacy, no-fingerprint) sidecar this render-time bypass has
    already decided not to trust -- must still be accepted, not rejected
    as "untraceable". That "cannot be verified at all" guard exists for
    the standalone `mfdoc test-validate` path; during a render/retry
    loop's own validation, this exact shape is precisely what
    `testbatch._write_test_doc_with_sidecar_or_invalidate` exists to
    clean up *after* acceptance -- rejecting it here would mean that
    cleanup path could never run at all, deadlocking a member that
    legitimately drops to zero BR references forever."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    path.write_text(
        """---
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
  verified: 0
  inferred: 0
  unresolved: 0
sources: ["MMP0100"]
---

# MMP0100 -- generated tests

```python
def test_placeholder():
    pass
```
""",
        encoding="utf-8",
    )
    # A legacy sidecar (no fingerprint field ever stamped) whose own id no
    # longer resolves as a current test_case scenario -- so this is the
    # "sidecar had ids, but none of them are still valid" shape, not the
    # "silently dropped a still-current scenario" one the completeness
    # check (a different guard) exists to catch.
    sidecar.write_text(
        "def test_rejects_unconfirmed_order():\n"
        "    # MMP0100:BR-999\n"
        "    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path, _render_time=True)
    assert result["ok"], result["problems"]


def test_a_fingerprint_mismatch_still_catches_a_dropped_scenario(indexed_db, tmp_path):
    """Copilot review follow-up (round 40): a genuine fingerprint
    *mismatch* (the corpus has moved on since this sidecar was stamped)
    used to leave `legacy_bypass_still_valid_ids` empty, unlike the
    no-fingerprint-at-all bypass just below it -- so a fresh candidate
    that silently drops a scenario the stale sidecar still had, while
    that scenario is still a real, current `test_case` row, passed
    validation with nothing to catch it, then got written with the
    *current* fingerprint: permanently accepting the omission instead of
    merely tolerating the staleness itself. `MMP0100:BR-001` is real and
    current; the sidecar covers it and `BR-004`, but the candidate's own
    manifest/body only reference `BR-004`."""
    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    path.write_text(
        """---
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
test_case_fingerprint: "stale-fingerprint-does-not-match-current-corpus"
---

# MMP0100 -- generated tests

```python
def test_rejects_unconfirmed_order():
    # MMP0100:BR-004 [[MMP0100:38-40]]
    ...
```
""",
        encoding="utf-8",
    )
    # The sidecar this stale fingerprint was stamped against still covers
    # both BR-001 and BR-004 -- the candidate above silently drops BR-001.
    sidecar.write_text(
        "def test_rejects_unconfirmed_order():\n"
        "    # MMP0100:BR-004\n"
        "    ...\n"
        "def test_something_else():\n"
        "    # MMP0100:BR-001\n"
        "    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path)
    assert not result["ok"], "a fingerprint mismatch must not waive the completeness check"
    assert any(
        "BR-001" in p and "no longer referenced" in p for p in result["problems"]
    ), result["problems"]


def test_prior_fingerprint_is_trusted_over_the_candidates_own_stamped_field(indexed_db, tmp_path):
    """Copilot review follow-up (round 40): `_prior_fingerprint` (the
    previous successful render's, captured by the caller before
    overwriting `path` with a fresh candidate) must be preferred *over*
    whatever the candidate's own front matter happens to carry, not just
    used as a fallback -- a freshly-generated candidate is never supposed
    to stamp this field at all (only `write_test_doc_with_sidecar` does,
    after validation succeeds), so a value that shows up anyway is the
    model echoing/hallucinating it, not a value this validation should
    trust over the caller's own known-genuine prior. Here the candidate's
    own stamped field is deliberately wrong (would read as a mismatch if
    trusted), while `_prior_fingerprint` is the real current fingerprint --
    the sidecar must still be treated as usable (matching), so the full
    two-directional manifest/sidecar check runs instead of the
    fingerprint-mismatch completeness fallback, and catches the
    candidate's manifest claiming a scenario (`BR-001`) the sidecar's
    actual code doesn't have."""
    import mfdoc.validate as validate_module

    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    real_fp = validate_module.doc_rule_fingerprint(conn, ["MMP0100"])
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    path.write_text(
        """---
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
test_case_fingerprint: "hallucinated-value-that-does-not-match-anything"
---

# MMP0100 -- generated tests

See [`MMP0100.py`](./MMP0100.py) for the generated test source.

## Scenarios covered

- MMP0100:BR-004
- MMP0100:BR-001
""",
        encoding="utf-8",
    )
    # The sidecar's actual code only has BR-004 -- the manifest's BR-001
    # claim above is unbacked by anything in the sidecar.
    sidecar.write_text(
        "def test_rejects_unconfirmed_order():\n"
        "    # MMP0100:BR-004\n"
        "    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path, _prior_fingerprint=real_fp)
    assert not result["ok"], (
        "the trusted _prior_fingerprint must win, making the sidecar 'usable' so the "
        "full manifest/sidecar cross-check runs and catches the unbacked manifest claim"
    )


def test_render_time_validation_never_trusts_the_candidates_own_field_even_with_no_prior(indexed_db, tmp_path):
    """Copilot review follow-up (round 42): when `_render_time=True` and
    the caller has no `_prior_fingerprint` to recover (a legacy sidecar
    with nothing to capture before the first overwrite, or an orphaned
    sidecar with no recoverable prior document at all -- exactly what
    `_test_chunk_reuse_ok`'s own docstring on the legacy-sidecar bypass
    describes), this must not fall back to reading the *candidate's own*
    stamped `test_case_fingerprint` field either. A model can echo/
    hallucinate a value that happens to coincidentally match the current
    corpus fingerprint (it need not be nonsense -- copying one verbatim
    from an example in its own prompt is exactly the failure mode), which
    would make a stale legacy sidecar look authoritative again and
    reproduce the exact manifest/sidecar retry deadlock the render-time
    bypass exists to prevent: `MMP0100:BR-001` is a genuinely *new*
    scenario the old (legacy, no-fingerprint) sidecar never had -- the
    bypass must still let it through, not deadlock on it."""
    import mfdoc.validate as validate_module

    conn = indexed_db
    testplan.run_all(conn, member_name="MMP0100")
    real_fp = validate_module.doc_rule_fingerprint(conn, ["MMP0100"])
    path = tmp_path / "MMP0100.md"
    sidecar = tmp_path / "MMP0100.py"
    path.write_text(
        f"""---
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
  verified: 2
  inferred: 0
  unresolved: 0
sources: ["MMP0100"]
test_case_fingerprint: "{real_fp}"
---

# MMP0100 -- generated tests

```python
def test_rejects_unconfirmed_order():
    # MMP0100:BR-004 [[MMP0100:38-40]]
    ...

def test_something_new():
    # MMP0100:BR-001 [[MMP0100:1]]
    ...
```
""",
        encoding="utf-8",
    )
    # A legacy sidecar (no fingerprint field ever stamped) that only ever
    # covered BR-004 -- BR-001 above is genuinely new to it.
    sidecar.write_text(
        "def test_rejects_unconfirmed_order():\n"
        "    # MMP0100:BR-004\n"
        "    ...\n",
        encoding="utf-8",
    )
    result = validate_test_doc(conn, path, _render_time=True)
    assert result["ok"], (
        "the candidate's own stamped field must never be trusted at render time, even "
        "with no _prior_fingerprint -- otherwise a coincidentally-matching value "
        "deadlocks the legacy-sidecar bypass on a genuinely new scenario"
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
