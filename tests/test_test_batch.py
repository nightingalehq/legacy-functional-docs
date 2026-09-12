"""End-to-end guard on `mfdoc test-gen`/`mfdoc test-batch`'s own config
wiring (cli.py), mirroring tests/test_cli_batch.py's approach: --caller
fake-echo returns the prompt itself as the response text, so the written
.md *is* the prompt the command actually built -- letting us assert on it
directly without a real model call.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from mfdoc import cli, testplan
from mfdoc.batch import ModelResponse

REPO_ROOT = Path(__file__).resolve().parent.parent


def _with_reference_and_templates(cli_args, tmp_path):
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")
    return project_dir


def test_test_gen_prompt_carries_the_derived_scenarios(cli_args, indexed_db, tmp_path):
    _with_reference_and_templates(cli_args, tmp_path)
    testplan.run_all(indexed_db, member_name="MMP0100")

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out" / "MMP0100.md"),
        member="MMP0100", language="python", framework="pytest", template=None,
        model=None, caller="fake-echo", provider="anthropic",
        gcp_project=None, gcp_region=None, matrix=False,
    )
    cli.cmd_test_gen(args)
    written = (tmp_path / "out" / "MMP0100.md").read_text(encoding="utf-8")
    assert "# Test brief: MMP0100" in written
    assert "MMP0100:BR-" in written
    assert "python/pytest" in written


def test_test_batch_selects_only_members_with_test_case_rows(cli_args, indexed_db, tmp_path):
    _with_reference_and_templates(cli_args, tmp_path)
    testplan.run_all(indexed_db, member_name="MMP0100")

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members=None,
        language="python", framework="pytest", template=None, model=None,
        caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
        concurrency=1, state="", matrix=False,
    )
    cli.cmd_test_batch(args)
    written = (tmp_path / "out" / "natural" / "MILLPROD" / "python" / "pytest" / "MMP0100.md").read_text(encoding="utf-8")
    assert "# Test brief: MMP0100" in written


def test_missing_template_exits_cleanly_rather_than_crashing(cli_args, tmp_path):
    project_dir = _with_reference_and_templates(cli_args, tmp_path)
    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out" / "X.md"),
        member="MMP0100", language="cobol", framework="nonexistent", template=None,
        model=None, caller="fake-echo", provider="anthropic",
        gcp_project=None, gcp_region=None, matrix=False,
    )
    rc = cli.cmd_test_gen(args)
    assert rc == 2


def _valid_test_doc_text(language: str, framework: str) -> str:
    return f"""---
title: "FAKEMOD — generated tests"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-08-11"
review_status: draft
confidence_summary:
  verified: 1
language: {language}
framework: {framework}
sources: ["FAKEMOD"]
---

# FAKEMOD tests

Covers the module as a whole [[FAKEMOD:1]].

```python
def test_x():
    # FAKEMOD:BR-001 [[FAKEMOD:1]]
    pass
```
"""


def test_corpus_signature_changes_when_scenario_content_changes_but_name_and_status_do_not(tmp_path):
    """Copilot review follow-up on issue #195: `_corpus_signature` used to
    hash only each test_case's (scenario_name, status). A `classify-rules`/
    `derive` rebuild that reassigns the same `scenario_name` strings (same
    count, same positional BR-numbering) to *different* underlying
    `rule_candidate` rows -- different citation/condition/source excerpt
    behind each id -- would leave every (scenario_name, status) pair
    unchanged, so `run_test_batch`/`plan_test_batch`'s `corpus_unchanged`
    fast path would wrongly skip every member, bypassing
    `_test_chunk_reuse_ok`'s own per-chunk sidecar-staleness check
    entirely. Hashing `citation` and the given/when/then JSON blobs too
    means any such change moves the signature even when `scenario_name`/
    `status` alone would not."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    def seed(citation: str) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
        conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
        insert(
            conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json=f'{{"citation": "[[{citation}]]", "source_excerpt": []}}',
            status="characterization", citation=citation, confidence="verified",
        )
        conn.commit()
        return conn

    conn_before = seed("FAKEMOD:1")
    conn_after = seed("FAKEMOD:2")  # same scenario_name/status, different citation
    sig_before = testbatch._corpus_signature(conn_before, "python", "pytest", 999)
    sig_after = testbatch._corpus_signature(conn_after, "python", "pytest", 999)
    assert sig_before != sig_after


def test_corpus_signature_changes_when_member_system_changes(tmp_path):
    """Copilot review follow-up on issue #195: `test_case_brief()` includes
    the member's `system` in its rendered header, but `_corpus_signature`
    only hashed `test_case` columns -- relabelling a source's configured
    `system` and re-ingesting (no `test_case` row itself changes) would
    leave the signature unchanged and wrongly skip re-rendering a document
    whose header text has actually changed."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    def seed(system: str | None) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO member (id, name, dialect, system) VALUES (1, 'FAKEMOD', 'natural', ?)",
            (system,),
        )
        conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
        insert(
            conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
            status="characterization", citation="FAKEMOD:1", confidence="verified",
        )
        conn.commit()
        return conn

    conn_before = seed("OLDSYS")
    conn_after = seed("NEWSYS")
    sig_before = testbatch._corpus_signature(conn_before, "python", "pytest", 999)
    sig_after = testbatch._corpus_signature(conn_after, "python", "pytest", 999)
    assert sig_before != sig_after


def test_corpus_signature_is_deterministic_across_scenario_name_collisions(tmp_path):
    """Copilot review follow-up on issue #195's fix: `test_case.
    scenario_name` isn't unique on its own -- a bare member name can
    collide across libraries (`member` is unique on `(name, library,
    dialect)`, not name alone), and `scenario_name` is built from that
    bare name. `_corpus_signature`'s query must break ties on
    `tc.member_id, tc.id`, or two equal `scenario_name` values leave their
    relative order to SQLite's unspecified tie behaviour -- changing this
    digest, and forcing an unnecessary full rerender on the next resume,
    even when nothing in the corpus actually moved. Exercised here by
    comparing two connections whose two colliding test_case rows are
    inserted in opposite order -- a real corpus is never guaranteed to
    insert them in any particular order relative to each other either."""
    import sqlite3

    from mfdoc.db import SCHEMA, insert
    from mfdoc.testbatch import _corpus_signature

    def seed(insert_lib1_first: bool) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO member (id, name, library, dialect) VALUES (1, 'FAKEMOD', 'LIB1', 'natural')")
        conn.execute("INSERT INTO member (id, name, library, dialect) VALUES (2, 'FAKEMOD', 'LIB2', 'natural')")
        conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'x')")
        conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (2, 1, 'x')")

        def tc(member_id):
            insert(
                conn, "test_case", member_id=member_id, kind="unit", scenario_name="FAKEMOD:BR-001",
                given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
                when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
                then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
                status="characterization", citation="FAKEMOD:1", confidence="verified",
            )

        if insert_lib1_first:
            tc(1)
            tc(2)
        else:
            tc(2)
            tc(1)
        conn.commit()
        return conn

    sig_a = _corpus_signature(seed(True), "python", "pytest", 50)
    sig_b = _corpus_signature(seed(False), "python", "pytest", 50)
    assert sig_a == sig_b, "insertion order alone must not change the corpus signature"


def test_corpus_signature_changes_when_rule_candidate_ordering_shifts_but_test_case_does_not(tmp_path):
    """Copilot review follow-up on issue #195: a `derive` rebuild can
    insert/reorder `rule_candidate` rows (and shift `routine` boundaries)
    before `mfdoc test-plan` has re-run to reflect that in `test_case` --
    leaving every `test_case` column `_corpus_signature` already hashes
    untouched. Without also hashing `rule_candidate`'s own `(id, line_no)`
    ordering and `routine` boundaries directly, the corpus-level fast path
    in `run_test_batch`/`plan_test_batch` would wrongly treat this as
    "nothing changed" and skip straight past `_test_chunk_reuse_ok`'s own
    per-chunk sidecar-staleness check."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    def seed(extra_rule: bool) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
        conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")
        rc_id = insert(
            conn, "rule_candidate", member_id=1, line_no=10, construct="IF",
            condition="COND-1", raw="IF COND-1",
        )
        insert(
            conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc_id,
            scenario_name="FAKEMOD:BR-001",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
            status="characterization", citation="FAKEMOD:1", confidence="verified",
        )
        if extra_rule:
            # A rule_candidate the derive rebuild produced, with no
            # matching test_case row yet -- test-plan hasn't re-run.
            insert(
                conn, "rule_candidate", member_id=1, line_no=20, construct="IF",
                condition="COND-2", raw="IF COND-2",
            )
        conn.commit()
        return conn

    sig_before = testbatch._corpus_signature(seed(False), "python", "pytest", 999)
    sig_after = testbatch._corpus_signature(seed(True), "python", "pytest", 999)
    assert sig_before != sig_after


def test_corpus_signature_changes_when_a_rule_candidate_is_reclassified_in_place(tmp_path):
    """Copilot review follow-up: `member_rule_fingerprint` hashes
    `construct` because a row reclassified in place (e.g. between a
    branch construct and `DECIDE ON`, which `_is_branch_row` excludes)
    changes which rows become scenarios even though `(id, line_no)` alone
    doesn't move -- but that reclassification also leaves every
    `test_case` row this signature already hashes untouched (test-plan
    hasn't re-run yet). Without `construct` in this corpus-level query
    too, `corpus_unchanged` would short-circuit both `run_test_batch` and
    `plan_test_batch` before ever reaching the per-member fingerprint
    check that would otherwise have caught it."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    def seed(construct: str) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
        conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")
        rc_id = insert(
            conn, "rule_candidate", member_id=1, line_no=10, construct=construct,
            condition="COND-1", raw="IF COND-1",
        )
        insert(
            conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc_id,
            scenario_name="FAKEMOD:BR-001",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
            status="characterization", citation="FAKEMOD:1", confidence="verified",
        )
        conn.commit()
        return conn

    sig_before = testbatch._corpus_signature(seed("IF"), "python", "pytest", 999)
    sig_after = testbatch._corpus_signature(seed("DECIDE ON"), "python", "pytest", 999)
    assert sig_before != sig_after, (
        "a construct-only reclassification (same id/line_no) must still change the corpus signature"
    )


def test_corpus_signature_changes_when_a_routine_boundary_shifts(tmp_path):
    """Copilot review follow-up on issue #195: the rule_candidate-ordering
    regression above only varies `rule_candidate` rows, never `routine`
    boundaries -- a future edit could drop the `routine` terms from
    `_corpus_signature` without any existing test noticing. This holds
    every `test_case`/`rule_candidate` input constant and changes only one
    `routine` row's `end_line` (the same boundary
    `brief.routine_aware_chunk_ranges`/`fetch_routines` use to decide
    chunk grouping), confirming the signature moves from that alone."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    def seed(end_line: int) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
        conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")
        rc_id = insert(
            conn, "rule_candidate", member_id=1, line_no=10, construct="IF",
            condition="COND-1", raw="IF COND-1",
        )
        insert(
            conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc_id,
            scenario_name="FAKEMOD:BR-001",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
            status="characterization", citation="FAKEMOD:1", confidence="verified",
        )
        insert(
            conn, "routine", member_id=1, name="SUB-A", kind="natural_subroutine",
            start_line=5, end_line=end_line,
        )
        conn.commit()
        return conn

    sig_before = testbatch._corpus_signature(seed(15), "python", "pytest", 999)
    sig_after = testbatch._corpus_signature(seed(25), "python", "pytest", 999)
    assert sig_before != sig_after


def test_corpus_signature_changes_when_a_test_case_relinks_to_a_different_rule_candidate(tmp_path):
    """Copilot review follow-up on issue #195: `_corpus_signature` hashed
    each `test_case`'s own derived content, but not which `rule_candidate`
    row it's actually linked to via `rule_candidate_id` -- the same link
    `fetch_test_case_rows`/`test_case_brief_chunk`'s routine-aware chunk
    layout reads. Holds every other input constant and only changes which
    of two existing `rule_candidate` rows the one `test_case` row points
    at."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    def seed(linked_rc_index: int) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
        conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")
        rc_ids = [
            insert(
                conn, "rule_candidate", member_id=1, line_no=line_no, construct="IF",
                condition=f"COND-{n}", raw=f"IF COND-{n}",
            )
            for n, line_no in enumerate((10, 20), start=1)
        ]
        insert(
            conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc_ids[linked_rc_index],
            scenario_name="FAKEMOD:BR-001",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
            status="characterization", citation="FAKEMOD:1", confidence="verified",
        )
        conn.commit()
        return conn

    sig_before = testbatch._corpus_signature(seed(0), "python", "pytest", 999)
    sig_after = testbatch._corpus_signature(seed(1), "python", "pytest", 999)
    assert sig_before != sig_after


def test_run_test_batch_does_not_reuse_state_or_file_across_frameworks(tmp_path):
    """Running the same member/language for two different frameworks must
    produce two separate output files and two separate resume-state
    entries -- a switch from --framework pytest to --framework unittest
    (same --language/--out/--state) must never skip regenerating or read
    back the other framework's stale content."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')"
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    def caller_for(framework):
        def _call(prompt):
            return ModelResponse(text=_valid_test_doc_text("python", framework), input_tokens=0, output_tokens=0)
        return _call

    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"

    pytest_summary = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller_for("pytest"),
        "writing rules text", "template text", state_path=state_path,
    )
    unittest_summary = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "unittest", out_dir, caller_for("unittest"),
        "writing rules text", "template text", state_path=state_path,
    )

    assert pytest_summary.skipped == 0
    assert unittest_summary.skipped == 0, "must not reuse the pytest run's state for a different framework"

    pytest_path = out_dir / "natural" / "python" / "pytest" / "FAKEMOD.md"
    unittest_path = out_dir / "natural" / "python" / "unittest" / "FAKEMOD.md"
    pytest_text = pytest_path.read_text(encoding="utf-8")
    unittest_text = unittest_path.read_text(encoding="utf-8")
    assert "framework: pytest" in pytest_text
    assert "framework: unittest" in unittest_text

    # Each framework's .md is slimmed (fence extracted) with its own sidecar
    # -- not sharing/clobbering the other framework's .py file.
    pytest_sidecar = out_dir / "natural" / "python" / "pytest" / "FAKEMOD.py"
    unittest_sidecar = out_dir / "natural" / "python" / "unittest" / "FAKEMOD.py"
    assert "def test_x" not in pytest_text
    assert "def test_x" not in unittest_text
    assert pytest_sidecar.read_text(encoding="utf-8") == unittest_sidecar.read_text(encoding="utf-8")
    assert "def test_x" in pytest_sidecar.read_text(encoding="utf-8")
    assert "FAKEMOD:BR-001" in pytest_text  # manifest, not the embedded fence


def test_generate_member_test_doc_retries_and_reports_failure_for_fake_echo():
    """fake-echo's response (the prompt itself) is never a valid document --
    generate_member_test_doc must retry once, then report ok=False rather
    than silently accepting an invalid file, exactly like batch.py's
    generate_module_doc does for module docs."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    def fake_echo(prompt):
        return ModelResponse(text=prompt, input_tokens=0, output_tokens=0)

    import tempfile
    out_path = Path(tempfile.mkdtemp()) / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, fake_echo,
        "writing rules text", "template text",
    )
    assert result.ok is False
    assert result.attempts == 2
    assert result.problems
    assert not out_path.with_suffix(".py").exists(), "a failed validation must never produce a sidecar"


def _near_miss_test_doc_conn():
    """An in-memory fact store with one member (one citable source line) and
    one test_case scenario -- enough for validate_test_doc to resolve a
    `[[FAKEMOD:1]]` citation and a `FAKEMOD:BR-001` scenario reference, the
    minimum needed to exercise the near-miss/targeted-patch path below."""
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')"
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()
    return conn


def _near_miss_test_doc_text(prose: str) -> str:
    return (
        _valid_test_doc_text("python", "pytest")
        .replace("Covers the module as a whole [[FAKEMOD:1]].", prose)
    )


def test_near_miss_uncited_assertion_in_test_doc_gets_a_targeted_patch_not_a_full_retry():
    """Issue #188 (porting #131/#170 to testbatch.py): a test document whose
    only validation problem is a single near-miss uncited assertive
    statement gets a cheap targeted-patch follow-up call
    (build_localized_test_patch_prompt) instead of a full retry -- the
    second call must carry the flagged-sentence prompt, not the writing
    rules/template/"Previous attempt failed" full-retry prompt, and must
    not consume one of the full-regeneration attempts."""
    from mfdoc import testbatch

    conn = _near_miss_test_doc_conn()
    calls = {"n": 0}
    prompts: list[str] = []

    near_miss_text = _near_miss_test_doc_text(
        "Covers the module as a whole [[FAKEMOD:1]]. The system also "
        "validates the account balance before posting."
    )
    patched_text = _near_miss_test_doc_text(
        "Covers the module as a whole [[FAKEMOD:1]]. The system also "
        "validates the account balance before posting [[FAKEMOD:1]]."
    )

    def caller(prompt: str) -> ModelResponse:
        calls["n"] += 1
        prompts.append(prompt)
        if calls["n"] == 1:
            return ModelResponse(text=near_miss_text, input_tokens=10, output_tokens=20)
        return ModelResponse(text=patched_text, input_tokens=5, output_tokens=8)

    import tempfile
    out_path = Path(tempfile.mkdtemp()) / "FAKEMOD.md"
    brief = "# Test brief: FAKEMOD\n\nSome brief text [[FAKEMOD:1]].\n"
    result = testbatch._generate_test_doc_from_brief(
        conn, "FAKEMOD", brief, "python", "pytest", out_path, caller,
        "writing rules text", "template text",
    )
    assert result.ok, result.problems
    assert calls["n"] == 2
    assert result.attempts == 1  # the patch call doesn't count as a full-retry attempt
    patch_prompt = prompts[1]
    assert "Flagged findings (locate the matching sentence; fix only these)" in patch_prompt
    assert "Uncited assertive statements" in patch_prompt
    assert "truncated to 140 characters" in patch_prompt
    assert "Previous attempt failed validation" not in patch_prompt
    assert "writing rules text" not in patch_prompt  # writing rules not resent
    assert "template text" not in patch_prompt  # template not resent
    assert out_path.with_suffix(".py").exists(), "a successful patch must still write the sidecar"


def test_near_miss_patch_failure_in_test_doc_falls_back_to_full_retry():
    """When the targeted patch attempt itself doesn't resolve validation,
    generation must still fall back to the existing full-retry path (with
    its normal max_attempts budget) rather than giving up."""
    from mfdoc import testbatch

    conn = _near_miss_test_doc_conn()
    calls = {"n": 0}
    prompts: list[str] = []

    near_miss_text = _near_miss_test_doc_text(
        "Covers the module as a whole [[FAKEMOD:1]]. The system also "
        "validates the account balance before posting."
    )
    fixed_text = _near_miss_test_doc_text(
        "Covers the module as a whole [[FAKEMOD:1]]. The system also "
        "validates the account balance before posting [[FAKEMOD:1]]."
    )

    def caller(prompt: str) -> ModelResponse:
        calls["n"] += 1
        prompts.append(prompt)
        if calls["n"] == 1:
            return ModelResponse(text=near_miss_text, input_tokens=10, output_tokens=20)
        if calls["n"] == 2:
            # Patch attempt: model fails to actually fix it.
            return ModelResponse(text=near_miss_text, input_tokens=5, output_tokens=8)
        # Full retry: succeeds.
        return ModelResponse(text=fixed_text, input_tokens=1, output_tokens=1)

    import tempfile
    out_path = Path(tempfile.mkdtemp()) / "FAKEMOD.md"
    brief = "# Test brief: FAKEMOD\n\nSome brief text [[FAKEMOD:1]].\n"
    result = testbatch._generate_test_doc_from_brief(
        conn, "FAKEMOD", brief, "python", "pytest", out_path, caller,
        "writing rules text", "template text", max_attempts=2,
    )
    assert result.ok, result.problems
    assert calls["n"] == 3
    assert result.attempts == 2
    assert "Previous attempt failed validation" in prompts[2]


def test_extract_code_fence_rejects_zero_or_multiple_fences():
    from mfdoc.testbatch import extract_code_fence

    assert extract_code_fence("no fence here", "python") is None
    assert extract_code_fence("```python\ncode\n```", "python") == "code\n"
    two_fences = "```python\na\n```\n\n```python\nb\n```"
    assert extract_code_fence(two_fences, "python") is None
    # A fence tagged for a different language doesn't count as a match.
    assert extract_code_fence("```java\ncode\n```", "python") is None


def test_natural_and_mantis_have_sidecar_extensions():
    from mfdoc.testlang import LANGUAGE_EXTENSIONS, sidecar_path_for

    assert LANGUAGE_EXTENSIONS["natural"] == "nsp"
    assert LANGUAGE_EXTENSIONS["mantis"] == "mantis"
    assert sidecar_path_for(Path("FAKEMOD.md"), "natural") == Path("FAKEMOD.nsp")
    assert sidecar_path_for(Path("FAKEMOD.md"), "mantis") == Path("FAKEMOD.mantis")


def test_silkcentral_and_uipath_have_no_sidecar_extension():
    """Test-case-definition targets stay embedded in the .md -- no
    invented extension for an import format that varies per deployment."""
    from mfdoc.testlang import sidecar_path_for

    assert sidecar_path_for(Path("FAKEMOD.md"), "silkcentral") is None
    assert sidecar_path_for(Path("FAKEMOD.md"), "uipath") is None


def test_unknown_language_keeps_code_embedded(tmp_path):
    """A language with no entry in testlang.LANGUAGE_EXTENSIONS must never
    get a guessed extension -- the doc stays exactly as generated."""
    from mfdoc.testbatch import write_test_doc_with_sidecar

    doc_text = _valid_test_doc_text("cobol", "cobol-unit")
    out_path = tmp_path / "FAKEMOD.md"
    out_path.write_text(doc_text, encoding="utf-8")
    sidecar = write_test_doc_with_sidecar(None, "FAKEMOD", out_path, doc_text, "cobol")
    assert sidecar is None
    assert out_path.read_text(encoding="utf-8") == doc_text
    assert not (tmp_path / "FAKEMOD.cobol").exists()


def test_sidecar_path_for_rejects_non_string_language_instead_of_crashing():
    """Copilot review follow-up on issue #195's fix: a malformed/hand-edited
    document's `language` front-matter value can be any YAML scalar shape
    (a list, a number, a mapping), not just a string. `LANGUAGE_EXTENSIONS.
    get(language)` on an unhashable value (e.g. `language: [python]`) used
    to raise `TypeError` instead of this function's own documented "unknown
    language" `None`, crashing `mfdoc test-validate` on exactly that
    malformed shape."""
    from pathlib import Path

    from mfdoc.testlang import sidecar_path_for

    doc_path = Path("FAKEMOD.md")
    assert sidecar_path_for(doc_path, ["python"]) is None  # must not raise
    assert sidecar_path_for(doc_path, {"python": True}) is None
    assert sidecar_path_for(doc_path, 42) is None
    assert sidecar_path_for(doc_path, "python") == Path("FAKEMOD.py")


def test_write_test_doc_with_sidecar_strips_whitespace_in_sources_before_fingerprinting(tmp_path):
    """Copilot review follow-up on issue #195: `validate_test_doc`'s
    fingerprint recomputation strips whitespace from `sources` member
    names before resolving them (a prior review round), but the write
    side (`write_test_doc_with_sidecar`) must normalize identically -- a
    `sources` entry with incidental whitespace (e.g. `["FAKEMOD "]`)
    would otherwise fail to resolve here, silently skip stamping a
    fingerprint at all, and every later validation would fall back to
    the weaker id-overlap heuristic even though nothing about the
    document itself is actually malformed."""
    from mfdoc import testbatch
    from mfdoc.testplan import doc_rule_fingerprint
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    doc_text = _valid_test_doc_text("python", "pytest").replace(
        'sources: ["FAKEMOD"]', 'sources: ["FAKEMOD "]',
    )
    out_path = tmp_path / "FAKEMOD.md"
    out_path.write_text(doc_text, encoding="utf-8")
    testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")

    written = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint:" in written, (
        "a sources entry with incidental whitespace must not silently "
        "skip fingerprint stamping"
    )
    expected_fp = doc_rule_fingerprint(conn, ["FAKEMOD"])
    assert f'test_case_fingerprint: "{expected_fp}"' in written


def test_write_test_doc_with_sidecar_omits_fingerprint_when_test_case_is_stale(tmp_path):
    """Copilot review follow-up on issue #195's fix: a `rule_candidate` row
    inserted (e.g. by a `derive` rebuild) with no corresponding `test_case`
    row yet (`mfdoc test-plan` hasn't re-run) means this document's own
    content -- rendered from whatever `test_case_brief` fed the model --
    already predates the current `rule_candidate` state. Stamping the
    *current* rule_candidate fingerprint onto it anyway would bake in a
    fingerprint that keeps matching every later recomputation (rule_
    candidate doesn't move again until the next derive run), making this
    now-stale sidecar look current even after test-plan catches up and a
    fresh render's manifest legitimately carries the new scenario --
    reproducing the exact "missing from sidecar" false positive issue #195
    exists to close, at the write side instead of the read side."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    rc1 = insert(conn, "rule_candidate", member_id=1, line_no=1, construct="IF", condition="COND-1", raw="IF COND-1")
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1, scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    # A second rule_candidate row appears (a derive rebuild), but test-plan
    # hasn't re-run to give it a test_case row yet -- test_case is now
    # stale relative to rule_candidate.
    insert(conn, "rule_candidate", member_id=1, line_no=2, construct="IF", condition="COND-2", raw="IF COND-2")
    conn.commit()

    doc_text = _valid_test_doc_text("python", "pytest")
    out_path = tmp_path / "FAKEMOD.md"
    out_path.write_text(doc_text, encoding="utf-8")
    testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")

    written = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint:" not in written


def test_write_test_doc_with_sidecar_strips_an_untrusted_preexisting_fingerprint(tmp_path):
    """Copilot review follow-up (round 41): when this function cannot
    compute a trusted fingerprint (here, `test_case` is stale relative to
    `rule_candidate` -- the same shape as the test above), it used to
    leave any `test_case_fingerprint` already present in the *candidate's
    own* front matter untouched. A model can echo/hallucinate this field
    from the brief or a prior template even though it's only ever
    supposed to be stamped here, after a successful validation -- left in
    place, that untrusted value could later coincidentally match once
    `test-plan` catches up, at which point `validate_test_doc`'s
    standalone fallback (no `_prior_fingerprint`) would read it as
    genuine and treat a sidecar it was never actually validated against
    as authoritative. Must be stripped regardless of whether a trusted
    replacement is computed."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    rc1 = insert(conn, "rule_candidate", member_id=1, line_no=1, construct="IF", condition="COND-1", raw="IF COND-1")
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1, scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    # test_case stale relative to rule_candidate -- no trusted fingerprint
    # can be computed for this write (same shape as the test above).
    insert(conn, "rule_candidate", member_id=1, line_no=2, construct="IF", condition="COND-2", raw="IF COND-2")
    conn.commit()

    doc_text = _valid_test_doc_text("python", "pytest").replace(
        'sources: ["FAKEMOD"]\n',
        'sources: ["FAKEMOD"]\ntest_case_fingerprint: "hallucinated-untrusted-value"\n',
    )
    out_path = tmp_path / "FAKEMOD.md"
    out_path.write_text(doc_text, encoding="utf-8")
    testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")

    written = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint:" not in written, (
        "an untrusted pre-existing fingerprint must be stripped, not left in place, "
        "when no trusted replacement can be computed"
    )


def test_write_test_doc_with_sidecar_strips_a_multiline_preexisting_fingerprint(tmp_path):
    """Copilot review follow-up (round 42): the round-41 fix above only
    stripped the `test_case_fingerprint` key's own line -- a YAML block
    scalar (`test_case_fingerprint: |`) or block sequence carries its
    actual content on the *following*, indented lines instead, which that
    single-line strip left behind. Left in place, those orphaned
    continuation lines produce malformed YAML (an indented block with no
    key of its own) once the front matter is rewritten -- even though the
    candidate validated cleanly before this rewrite touched it. The very
    next key (`generated_by`) must survive untouched, immediately after
    the multiline field is fully removed."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    rc1 = insert(conn, "rule_candidate", member_id=1, line_no=1, construct="IF", condition="COND-1", raw="IF COND-1")
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1, scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    # test_case stale relative to rule_candidate -- no trusted fingerprint
    # can be computed for this write (same shape as the tests above).
    insert(conn, "rule_candidate", member_id=1, line_no=2, construct="IF", condition="COND-2", raw="IF COND-2")
    conn.commit()

    doc_text = _valid_test_doc_text("python", "pytest").replace(
        'sources: ["FAKEMOD"]\n',
        'sources: ["FAKEMOD"]\ntest_case_fingerprint: |\n  untrusted\n  continuation-line\n',
    )
    out_path = tmp_path / "FAKEMOD.md"
    out_path.write_text(doc_text, encoding="utf-8")
    testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")

    written = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint" not in written, (
        "the multiline field's key must be stripped, not left in place"
    )
    assert "untrusted" not in written and "continuation-line" not in written, (
        "the block scalar's own continuation lines must be stripped too, not just its key line"
    )
    assert "generated_by: mfdoc" in written, "an unrelated later key must survive the strip untouched"


def test_write_test_doc_with_sidecar_strips_a_quoted_key_preexisting_fingerprint(tmp_path):
    """Copilot review follow-up (round 52): `_TEST_CASE_FINGERPRINT_FIELD`
    only matched a bare `test_case_fingerprint:` key -- YAML permits a
    quoted mapping key (`"test_case_fingerprint": ...`) with exactly the
    same meaning (`yaml.safe_load` parses both into the identical dict
    key), and a model echoing/hallucinating this field can just as easily
    quote the key as not. Left unstripped, a quoted-key copy would survive
    this rewrite untouched and be recoverable by a later
    `_prior_fingerprint_for` call as a false trusted prior -- the same
    leak the round-41 fix closed for the bare-key form."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    rc1 = insert(conn, "rule_candidate", member_id=1, line_no=1, construct="IF", condition="COND-1", raw="IF COND-1")
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1, scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    # test_case stale relative to rule_candidate -- no trusted fingerprint
    # can be computed for this write (same shape as the tests above).
    insert(conn, "rule_candidate", member_id=1, line_no=2, construct="IF", condition="COND-2", raw="IF COND-2")
    conn.commit()

    doc_text = _valid_test_doc_text("python", "pytest").replace(
        'sources: ["FAKEMOD"]\n',
        'sources: ["FAKEMOD"]\n"test_case_fingerprint": "hallucinated-untrusted-value"\n',
    )
    out_path = tmp_path / "FAKEMOD.md"
    out_path.write_text(doc_text, encoding="utf-8")
    testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")

    written = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint" not in written, (
        "a quoted-key pre-existing fingerprint must be stripped just like a bare-key one"
    )


def test_write_test_doc_with_sidecar_omits_fingerprint_for_empty_sources(tmp_path):
    """Copilot review follow-up on issue #195: `sources: []` is a
    syntactically valid list (so `validate_doc`'s own malformed-shape
    check doesn't flag it), but an earlier version of this fix
    substituted `[member_name]` for it and stamped a fingerprint anyway.
    `validate_test_doc`'s own recomputation always reads the document's
    *own* `sources` verbatim (never substituting `member_name`), so that
    stamped value could never be reproduced there -- permanently pushing
    such a document onto the weaker id-overlap fallback despite carrying
    what looked like a valid fingerprint. The write side must omit the
    field entirely for this shape instead, consistent with the read
    side's own inability to recompute one."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    doc_text = _valid_test_doc_text("python", "pytest").replace(
        'sources: ["FAKEMOD"]', "sources: []",
    )
    out_path = tmp_path / "FAKEMOD.md"
    out_path.write_text(doc_text, encoding="utf-8")
    testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")

    written = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint:" not in written


def test_generate_member_test_doc_writes_sidecar_and_slims_md(tmp_path):
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    doc_text = _valid_test_doc_text("python", "pytest")

    def caller(prompt):
        return ModelResponse(text=doc_text, input_tokens=1, output_tokens=2)

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, caller, "writing rules text", "template text",
    )
    assert result.ok is True

    sidecar_path = out_path.with_suffix(".py")
    assert sidecar_path.exists()
    assert sidecar_path.read_text(encoding="utf-8") == "def test_x():\n    # FAKEMOD:BR-001 [[FAKEMOD:1]]\n    pass\n"

    md_text = out_path.read_text(encoding="utf-8")
    assert "def test_x" not in md_text
    assert "FAKEMOD.py" in md_text
    assert "## Scenarios covered" in md_text
    assert "FAKEMOD:BR-001" in md_text

    # Round-trip: the slimmed .md must still validate clean on its own.
    from mfdoc.validate import validate_test_doc
    revalidated = validate_test_doc(conn, out_path)
    assert revalidated["ok"], revalidated["problems"]


def test_write_test_doc_with_sidecar_or_invalidate_removes_a_stale_sidecar_when_none_is_written(tmp_path):
    """Copilot review follow-up on issue #195's fix: `write_test_doc_with_
    sidecar` silently returns `None` without writing anything when the
    validated candidate's own code fence has no `MEMBER:BR-nnn`
    references at all -- a real, valid shape, not an error. Every call
    site in the single-document render path used to ignore that return
    value entirely, leaving a *previous* render's sidecar sitting on
    disk, now paired with a document that no longer references any of it
    -- the same "old sidecar survives an incompatible new document"
    mismatch the chunked path's own boundary-shift cleanup exists to
    prevent. `_write_test_doc_with_sidecar_or_invalidate` wraps every
    call site with this cleanup."""
    from mfdoc import testbatch

    out_path = tmp_path / "FAKEMOD.md"
    sidecar_path = tmp_path / "FAKEMOD.py"
    sidecar_path.write_text("# leftover from a prior render with real BR refs\n", encoding="utf-8")

    doc_text_no_br_refs = (
        "---\nsources: [\"FAKEMOD\"]\nlanguage: python\n---\n\n"
        "# FAKEMOD tests\n\n"
        "```python\ndef test_placeholder():\n    pass\n```\n"
    )
    written, cleanup_problem = testbatch._write_test_doc_with_sidecar_or_invalidate(
        None, "FAKEMOD", out_path, doc_text_no_br_refs, "python",
    )
    assert written is None, "no BR references in the fence -- nothing should be split out"
    assert cleanup_problem is None
    assert not sidecar_path.exists(), (
        "the stale sidecar from a previous render must be removed, not left orphaned "
        "next to a document that no longer references any of it"
    )


def test_write_test_doc_with_sidecar_or_invalidate_leaves_a_fresh_sidecar_alone(tmp_path):
    """The other half: when this call *does* produce a fresh sidecar (real
    `MEMBER:BR-nnn` references in the fence), nothing extra gets removed
    -- the new sidecar it just wrote is exactly what should be there."""
    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    rc1 = _insert_rc(conn, 1, 10)
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "COND", "citation": "[[FAKEMOD:10]]"}',
        then_json='{"citation": "[[FAKEMOD:10]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:10", confidence="verified",
    )
    conn.commit()

    out_path = tmp_path / "FAKEMOD.md"
    doc_text = (
        "---\nsources: [\"FAKEMOD\"]\n---\n\n# FAKEMOD tests\n\n"
        "```python\ndef test_one():\n    # FAKEMOD:BR-001\n    ...\n```\n"
    )
    written, cleanup_problem = testbatch._write_test_doc_with_sidecar_or_invalidate(
        conn, "FAKEMOD", out_path, doc_text, "python",
    )
    assert cleanup_problem is None
    assert written is not None
    assert written.exists()
    assert "FAKEMOD:BR-001" in written.read_text(encoding="utf-8")


def test_write_test_doc_with_sidecar_or_invalidate_reports_a_failed_stale_removal(tmp_path, monkeypatch):
    """Copilot review follow-up: a failed removal of the stale sidecar must
    be propagated as a problem, not just logged -- otherwise the render
    caller reports `ok=True` (a clean, resumable state) while a stale
    sidecar that a later standalone `mfdoc test-validate` sweep (or this
    same member's own next resumed run, via its recorded state) would
    treat as still current remains on disk next to a document that no
    longer references any of it."""
    from mfdoc import testbatch
    from pathlib import Path

    out_path = tmp_path / "FAKEMOD.md"
    sidecar_path = tmp_path / "FAKEMOD.py"
    sidecar_path.write_text("# leftover from a prior render with real BR refs\n", encoding="utf-8")

    real_unlink = Path.unlink

    def exploding_unlink(self, *args, **kwargs):
        if self == sidecar_path:
            raise OSError("simulated: file is locked by another process")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", exploding_unlink)

    doc_text_no_br_refs = (
        "---\nsources: [\"FAKEMOD\"]\nlanguage: python\n---\n\n"
        "# FAKEMOD tests\n\n"
        "```python\ndef test_placeholder():\n    pass\n```\n"
    )
    written, cleanup_problem = testbatch._write_test_doc_with_sidecar_or_invalidate(
        None, "FAKEMOD", out_path, doc_text_no_br_refs, "python",
    )
    assert written is None
    assert cleanup_problem is not None
    assert "could not remove stale sidecar" in cleanup_problem
    assert sidecar_path.exists()


def test_prior_fingerprint_for_does_not_crash_on_malformed_front_matter(tmp_path):
    """Copilot review follow-up on issue #195: a previous failed render
    can leave arbitrary YAML between the `---` markers on `out_path` --
    `split_frontmatter`'s `yaml.safe_load(...) or {}` only substitutes an
    empty dict for a *falsy* parse result (`None`/`""`/`[]`), not a
    truthy non-dict one like a bare scalar or a non-empty list, so
    `_prior_fingerprint_for` calling `.get()` on that would raise instead
    of returning `None` and letting the next render attempt recover."""
    from mfdoc.testbatch import _prior_fingerprint_for

    out_path = tmp_path / "FAKEMOD.md"
    out_path.write_text("---\njust a bare string, not a mapping\n---\nbody\n", encoding="utf-8")
    assert _prior_fingerprint_for(out_path) is None  # must not raise

    out_path.write_text("---\n- a\n- b\n---\nbody\n", encoding="utf-8")
    assert _prior_fingerprint_for(out_path) is None  # must not raise


def test_generate_member_test_doc_stamps_fingerprint_and_detects_a_later_insertion(tmp_path):
    """Copilot review follow-up on issue #195: an end-to-end guard through
    the *real* render path (`generate_member_test_doc` ->
    `write_test_doc_with_sidecar`), not just a hand-constructed
    `test_case_fingerprint` in a test fixture. If the fingerprint stamping
    in `write_test_doc_with_sidecar` were ever accidentally removed, this
    is what would catch it: a document rendered today, then a rule
    inserted into `rule_candidate` afterward (before `mfdoc test-plan`
    re-runs), must be re-detected as stale on the very next validation,
    exactly the insertion-after-range case
    `test_stale_sidecar_whose_old_ids_remain_a_subset_after_an_insertion`
    proves at the unit level -- this proves the same thing is actually
    wired up through the real write path."""
    from mfdoc import testbatch
    from mfdoc.validate import validate_test_doc
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")
    rc_id = insert(
        conn, "rule_candidate", member_id=1, line_no=10, construct="IF",
        condition="COND-1", raw="IF COND-1",
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc_id,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    doc_text = _valid_test_doc_text("python", "pytest")

    def caller(prompt):
        return ModelResponse(text=doc_text, input_tokens=1, output_tokens=2)

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, caller, "writing rules text", "template text",
    )
    assert result.ok is True

    md_text = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint:" in md_text, (
        "write_test_doc_with_sidecar must stamp a fingerprint into the "
        "document's front matter -- if this assertion ever fails, every "
        "document produced from here on falls back to the weaker "
        "id-overlap heuristic and the insertion-after-range case "
        "regresses silently"
    )

    fresh = validate_test_doc(conn, out_path)
    assert fresh["sidecar_stale"] is False, "a freshly written document must not look stale"

    # Now insert a second rule_candidate row *after* this document's own
    # BR-range -- FAKEMOD:BR-001 stays a literal subset of the new valid
    # set, the exact shape an id-overlap-only check cannot catch.
    insert(
        conn, "rule_candidate", member_id=1, line_no=20, construct="IF",
        condition="COND-2", raw="IF COND-2",
    )
    conn.commit()

    revalidated = validate_test_doc(conn, out_path)
    assert revalidated["sidecar_stale"] is True, (
        "the real write path's stamped fingerprint must detect this "
        "insertion the same way the unit-level test does"
    )
    assert revalidated["ok"], revalidated["problems"]


def test_rerender_after_an_insertion_is_not_falsely_rejected_against_the_old_sidecar(tmp_path):
    """Copilot review follow-up on issue #195 -- the critical case: without
    `_prior_fingerprint_for` threading the *previous* render's fingerprint
    into the validation of a freshly-generated candidate (whose own front
    matter never carries `test_case_fingerprint` -- only `write_test_doc_
    with_sidecar` adds it, after that validation succeeds), the render
    loop's own validation of a genuinely-fine new response would fall back
    to the id-overlap heuristic and falsely reject it: the old sidecar's
    id (`BR-001`) stays a literal subset of the post-insertion valid set
    (`{BR-001, BR-002}`), so it reads as "still current" and gets
    cross-checked against the fresh manifest -- which now legitimately
    also lists `BR-002` -- producing a false "listed in manifest but not
    found in sidecar" problem for a response that was actually correct.
    This is the exact scenario issue #195 exists to fix, reproduced
    through the real render/retry path end to end, not just a later
    standalone re-validation."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")
    rc1 = insert(
        conn, "rule_candidate", member_id=1, line_no=10, construct="IF",
        condition="COND-1", raw="IF COND-1",
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    def doc_text(ids: list[str]) -> str:
        fence = "\n".join(
            f"def test_{i.split('-')[-1]}():\n    # FAKEMOD:{i} [[FAKEMOD:1]]\n    pass" for i in ids
        )
        return f"""---
title: "FAKEMOD -- generated tests"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-01-01"
review_status: draft
confidence_summary:
  verified: {len(ids)}
language: python
framework: pytest
sources: ["FAKEMOD"]
---

# FAKEMOD tests

Covers the module as a whole [[FAKEMOD:1]].

```python
{fence}
```
"""

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path,
        lambda prompt: ModelResponse(text=doc_text(["BR-001"]), input_tokens=1, output_tokens=2),
        "writing rules text", "template text",
    )
    assert first.ok is True, first.problems

    # A derive rebuild + a real mfdoc test-plan re-run: a new rule (and its
    # test_case row) exist now, but the sidecar/fingerprint from the first
    # render above haven't been refreshed yet -- this run's re-render is
    # exactly what's supposed to refresh them.
    rc2 = insert(
        conn, "rule_candidate", member_id=1, line_no=20, construct="IF",
        condition="COND-2", raw="IF COND-2",
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc2,
        scenario_name="FAKEMOD:BR-002",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path,
        lambda prompt: ModelResponse(
            text=doc_text(["BR-001", "BR-002"]), input_tokens=1, output_tokens=2,
        ),
        "writing rules text", "template text",
    )
    assert second.ok is True, second.problems
    assert second.attempts == 1, (
        "a genuinely correct response must validate clean on the first "
        "attempt, not be falsely rejected against the stale old sidecar "
        "and consume a retry"
    )


def test_run_test_batch_pool_loop_rerender_after_an_insertion_is_not_falsely_rejected(tmp_path):
    """Copilot review follow-up: the previous test exercises
    `generate_member_test_doc`/`_generate_test_doc_from_brief`, but
    `run_test_batch`'s own separate inline `ThreadPoolExecutor` loop for
    non-chunked members (the ordinary `mfdoc test-batch` path most real
    runs actually take, per issue #188's own review history) duplicates
    the same `_prior_fingerprint_for` capture and `validate_test_doc`
    calls independently -- it needs its own regression, not just implicit
    coverage from the other path."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")
    rc1 = insert(
        conn, "rule_candidate", member_id=1, line_no=10, construct="IF",
        condition="COND-1", raw="IF COND-1",
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    def doc_text(ids: list[str]) -> str:
        fence = "\n".join(
            f"def test_{i.split('-')[-1]}():\n    # FAKEMOD:{i} [[FAKEMOD:1]]\n    pass" for i in ids
        )
        return f"""---
title: "FAKEMOD -- generated tests"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-01-01"
review_status: draft
confidence_summary:
  verified: {len(ids)}
language: python
framework: pytest
sources: ["FAKEMOD"]
---

# FAKEMOD tests

Covers the module as a whole [[FAKEMOD:1]].

```python
{fence}
```
"""

    out_dir = tmp_path / "out"
    first_ids = ["BR-001"]
    summary1 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir,
        lambda prompt: ModelResponse(text=doc_text(first_ids), input_tokens=1, output_tokens=2),
        "writing rules text", "template text",
    )
    assert summary1.ok == 1, summary1.results[0].problems

    rc2 = insert(
        conn, "rule_candidate", member_id=1, line_no=20, construct="IF",
        condition="COND-2", raw="IF COND-2",
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc2,
        scenario_name="FAKEMOD:BR-002",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    summary2 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir,
        lambda prompt: ModelResponse(
            text=doc_text(["BR-001", "BR-002"]), input_tokens=1, output_tokens=2,
        ),
        "writing rules text", "template text",
    )
    assert summary2.ok == 1, summary2.results[0].problems
    assert summary2.results[0].attempts == 1, (
        "a genuinely correct response through run_test_batch's own pool "
        "loop must validate clean on the first attempt, not be falsely "
        "rejected against the stale old sidecar"
    )


def test_legacy_sidecar_with_no_fingerprint_does_not_deadlock_after_an_insertion(tmp_path):
    """Copilot review follow-up on issue #195 -- the actual scenario the
    issue itself describes: a document/sidecar pair written *before* this
    fix ever shipped (no `test_case_fingerprint` anywhere, on either the
    document or `_prior_fingerprint_for`'s recovery attempt). Without
    `_render_time` bypassing the id-overlap fallback for exactly this
    case, this deadlocks rather than self-resolving: `test-plan` adding a
    new scenario after the old sidecar's range makes the old ids a
    coincidental subset of the new valid set, the id-overlap check reads
    that as "still current", the fresh candidate's manifest listing the
    new id gets rejected as "missing from sidecar" on *every* retry
    (nothing about the corpus changes between them), and because
    `write_test_doc_with_sidecar` only runs after a successful validation,
    the sidecar is never refreshed and never gets a fingerprint either --
    reproducing indefinitely on every future invocation, not just once."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")
    rc1 = insert(
        conn, "rule_candidate", member_id=1, line_no=10, construct="IF",
        condition="COND-1", raw="IF COND-1",
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    # Simulates pre-upgrade output: written directly to disk, never
    # through write_test_doc_with_sidecar, so no test_case_fingerprint
    # anywhere -- exactly what every real document looked like before
    # this fix existed.
    out_path = tmp_path / "FAKEMOD.md"
    out_path.write_text(
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

- FAKEMOD:BR-001
""",
        encoding="utf-8",
    )
    (tmp_path / "FAKEMOD.py").write_text(
        "def test_one():\n    # FAKEMOD:BR-001\n    ...\n", encoding="utf-8",
    )

    # A real mfdoc test-plan re-run: a new rule (and its test_case row)
    # exist now, but the legacy sidecar/document haven't been refreshed.
    rc2 = insert(
        conn, "rule_candidate", member_id=1, line_no=20, construct="IF",
        condition="COND-2", raw="IF COND-2",
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc2,
        scenario_name="FAKEMOD:BR-002",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    def doc_text(ids: list[str]) -> str:
        fence = "\n".join(
            f"def test_{i.split('-')[-1]}():\n    # FAKEMOD:{i} [[FAKEMOD:1]]\n    pass" for i in ids
        )
        return f"""---
title: "FAKEMOD -- generated tests"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-01-01"
review_status: draft
confidence_summary:
  verified: {len(ids)}
language: python
framework: pytest
sources: ["FAKEMOD"]
---

# FAKEMOD tests

Covers the module as a whole [[FAKEMOD:1]].

```python
{fence}
```
"""

    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path,
        lambda prompt: ModelResponse(
            text=doc_text(["BR-001", "BR-002"]), input_tokens=1, output_tokens=2,
        ),
        "writing rules text", "template text",
    )
    assert result.ok is True, result.problems
    assert result.attempts == 1, (
        "a legacy sidecar with no fingerprint context must not deadlock a "
        "genuinely correct rerender after an insertion"
    )
    # And the deadlock is now actually broken going forward: the freshly
    # written document has a real fingerprint stamped.
    assert "test_case_fingerprint:" in out_path.read_text(encoding="utf-8")


def test_sidecar_present_cross_checks_manifest_against_real_code(tmp_path):
    from mfdoc.validate import validate_test_doc
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json="{}", when_json="{}", then_json="{}",
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    md_path = tmp_path / "FAKEMOD.md"
    md_path.write_text(
        _valid_test_doc_text("python", "pytest").replace(
            "```python\ndef test_x():\n    # FAKEMOD:BR-001 [[FAKEMOD:1]]\n    pass\n```",
            "See [`FAKEMOD.py`](./FAKEMOD.py) for the generated test source.\n\n"
            "## Scenarios covered\n\n- FAKEMOD:BR-001\n",
        ),
        encoding="utf-8",
    )
    (tmp_path / "FAKEMOD.py").write_text(
        "def test_x():\n    # FAKEMOD:BR-001 [[FAKEMOD:1]]\n    pass\n", encoding="utf-8",
    )
    result = validate_test_doc(conn, md_path)
    assert result["ok"], result["problems"]


def test_sidecar_manifest_drift_is_flagged(tmp_path):
    """The manifest and the sidecar's real content disagreeing must fail
    validation -- otherwise a hand-edited or stale manifest could silently
    claim coverage the actual code doesn't have."""
    from mfdoc.validate import validate_test_doc
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json="{}", when_json="{}", then_json="{}",
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    md_path = tmp_path / "FAKEMOD.md"
    md_path.write_text(
        _valid_test_doc_text("python", "pytest").replace(
            "```python\ndef test_x():\n    # FAKEMOD:BR-001 [[FAKEMOD:1]]\n    pass\n```",
            "See [`FAKEMOD.py`](./FAKEMOD.py) for the generated test source.\n\n"
            "## Scenarios covered\n\n- FAKEMOD:BR-001\n",
        ),
        encoding="utf-8",
    )
    # Sidecar's real content doesn't actually reference FAKEMOD:BR-001 --
    # the manifest is claiming coverage the code doesn't have.
    (tmp_path / "FAKEMOD.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    result = validate_test_doc(conn, md_path)
    assert not result["ok"]
    assert any("manifest" in p for p in result["problems"])


def test_natural_and_mantis_templates_exist_and_load():
    """Both new templates must exist at the path cli._test_template_path
    computes, and must contain the front-matter/citation shape
    test-writing-rules.md requires -- a template that doesn't parse as
    valid front matter would make every render using it fail validation
    silently confusingly (looks like a model problem, is actually a
    template problem)."""
    natural_path = REPO_ROOT / "templates" / "tests" / "natural_natunit.md"
    mantis_path = REPO_ROOT / "templates" / "tests" / "mantis_native.md"
    assert natural_path.exists()
    assert mantis_path.exists()

    natural_text = natural_path.read_text(encoding="utf-8")
    mantis_text = mantis_path.read_text(encoding="utf-8")

    for text, language, framework in (
        (natural_text, "natural", "natunit"),
        (mantis_text, "mantis", "native"),
    ):
        assert f"language: {language}" in text
        assert f"framework: {framework}" in text
        assert "{MEMBER}" in text
        assert "{MEMBER}:BR-nnn" in text
        assert f"```{language}" in text
        assert "* " in text or "*\n" in text  # a `*`-prefixed comment line is present


def test_generate_member_test_doc_round_trips_natural_and_mantis(tmp_path):
    """A response shaped exactly like the natural/mantis templates must
    validate and split into the right sidecar extension -- proves the
    templates and testlang.py's new entries actually work together, not
    just that each exists in isolation."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    for language, framework, ext, fence_body in (
        ("natural", "natunit", "nsp",
         "* FAKEMOD:BR-001 [[FAKEMOD:1]]\nCALLNAT 'ASSERT-EQUAL' #EXPECTED #ACTUAL 'test_x'\n"),
        ("mantis", "native", "mantis",
         "* FAKEMOD:BR-001 [[FAKEMOD:1]]\nPERFORM FAKEMOD-UNDER-TEST\n"),
    ):
        doc_text = _valid_test_doc_text(language, framework).replace(
            "```python\ndef test_x():\n    # FAKEMOD:BR-001 [[FAKEMOD:1]]\n    pass\n```",
            f"```{language}\n{fence_body}```",
        )

        def caller(prompt, _doc_text=doc_text):
            return ModelResponse(text=_doc_text, input_tokens=1, output_tokens=2)

        out_path = tmp_path / language / "FAKEMOD.md"
        result = testbatch.generate_member_test_doc(
            conn, "FAKEMOD", language, framework, out_path, caller,
            "writing rules text", "template text",
        )
        assert result.ok is True, result.problems
        sidecar_path = out_path.with_suffix(f".{ext}")
        assert sidecar_path.exists()
        assert sidecar_path.read_text(encoding="utf-8") == fence_body


def test_silkcentral_and_uipath_templates_exist_and_load():
    silkcentral_path = REPO_ROOT / "templates" / "tests" / "silkcentral_testcase.md"
    uipath_path = REPO_ROOT / "templates" / "tests" / "uipath_testcase.md"
    assert silkcentral_path.exists()
    assert uipath_path.exists()

    for path, language in ((silkcentral_path, "silkcentral"), (uipath_path, "uipath")):
        text = path.read_text(encoding="utf-8")
        assert f"language: {language}" in text
        assert "framework: testcase" in text
        assert "{MEMBER}" in text
        assert "{MEMBER}:BR-nnn" in text
        # No LANGUAGE_EXTENSIONS entry for these two -- the fence must not
        # claim a language tag testlang.py would try to split on its own
        # extension; it's still fenced, just under a neutral content tag.
        assert f"```{language}" not in text


def test_generate_member_test_doc_keeps_silkcentral_and_uipath_embedded(tmp_path):
    """No LANGUAGE_EXTENSIONS entry for these two -- the fence must stay
    embedded in the .md rather than being split to a fabricated sidecar
    extension. write_test_doc_with_sidecar returning None must not be
    mistaken for a validation failure -- generate_member_test_doc's
    result.ok must still be True."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    for language in ("silkcentral", "uipath"):
        doc_text = _valid_test_doc_text(language, "testcase").replace(
            "```python\ndef test_x():\n    # FAKEMOD:BR-001 [[FAKEMOD:1]]\n    pass\n```",
            f"```{language}\n# FAKEMOD:BR-001 [[FAKEMOD:1]]\n- test_case_id: FAKEMOD-BR-001\n```",
        )

        def caller(prompt, _doc_text=doc_text):
            return ModelResponse(text=_doc_text, input_tokens=1, output_tokens=2)

        out_path = tmp_path / language / "FAKEMOD.md"
        result = testbatch.generate_member_test_doc(
            conn, "FAKEMOD", language, "testcase", out_path, caller,
            "writing rules text", "template text",
        )
        assert result.ok is True, result.problems
        assert "test_case_id: FAKEMOD-BR-001" in out_path.read_text(encoding="utf-8")
        assert not any(out_path.parent.glob("FAKEMOD.*testcase*"))


def test_testgen_matrix_reads_config_list():
    from mfdoc.cli import _testgen_matrix

    assert _testgen_matrix({}) == []
    assert _testgen_matrix({"matrix": []}) == []
    targets = _testgen_matrix({
        "matrix": [
            {"language": "python", "framework": "pytest"},
            {"language": "natural", "framework": "natunit", "template": "custom.md"},
        ]
    })
    assert targets == [
        {"language": "python", "framework": "pytest"},
        {"language": "natural", "framework": "natunit", "template": "custom.md"},
    ]


def test_test_batch_matrix_and_language_are_mutually_exclusive(cli_args, indexed_db, tmp_path):
    from types import SimpleNamespace

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members=None,
        language="python", framework="pytest", template=None, model=None,
        caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
        concurrency=1, state="", matrix=True,
    )
    rc = cli.cmd_test_batch(args)
    assert rc == 2


def test_test_batch_matrix_requires_config_matrix_entries(tmp_path):
    """--matrix with no options.testgen.matrix in config must fail cleanly,
    the same way missing --language/--framework already does, not crash
    on an empty target list."""
    import shutil
    from types import SimpleNamespace

    project_dir = tmp_path / "proj"
    shutil.copytree(REPO_ROOT / "examples", project_dir / "examples")
    shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
    shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")
    cfg_text = (REPO_ROOT / "project.yml").read_text(encoding="utf-8")
    (project_dir / "project.yml").write_text(cfg_text, encoding="utf-8")

    args = SimpleNamespace(
        config=str(project_dir / "project.yml"), out=str(tmp_path / "out"), members=None,
        language=None, framework=None, template=None, model=None,
        caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
        concurrency=1, state="", matrix=True,
    )
    # This project's checked-in options.testgen has no `matrix` key (until
    # Task 6 adds one) -- but even after Task 6 adds it, this test's
    # point is the *shape* of the error path, not this specific config's
    # absence of the key, so it stays valid either way as long as the
    # fixture project used here doesn't define one. Assert on the error
    # path directly instead of relying on that absence:
    from mfdoc import cli as cli_mod
    cfg = cli_mod.load_config(args.config)
    testgen_cfg = dict(cli_mod._testgen_config(cfg))
    testgen_cfg.pop("matrix", None)
    import unittest.mock as mock
    with mock.patch.object(cli_mod, "_testgen_config", return_value=testgen_cfg):
        rc = cli.cmd_test_batch(args)
    assert rc == 2


def test_test_gen_matrix_renders_every_configured_target(cli_args, indexed_db, tmp_path):
    from types import SimpleNamespace
    import shutil

    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")
    testplan.run_all(indexed_db, member_name="MMP0100")

    cfg = cli.load_config(cli_args.config)
    testgen_cfg = dict(cli._testgen_config(cfg))
    testgen_cfg["matrix"] = [
        {"language": "python", "framework": "pytest"},
        {"language": "natural", "framework": "natunit"},
    ]
    # --out is a single full-document path in non-matrix cmd_test_gen and is
    # mutually exclusive with --matrix (see
    # test_test_gen_out_and_matrix_are_mutually_exclusive); to point matrix
    # output at tmp_path without setting --out, override out_dir in config
    # instead -- this is the per-target default path cmd_test_gen falls
    # back to when --out is omitted.
    testgen_cfg["out_dir"] = str(tmp_path / "out")
    import unittest.mock as mock
    args = SimpleNamespace(
        config=cli_args.config, out=None,
        member="MMP0100", language=None, framework=None, template=None,
        model=None, caller="fake-echo", provider="anthropic",
        gcp_project=None, gcp_region=None, matrix=True,
    )
    with mock.patch.object(cli, "_testgen_config", return_value=testgen_cfg):
        cli.cmd_test_gen(args)
    # Not asserting the return code here: fake-echo (like every other
    # fake-echo-driven cmd_test_gen test in this file, e.g.
    # test_test_gen_prompt_carries_the_derived_scenarios) echoes the raw
    # prompt back as the "generated" document, which never passes
    # validate_test_doc's front-matter check -- generate_member_test_doc
    # still writes it to disk on every attempt, which is what's under test
    # here: that --matrix iterates every configured target and writes each
    # to its own per-target path. "mom" is this fixture project.yml's own
    # `system` -- out_dir gets a project-namespace subdirectory even when
    # explicitly configured (see cli._project_namespace's docstring for why
    # an explicit-but-still-shared out_dir doesn't get index_db's
    # "explicit means distinguishing" exemption).
    python_out = (tmp_path / "out" / "mom" / "natural" / "MILLPROD" / "python" / "pytest" / "MMP0100.md")
    natural_out = (tmp_path / "out" / "mom" / "natural" / "MILLPROD" / "natural" / "natunit" / "MMP0100.md")
    assert python_out.exists()
    assert natural_out.exists()
    assert "python/pytest" in python_out.read_text(encoding="utf-8")
    assert "natural/natunit" in natural_out.read_text(encoding="utf-8")


def test_test_gen_out_and_matrix_are_mutually_exclusive(cli_args, tmp_path):
    from types import SimpleNamespace

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "X.md"),
        member="MMP0100", language=None, framework=None, template=None,
        model=None, caller="fake-echo", provider="anthropic",
        gcp_project=None, gcp_region=None, matrix=True,
    )
    rc = cli.cmd_test_gen(args)
    assert rc == 2


# --- Finding 1: malformed options.testgen.matrix entries must exit 2, not
# crash with a raw traceback (KeyError/AttributeError) from the per-target
# loop's unguarded target["language"], target["framework"] access. ---

def _matrix_cfg_missing(testgen_cfg: dict, bad_entry) -> dict:
    cfg = dict(testgen_cfg)
    cfg["matrix"] = [bad_entry]
    return cfg


def test_test_batch_matrix_entry_missing_framework_exits_cleanly(cli_args, indexed_db, tmp_path):
    import unittest.mock as mock
    from types import SimpleNamespace

    cfg = cli.load_config(cli_args.config)
    testgen_cfg = _matrix_cfg_missing(cli._testgen_config(cfg), {"language": "python"})

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members=None,
        language=None, framework=None, template=None, model=None,
        caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
        concurrency=1, state="", matrix=True,
    )
    with mock.patch.object(cli, "_testgen_config", return_value=testgen_cfg):
        rc = cli.cmd_test_batch(args)
    assert rc == 2


def test_test_batch_matrix_entry_missing_language_exits_cleanly(cli_args, indexed_db, tmp_path):
    import unittest.mock as mock
    from types import SimpleNamespace

    cfg = cli.load_config(cli_args.config)
    testgen_cfg = _matrix_cfg_missing(cli._testgen_config(cfg), {"framework": "pytest"})

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members=None,
        language=None, framework=None, template=None, model=None,
        caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
        concurrency=1, state="", matrix=True,
    )
    with mock.patch.object(cli, "_testgen_config", return_value=testgen_cfg):
        rc = cli.cmd_test_batch(args)
    assert rc == 2


def test_test_batch_matrix_entry_not_a_mapping_exits_cleanly(cli_args, indexed_db, tmp_path):
    """A scalar matrix entry (e.g. `matrix: [python]`, a plausible typo for
    `matrix: [{language: python, framework: pytest}]`) must not crash with
    AttributeError on `.get` -- same clean exit-2 treatment as the
    dict-but-incomplete cases above."""
    import unittest.mock as mock
    from types import SimpleNamespace

    cfg = cli.load_config(cli_args.config)
    testgen_cfg = _matrix_cfg_missing(cli._testgen_config(cfg), "python")

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members=None,
        language=None, framework=None, template=None, model=None,
        caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
        concurrency=1, state="", matrix=True,
    )
    with mock.patch.object(cli, "_testgen_config", return_value=testgen_cfg):
        rc = cli.cmd_test_batch(args)
    assert rc == 2


def test_test_gen_matrix_entry_missing_framework_exits_cleanly(cli_args, indexed_db, tmp_path):
    import unittest.mock as mock
    from types import SimpleNamespace

    cfg = cli.load_config(cli_args.config)
    testgen_cfg = _matrix_cfg_missing(cli._testgen_config(cfg), {"language": "python"})

    args = SimpleNamespace(
        config=cli_args.config, out=None,
        member="MMP0100", language=None, framework=None, template=None,
        model=None, caller="fake-echo", provider="anthropic",
        gcp_project=None, gcp_region=None, matrix=True,
    )
    with mock.patch.object(cli, "_testgen_config", return_value=testgen_cfg):
        rc = cli.cmd_test_gen(args)
    assert rc == 2


def test_test_gen_matrix_entry_missing_language_exits_cleanly(cli_args, indexed_db, tmp_path):
    import unittest.mock as mock
    from types import SimpleNamespace

    cfg = cli.load_config(cli_args.config)
    testgen_cfg = _matrix_cfg_missing(cli._testgen_config(cfg), {"framework": "pytest"})

    args = SimpleNamespace(
        config=cli_args.config, out=None,
        member="MMP0100", language=None, framework=None, template=None,
        model=None, caller="fake-echo", provider="anthropic",
        gcp_project=None, gcp_region=None, matrix=True,
    )
    with mock.patch.object(cli, "_testgen_config", return_value=testgen_cfg):
        rc = cli.cmd_test_gen(args)
    assert rc == 2


# --- Finding 2: spec-mandated test -- one --matrix invocation running the
# same members through two different {language, framework} targets, sharing
# one --state file, must produce two independent output subtrees and two
# independent per-member state entries. ---

def test_run_test_batch_matrix_targets_get_independent_output_and_state(tmp_path):
    """Same members, same shared state_path, two different (language,
    framework) targets in turn (as cmd_test_batch --matrix does) -- proves
    the shared-state-file claim in the design spec's Matrix support section:
    per-member state keys already include language/framework, so one
    target's resume bookkeeping and rendered output tree can't collide
    with another's."""
    from mfdoc import testbatch
    import json
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute(
        "INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')"
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    def caller_for(language, framework):
        def _call(prompt):
            return ModelResponse(
                text=_valid_test_doc_text(language, framework), input_tokens=0, output_tokens=0
            )
        return _call

    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"

    pytest_summary = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller_for("python", "pytest"),
        "writing rules text", "template text", state_path=state_path,
    )
    unittest_summary = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "unittest", out_dir, caller_for("python", "unittest"),
        "writing rules text", "template text", state_path=state_path,
    )

    assert pytest_summary.skipped == 0
    assert unittest_summary.skipped == 0

    # Two independent output subtrees.
    pytest_path = out_dir / "natural" / "python" / "pytest" / "FAKEMOD.md"
    unittest_path = out_dir / "natural" / "python" / "unittest" / "FAKEMOD.md"
    assert pytest_path.exists()
    assert unittest_path.exists()
    assert "framework: pytest" in pytest_path.read_text(encoding="utf-8")
    assert "framework: unittest" in unittest_path.read_text(encoding="utf-8")

    # Two independent per-member state entries in the one shared state file.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pytest_key = "natural::FAKEMOD::python::pytest"
    unittest_key = "natural::FAKEMOD::python::unittest"
    assert pytest_key in state
    assert unittest_key in state
    assert pytest_key != unittest_key
    assert state[pytest_key]["ok"] is True
    assert state[unittest_key]["ok"] is True
    assert "brief_sha256" in state[pytest_key]
    assert "brief_sha256" in state[unittest_key]
    assert state[pytest_key]["brief_sha256"] == state[unittest_key]["brief_sha256"], (
        "same member/brief content across targets -- only language/framework differ, "
        "which the state *key* encodes, not the brief hash"
    )


# --- Chunked rendering for oversized members (see DEFAULT_MAX_SCENARIOS_PER_CALL) ---

def _seed_fakemod_scenarios(conn, count: int):
    from mfdoc.db import insert

    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    for n in range(1, count + 1):
        insert(
            conn, "test_case", member_id=1, kind="unit", scenario_name=f"FAKEMOD:BR-{n:03d}",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
            status="characterization", citation="FAKEMOD:1", confidence="verified",
        )
    conn.commit()


def _seed_fakemod_scenarios_with_routines(conn, per_routine: dict[str, int]):
    """Like _seed_fakemod_scenarios, but each scenario is linked (via
    rule_candidate_id) to a rule_candidate that falls inside a named
    routine -- `per_routine` maps routine name to how many scenarios it
    gets, in the order given. Used to prove chunking and brief rendering
    both honour routine boundaries for generated tests, the same way they
    already do for module docs."""
    from mfdoc.db import insert

    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    line = 1
    n = 0
    for routine_name, count in per_routine.items():
        start = line
        for _ in range(count):
            n += 1
            line += 1
            rc_id = insert(
                conn, "rule_candidate", member_id=1, line_no=line, construct="IF",
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
        insert(conn, "routine", member_id=1, name=routine_name, kind="natural_subroutine",
               start_line=start, end_line=line)
        line += 1
    conn.commit()


def _seed_fakemod_scenarios_with_lines(conn, line_nos: list[int]):
    """Like _seed_fakemod_scenarios, but each scenario is linked (via
    rule_candidate_id) to a rule_candidate at an explicit line_no -- lets a
    test control source-line spacing directly, to build a deliberately
    sparse (source-dense) chunk alongside ordinary ones (issue #105)."""
    from mfdoc.db import insert

    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    max_line = max(line_nos)
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, ?, 'irrelevant')", (max_line,))
    for n, line_no in enumerate(line_nos, start=1):
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
    conn.commit()


def _chunk_aware_caller(language: str, framework: str):
    """A fake caller that returns a fully valid single-chunk document citing
    exactly the FAKEMOD:BR-nnn ids present in the prompt it was sent --
    mirrors what a real model does for one chunk's brief, without a real
    call. Reused across the chunking tests below."""
    import re as _re

    ids_re = _re.compile(r"FAKEMOD:BR-\d+")

    def caller(prompt: str) -> ModelResponse:
        ids = sorted(set(ids_re.findall(prompt)))
        fence_lines = "\n".join(
            f"def test_{i.split('-')[-1]}():\n    # {i} [[FAKEMOD:1]]\n    pass" for i in ids
        )
        text = f"""---
title: "FAKEMOD — generated tests"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-09-02"
review_status: draft
confidence_summary:
  verified: {len(ids)}
language: {language}
framework: {framework}
sources: ["FAKEMOD"]
---

# FAKEMOD tests

Covers the module as a whole [[FAKEMOD:1]].

```python
{fence_lines}
```
"""
        return ModelResponse(text=text, input_tokens=1, output_tokens=2)
    return caller


def test_generate_member_test_doc_chunks_an_oversized_member(tmp_path):
    """A member whose test_case set exceeds max_scenarios_per_call renders
    as several independent chunk documents plus a deterministic index doc
    at the normal out_path -- proves the whole chunked path end to end:
    every chunk validates and gets its own sidecar, the index aggregates
    real (not invented) confidence numbers from the chunks, and every
    scenario across all 5 rows ends up in the index's manifest."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert result.ok is True, result.problems
    assert result.attempts == 3  # ceil(5 / 2) chunks

    for i in (1, 2, 3):
        chunk_path = tmp_path / f"FAKEMOD.chunk{i}.md"
        assert chunk_path.exists()
        from mfdoc.validate import validate_test_doc
        assert validate_test_doc(conn, chunk_path)["ok"]

    index_text = out_path.read_text(encoding="utf-8")
    assert "language: python" in index_text
    assert "framework: pytest" in index_text
    assert "doc_type: generated_test" in index_text
    assert "verified: 5" in index_text, "confidence_summary must aggregate all 5 chunked scenarios"
    for n in range(1, 6):
        assert f"FAKEMOD:BR-{n:03d}" in index_text

    from mfdoc.validate import validate_test_doc
    revalidated = validate_test_doc(conn, out_path)
    assert revalidated["ok"], revalidated["problems"]


def test_generate_member_test_doc_reports_failure_when_one_chunk_fails(tmp_path):
    """One bad chunk must fail the whole member (ok=False) with a problem
    naming which chunk, but must not prevent the other chunks from
    rendering and validating on their own -- proves chunks are judged
    independently, not all-or-nothing on the first failure."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    good_caller = _chunk_aware_caller("python", "pytest")

    def flaky_caller(prompt: str) -> ModelResponse:
        if "BR-003" in prompt:
            return ModelResponse(text="not a valid document", input_tokens=1, output_tokens=1)
        return good_caller(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, flaky_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert result.ok is False
    assert any("chunk 2" in p for p in result.problems)

    assert (tmp_path / "FAKEMOD.chunk1.md").exists()
    from mfdoc.validate import validate_test_doc
    assert validate_test_doc(conn, tmp_path / "FAKEMOD.chunk1.md")["ok"]

    index_text = out_path.read_text(encoding="utf-8")
    assert "FAKEMOD:BR-001" in index_text and "FAKEMOD:BR-002" in index_text
    assert "FAKEMOD:BR-003" not in index_text, "a failed chunk's scenarios must not be claimed as covered"


def test_generate_member_test_doc_failed_chunk_diagnostics_flag_a_sparse_outlier(tmp_path):
    """Issue #105: two ordinary chunks (2 scenarios each, tightly packed
    source lines) plus one deliberately sparse chunk (2 scenarios, but
    their originating rules sit far apart in the source) -- when the
    sparse chunk is the one that fails, its own reported problem must
    carry a density diagnostic marking it an outlier relative to its
    siblings, mirroring batch.py's module-doc chunking (shared
    implementation in brief.py)."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios_with_lines(conn, [
        1, 2,      # chunk 1: tight
        10, 11,    # chunk 2: tight
        20, 90,    # chunk 3: sprawling source -- the outlier
    ])

    good_caller = _chunk_aware_caller("python", "pytest")

    def flaky_caller(prompt: str) -> ModelResponse:
        if "BR-005" in prompt:
            return ModelResponse(text="not a valid document", input_tokens=1, output_tokens=1)
        return good_caller(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, flaky_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert result.ok is False
    dense_problems = [p for p in result.problems if "chunk 3" in p]
    assert dense_problems, result.problems
    assert "density:" in dense_problems[0]
    assert "OUTLIER" in dense_problems[0]
    assert not any("chunk 1" in p and "OUTLIER" in p for p in result.problems)
    assert not any("chunk 2" in p and "OUTLIER" in p for p in result.problems)


def test_generate_member_test_doc_chunks_by_routine_not_flat_count(tmp_path):
    """Two routines, X (3 scenarios) and Y (2 scenarios) -- with
    max_scenarios_per_call=2, a flat count would cut X down the middle;
    routine-aware chunking must keep X whole as an oversized chunk and Y
    as its own, matching batch.py's identical guarantee for module docs."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios_with_routines(conn, {"X": 3, "Y": 2})

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert result.ok is True, result.problems
    assert result.attempts == 2, "X (3 scenarios) and Y (2 scenarios) pack as two chunks, not three"

    chunk1 = (tmp_path / "FAKEMOD.chunk1.md").read_text(encoding="utf-8")
    chunk2 = (tmp_path / "FAKEMOD.chunk2.md").read_text(encoding="utf-8")
    for n in (1, 2, 3):
        assert f"FAKEMOD:BR-{n:03d}" in chunk1
        assert f"FAKEMOD:BR-{n:03d}" not in chunk2
    for n in (4, 5):
        assert f"FAKEMOD:BR-{n:03d}" in chunk2


def test_test_case_brief_tags_scenarios_with_their_routine():
    """test_case_brief must surface each scenario's routine, the same
    grouping cue module_brief gives narrative docs."""
    import sqlite3

    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios_with_routines(conn, {"X": 2})

    brief = testplan.test_case_brief(conn, "FAKEMOD")
    assert "routine: `X`" in brief


def test_failed_chunk_confidence_is_not_counted_in_the_index(tmp_path):
    """A chunk can fail validate_test_doc for a reason unrelated to its
    front matter (here: an invented scenario id) while still returning a
    perfectly parseable, confident-looking confidence_summary. The index's
    aggregated confidence must come only from chunks whose scenarios it
    actually claims as covered -- summing a failed chunk's numbers would
    silently over-report confidence for coverage that doesn't exist."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    good_caller = _chunk_aware_caller("python", "pytest")

    def flaky_caller(prompt: str) -> ModelResponse:
        if "BR-003" in prompt:
            # Valid, parseable front matter with a confident-looking
            # summary -- but the fence references an invented scenario id,
            # so it fails validate_test_doc's scenario-existence check, not
            # a front-matter problem.
            text = """---
title: "FAKEMOD chunk"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-09-02"
review_status: draft
confidence_summary:
  verified: 99
language: python
framework: pytest
sources: ["FAKEMOD"]
---

```python
def test_invented():
    # FAKEMOD:BR-999 [[FAKEMOD:1]]
    pass
```
"""
            return ModelResponse(text=text, input_tokens=1, output_tokens=1)
        return good_caller(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, flaky_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert result.ok is False

    index_text = out_path.read_text(encoding="utf-8")
    assert "verified: 2" in index_text, (
        "only the ok chunk's 2 scenarios should count -- the failed chunk's "
        "fabricated 'verified: 99' must not leak into the index"
    )
    assert "verified: 99" not in index_text
    assert "verified: 101" not in index_text


def test_run_test_batch_per_member_skip_sees_a_rule_candidate_only_change(tmp_path):
    """Copilot review follow-up on issue #195: `run_test_batch`'s
    per-member resume skip hashes `test_case_brief()`'s output, which only
    ever reads `test_case` -- never `rule_candidate` directly. A `derive`
    rebuild that inserts a `rule_candidate` row for this member *before*
    `mfdoc test-plan` re-runs to reflect it in `test_case` would leave
    that brief (and therefore the old per-member hash) completely
    unchanged, wrongly skipping re-rendering and leaving a now-stale
    sidecar in place indefinitely -- a different resume layer from
    `_corpus_signature`'s global fast path, which alone doesn't cover
    this. `member_rule_fingerprint` folded into this hash is what closes
    it."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 2)

    caller = _chunk_aware_caller("python", "pytest")
    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"

    summary1 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=10,
    )
    assert summary1.skipped == 0 and summary1.ok == 1

    # A rule_candidate row appears (a derive rebuild), but no new test_case
    # row yet -- test_case_brief()'s output is byte-identical to before.
    insert(
        conn, "rule_candidate", member_id=1, line_no=99, construct="IF",
        condition="COND-NEW", raw="IF COND-NEW",
    )
    conn.commit()

    summary2 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=10,
    )
    assert summary2.skipped == 0, (
        "a rule_candidate-only change must not be masked by the per-member "
        "brief-hash skip just because test_case_brief() itself is unchanged"
    )
    assert summary2.ok == 1


def test_run_test_batch_threshold_change_is_not_masked_by_resume_state(tmp_path):
    """Changing max_scenarios_per_call between runs must not be treated as
    'nothing changed' by resumable skip -- the same test_case content can
    need a different output shape (single doc vs. chunked) purely because
    the threshold moved, and both the corpus-level signature and the
    per-member brief hash need to reflect that."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    caller = _chunk_aware_caller("python", "pytest")
    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"

    # First run: threshold above the member's row count -- renders as one doc.
    summary1 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=10,
    )
    assert summary1.skipped == 0 and summary1.ok == 1
    single_path = out_dir / "natural" / "python" / "pytest" / "FAKEMOD.md"
    assert single_path.exists()
    assert not (out_dir / "natural" / "python" / "pytest" / "FAKEMOD.chunk1.md").exists()

    # Second run: same test_case content, lower threshold -- must re-render
    # as chunks, not get skipped as a no-op.
    summary2 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=2,
    )
    assert summary2.skipped == 0, "a threshold change must not be treated as a no-op by resume/skip"
    assert summary2.ok == 1
    assert (out_dir / "natural" / "python" / "pytest" / "FAKEMOD.chunk1.md").exists()


def test_run_test_batch_member_level_resume_skip_sees_a_missing_sidecar(tmp_path):
    """Copilot review follow-up: a previously successful *split* single-
    document output can lose its `.py`/`.nsp` sidecar (deleted out from
    under this tool, or lost to some other bug) while the database and
    resume state stay otherwise unchanged -- `corpus_unchanged` alone
    can't see that, and unlike `_test_chunk_reuse_ok` on the chunked path,
    nothing else in the member-level resume skip would ever notice the
    executable source is gone. Must force a real re-render, not keep
    reporting the incomplete output as reusable indefinitely."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 1)

    caller = _chunk_aware_caller("python", "pytest")
    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"

    summary1 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=10,
    )
    assert summary1.skipped == 0 and summary1.ok == 1
    out_path = out_dir / "natural" / "python" / "pytest" / "FAKEMOD.md"
    sidecar_path = out_path.with_suffix(".py")
    assert sidecar_path.exists()
    sidecar_path.unlink()

    # Nothing about the corpus or resume state changed -- only the
    # sidecar file disappeared from disk.
    summary2 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=10,
    )
    assert summary2.skipped == 0, "a missing sidecar must force a real re-render, not a resumed skip"
    assert summary2.ok == 1
    assert sidecar_path.exists(), "the re-render must recreate the missing sidecar"


def test_run_test_batch_still_skips_an_unchanged_chunked_member_on_resume(tmp_path):
    """Copilot review follow-up: the chunk *index* document
    (`_render_chunk_index`'s own output) carries a `## Scenarios covered`
    section too -- its own aggregate across every chunk -- but never gets
    a sidecar of its own at all (each chunk gets its own instead). The
    member-level resume skip's missing-sidecar check (added above) must
    not mistake that index for a split document that lost its sidecar --
    otherwise every unchanged *chunked* member would be forced through a
    full rebuild on every single resume, defeating resumability for the
    entire chunked path."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    caller = _counting_caller(_chunk_aware_caller("python", "pytest"))
    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"

    summary1 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=2,
    )
    assert summary1.skipped == 0 and summary1.ok == 1
    assert caller.calls == 2, "sanity check: two chunks rendered"

    # Nothing changed at all -- must be a resumed skip, no model calls.
    summary2 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=2,
    )
    assert summary2.skipped == 1, (
        "an unchanged chunked member's index must not be misread as a split "
        "document missing its sidecar"
    )
    assert caller.calls == 2, "no new model calls -- the resume must be a true skip"


def test_run_test_batch_chunked_member_resume_skip_sees_a_missing_chunk_file(tmp_path):
    """Copilot review follow-up (round 40): the member-level resume skip's
    missing-sidecar check only ever looks at `out_path` itself, which for
    a chunked member is the deterministic *index* document -- one that,
    by design, never has its own sidecar and is deliberately excluded
    from that check (see the test above). Nothing there ever verified
    that the chunk files the index links to are still on disk. If a
    chunk file is deleted out from under this tool while the database,
    resume state, and index all stay otherwise unchanged, this must
    force a real re-render of the missing chunk, not keep reporting the
    now-incomplete output as reusable indefinitely."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    caller = _counting_caller(_chunk_aware_caller("python", "pytest"))
    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"

    summary1 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=2,
    )
    assert summary1.skipped == 0 and summary1.ok == 1
    assert caller.calls == 2, "sanity check: two chunks rendered"

    chunk_dir = out_dir / "natural" / "python" / "pytest"
    chunk1 = chunk_dir / "FAKEMOD.chunk1.md"
    assert chunk1.exists()
    chunk1.unlink()

    summary2 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=2,
    )
    assert summary2.skipped == 0, "a missing chunk file must force a real re-render, not a resumed skip"
    assert summary2.ok == 1
    assert chunk1.exists(), "the re-render must recreate the missing chunk"


def test_run_test_batch_does_not_abort_the_whole_run_when_a_chunked_member_raises(tmp_path):
    """Copilot review follow-up: `run_test_batch`'s own dispatch loop for
    chunked members had no `try/except` around `generate_member_test_doc`
    -- several of that function's own filesystem writes now raise on
    failure rather than swallowing it (issue #195's several review
    rounds), so a real, if rare, failure (a transient filesystem error
    mid-render) would propagate all the way out of `run_test_batch`,
    discarding every *other* member's already-checkpointed result in the
    same batch along with it. Must be caught and reported as this one
    member's own failure instead."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")
    for n in range(1, 5):
        rc = insert(
            conn, "rule_candidate", member_id=1, line_no=n, construct="IF",
            condition=f"COND-{n}", raw=f"IF COND-{n}",
        )
        insert(
            conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc,
            scenario_name=f"FAKEMOD:BR-{n:03d}",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
            then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
            status="characterization", citation="FAKEMOD:1", confidence="verified",
        )
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'OTHERMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (2, 1, 'irrelevant')")
    rc_other = insert(conn, "rule_candidate", member_id=2, line_no=1, construct="IF", condition="X", raw="IF X")
    insert(
        conn, "test_case", member_id=2, kind="unit", rule_candidate_id=rc_other,
        scenario_name="OTHERMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[OTHERMOD:1]]"}',
        then_json='{"citation": "[[OTHERMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="OTHERMOD:1", confidence="verified",
    )
    conn.commit()

    def exploding_generate_member_test_doc(*args, **kwargs):
        if args[1] == "FAKEMOD":
            raise RuntimeError("simulated: a bug deep in the chunked render path")
        return real_generate_member_test_doc(*args, **kwargs)

    real_generate_member_test_doc = testbatch.generate_member_test_doc
    import unittest.mock
    with unittest.mock.patch.object(testbatch, "generate_member_test_doc", exploding_generate_member_test_doc):
        summary = testbatch.run_test_batch(
            conn, ["FAKEMOD", "OTHERMOD"], "python", "pytest", tmp_path,
            _chunk_aware_caller("python", "pytest"),
            "writing rules text", "template text", max_scenarios_per_call=2,
        )

    by_name = {r.member: r for r in summary.results}
    assert by_name["FAKEMOD"].ok is False
    assert any("chunked render raised" in p for p in by_name["FAKEMOD"].problems)
    assert by_name["OTHERMOD"].ok is True, "the other member's own result must not be lost"


def test_shrinking_back_below_threshold_removes_leftover_chunk_files_and_sidecars(tmp_path):
    """Copilot review follow-up on issue #195: the symmetric direction of
    the threshold-change test above. A member that *shrinks* back under
    the chunking threshold goes straight to the single-document path,
    which never revisits its old `.chunk<N>.md`/sidecar pairs -- left
    alone, a full tree walk (`mfdoc test-validate`) would still find and
    validate those orphaned files independently, where their stale
    manifests/sidecars can produce false staleness failures for
    documents nothing renders into any more."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    caller = _chunk_aware_caller("python", "pytest")
    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"
    docs_dir = out_dir / "natural" / "python" / "pytest"

    # First run: chunked.
    summary1 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=2,
    )
    assert summary1.ok == 1
    chunk_md = docs_dir / "FAKEMOD.chunk1.md"
    chunk_sidecar = docs_dir / "FAKEMOD.chunk1.py"
    assert chunk_md.exists()
    assert chunk_sidecar.exists()

    # Second run: higher threshold -- shrinks back to a single document.
    summary2 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=10,
    )
    assert summary2.ok == 1, summary2.results[0].problems
    assert not chunk_md.exists(), "leftover chunk index file must be removed"
    assert not chunk_sidecar.exists(), "leftover chunk sidecar must be removed"


def test_chunked_rerender_defers_pruning_extra_chunks_until_the_new_index_is_committed(
    tmp_path, monkeypatch,
):
    """Copilot review follow-up: a chunked-to-chunked rerender that shrinks
    `chunk_count` (e.g. the threshold was raised) must not prune the now-
    extra `.chunk<N>` files until the *new*, smaller index has actually
    been written -- those files are still referenced by the *old* index
    still on disk. Pruning them first and only then attempting the new
    index (which can still fail, here simulated as a bug in
    `_render_chunk_index` itself) would leave that old, still-current
    index pointing at chunk files that no longer exist."""
    import pytest

    from mfdoc import testbatch

    conn = _sqlite_conn()
    _seed_fakemod_scenarios(conn, 6)

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True
    assert first.chunk_state is not None and set(first.chunk_state) == {"1", "2", "3"}
    chunk3_path = tmp_path / "FAKEMOD.chunk3.md"
    chunk3_sidecar = tmp_path / "FAKEMOD.chunk3.py"
    assert chunk3_path.exists() and chunk3_sidecar.exists()
    old_index_text = out_path.read_text(encoding="utf-8")

    def exploding_render_chunk_index(*args, **kwargs):
        raise RuntimeError("simulated: a bug in _render_chunk_index itself")

    monkeypatch.setattr(testbatch, "_render_chunk_index", exploding_render_chunk_index)

    with pytest.raises(RuntimeError):
        testbatch.generate_member_test_doc(
            conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
            "writing rules text", "template text", max_scenarios_per_call=3,
            prior_chunks=first.chunk_state,
        )

    assert chunk3_path.exists(), "the now-extra chunk 3 document must survive an index that never committed"
    assert chunk3_sidecar.exists(), "the now-extra chunk 3 sidecar must survive too"
    assert out_path.read_text(encoding="utf-8") == old_index_text, (
        "out_path itself (the old index, still referencing chunk 3) must be untouched"
    )


def test_chunked_index_removes_a_leftover_single_doc_sidecar(tmp_path):
    """Copilot review follow-up on issue #195: a member that grows past
    the chunking threshold between runs leaves its *prior single-document*
    sidecar (`FAKEMOD.py`) on disk -- `_prune_stale_chunk_files` only ever
    removes `.chunk<N>` files, deliberately, and the chunked index document
    at that same `out_path` never gets its own sidecar (each chunk gets
    its own). `sidecar_path_for` is purely path-based, though, so without
    removing that leftover file, the index document's own aggregated
    manifest would be cross-checked against unrelated old single-doc
    content -- a fingerprint on the index document alone can't fix this,
    since the *old sidecar itself* has no fingerprint field to update, and
    would still be found "usable" by the legacy id-overlap fallback
    whenever its old ids happen to still resolve."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    caller = _chunk_aware_caller("python", "pytest")
    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"
    docs_dir = out_dir / "natural" / "python" / "pytest"

    # First run: single document -- write_test_doc_with_sidecar creates
    # FAKEMOD.py, referencing the same ids the (identical) index would
    # later aggregate, so a naive test wouldn't distinguish "removed" from
    # "still there but coincidentally matches".
    summary1 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=10,
    )
    assert summary1.ok == 1
    leftover_sidecar = docs_dir / "FAKEMOD.py"
    assert leftover_sidecar.exists()

    # Second run: lower threshold forces chunking. Must validate clean --
    # the leftover single-doc sidecar must not be cross-checked against
    # the new chunked index's own manifest.
    summary2 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
        max_scenarios_per_call=2,
    )
    assert summary2.ok == 1, summary2.results[0].problems
    assert not leftover_sidecar.exists(), (
        "the prior single-doc sidecar must be removed once this out_path "
        "becomes a chunked index -- it no longer describes anything real"
    )


def test_resolve_max_scenarios_per_call():
    """`None` (not configured) falls back to the default; an explicit
    positive int is respected as-is; 0 or negative must raise rather than
    silently substituting the default -- `or DEFAULT` would have treated
    an intentional 0 the same as "not configured", masking either a real
    use case or a real misconfiguration."""
    import pytest
    from mfdoc.testbatch import DEFAULT_MAX_SCENARIOS_PER_CALL, _resolve_max_scenarios_per_call

    assert _resolve_max_scenarios_per_call(None) == DEFAULT_MAX_SCENARIOS_PER_CALL
    assert _resolve_max_scenarios_per_call(5) == 5
    with pytest.raises(ValueError):
        _resolve_max_scenarios_per_call(0)
    with pytest.raises(ValueError):
        _resolve_max_scenarios_per_call(-1)


def test_generate_member_test_doc_chunked_validates_the_index_document(tmp_path, monkeypatch):
    """The index document is built deterministically, not model-generated,
    but that's not a reason to skip checking it -- a bug in
    _render_chunk_index must surface as a reported failure, not a silent
    ok=True just because every chunk happened to validate on its own.
    Simulated here by monkeypatching _render_chunk_index to return content
    that fails validate_test_doc outright."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    monkeypatch.setattr(testbatch, "_render_chunk_index", lambda *a, **k: "not a valid document")

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert result.ok is False
    assert any("index document" in p for p in result.problems)


# --- Issue #88: port batch.py's content-hash chunk-skip/reuse logic into
# testbatch.py's chunked path, mirroring batch.py's own
# test_chunk_resume_*/test_run_batch_persists_chunk_state_and_reuses_it_
# across_calls tests (tests/test_batch.py) verbatim in spirit. ---

def _counting_caller(caller):
    """Wraps any ModelCaller to also expose a `.calls` count -- used to
    prove a resumed chunk generation makes zero (or exactly the expected
    number of) model calls, not just that it produces the right output.
    (Mirrors tests/test_batch.py's helper of the same name.)"""
    def counted(prompt: str) -> ModelResponse:
        counted.calls += 1
        return caller(prompt)
    counted.calls = 0
    return counted


def test_chunk_resume_makes_no_model_calls_when_every_chunk_brief_is_unchanged(tmp_path):
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    first_caller = _counting_caller(_chunk_aware_caller("python", "pytest"))
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, first_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True
    assert first_caller.calls == 3  # ceil(5 / 2) chunks, no whole-member narrative step
    assert first.chunk_state is not None and set(first.chunk_state) == {"1", "2", "3"}

    def exploding_caller(prompt: str) -> ModelResponse:
        raise AssertionError("must not call the model for an unchanged chunk")

    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, exploding_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True
    assert second.chunk_state == first.chunk_state


def test_chunk_resume_only_regenerates_the_chunk_whose_own_test_case_changed(tmp_path):
    """Changing one test_case row's condition text only changes the brief --
    and so only the model call -- for the chunk that row's own rule falls
    in; the other chunks must be reused untouched."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios_with_routines(conn, {"X": 3, "Y": 2})

    out_path = tmp_path / "FAKEMOD.md"
    first_caller = _counting_caller(_chunk_aware_caller("python", "pytest"))
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, first_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True
    assert first_caller.calls == 2  # X (3 scenarios) and Y (2 scenarios) pack as two chunks
    chunk1_before = (tmp_path / "FAKEMOD.chunk1.md").read_text(encoding="utf-8")

    # BR-004 is Y's first scenario, in chunk 2's range -- change its
    # condition so only that chunk's brief hash changes.
    conn.execute(
        "UPDATE test_case SET when_json=json_set(when_json, '$.condition', 'COND-4-CHANGED') "
        "WHERE scenario_name='FAKEMOD:BR-004'"
    )
    conn.commit()

    second_caller = _counting_caller(_chunk_aware_caller("python", "pytest"))
    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, second_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True
    assert second_caller.calls == 1, "only the chunk covering the changed scenario should re-render"
    assert (tmp_path / "FAKEMOD.chunk1.md").read_text(encoding="utf-8") == chunk1_before, (
        "chunk 1 (routine X, unaffected by the BR-004 change) must be reused untouched"
    )


def test_chunk_resume_falls_back_to_regenerating_a_chunk_whose_cached_file_is_gone(tmp_path):
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    (tmp_path / "FAKEMOD.chunk2.md").unlink()

    second_caller = _counting_caller(_chunk_aware_caller("python", "pytest"))
    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, second_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True
    assert second_caller.calls == 1
    assert (tmp_path / "FAKEMOD.chunk2.md").exists()


def test_chunk_resume_always_regenerates_a_previously_failed_chunk(tmp_path):
    """A chunk that failed last run, with a brief unchanged since, must
    never be treated as reusable just because its (invalid) file is still
    on disk and its hash still matches -- that would re-validate the same
    broken content and report the same failure forever, with no path back
    to a real retry. Only a chunk whose *prior* record was itself ok=True
    is eligible for reuse."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    good_caller = _chunk_aware_caller("python", "pytest")

    def flaky_caller(prompt: str) -> ModelResponse:
        if "BR-003" in prompt:
            return ModelResponse(text="not a valid document", input_tokens=1, output_tokens=1)
        return good_caller(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, flaky_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is False
    assert first.chunk_state["2"]["ok"] is False, "chunk 2 (covering BR-003) must be recorded as failed"

    # Second run: chunk 2's brief is unchanged (same test_case content), but
    # this time the caller can actually produce a valid response for it --
    # a chunk recorded as failed must always get a fresh model call, never
    # be silently reused/re-validated into the same stale failure.
    second_caller = _counting_caller(good_caller)
    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, second_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True, second.problems
    assert second_caller.calls == 1, "the previously-failed chunk must be regenerated, not reused"
    assert second.chunk_state["2"]["ok"] is True


def test_chunk_reuse_treats_a_stale_sidecar_as_a_cache_miss(tmp_path, monkeypatch):
    """Issue #195 review follow-up: `_test_chunk_reuse_ok` must not reuse a
    chunk verbatim just because `validate_test_doc` came back `ok=True` --
    `ok=True` alone can mean the sidecar staleness guard tolerated a stale
    on-disk sidecar by falling back to scanning the document body, not that
    the sidecar source itself is still accurate. Reusing it verbatim would
    leave that stale `.py`/`.nsp` file (with its now-wrong BR-nnn comments)
    on disk forever, since nothing would ever call
    `write_test_doc_with_sidecar` again to refresh it. `sidecar_stale` in
    the result must therefore force a cache miss (a normal re-render),
    which -- once it validates -- rewrites the sidecar the usual way."""
    from mfdoc import testbatch

    chunk_path = tmp_path / "FAKEMOD.chunk1.md"
    chunk_path.write_text("irrelevant -- validate_test_doc is faked below", encoding="utf-8")
    prior_chunks = {"1": {"ok": True, "brief_sha256": "same-hash"}}

    monkeypatch.setattr(
        testbatch, "validate_test_doc",
        lambda conn, path, _text=None, _prior_fingerprint=None, _render_time=False, _fingerprint_cache=None, _valid_scenarios=None: {"ok": True, "sidecar_stale": True, "problems": []},
    )
    assert testbatch._test_chunk_reuse_ok(None, prior_chunks, 1, "same-hash", chunk_path, "python") is False

    monkeypatch.setattr(
        testbatch, "validate_test_doc",
        lambda conn, path, _text=None, _prior_fingerprint=None, _render_time=False, _fingerprint_cache=None, _valid_scenarios=None: {"ok": True, "sidecar_stale": False, "problems": []},
    )
    assert testbatch._test_chunk_reuse_ok(None, prior_chunks, 1, "same-hash", chunk_path, "python") is True


def test_chunk_reuse_ok_passes_its_fingerprint_cache_through_to_validation(tmp_path, monkeypatch):
    """Copilot review follow-up on issue #195's fix: `_test_chunk_reuse_ok`'s
    own `_fingerprint_cache`/`_valid_scenarios` parameters must actually
    reach `validate_test_doc`/`_readonly_validate_test_doc` -- earlier
    rounds added each parameter to both validators and to the chunk loop/
    dry-run callers that create the shared dict/provider, but
    `_test_chunk_reuse_ok` itself dropped them on the floor instead of
    forwarding them, leaving the intended cache-hit path just as expensive
    as no cache at all."""
    from mfdoc import testbatch

    chunk_path = tmp_path / "FAKEMOD.chunk1.md"
    chunk_path.write_text("irrelevant -- validate_test_doc is faked below", encoding="utf-8")
    prior_chunks = {"1": {"ok": True, "brief_sha256": "same-hash"}}
    received = []

    def fake_validate(conn, path, _text=None, _prior_fingerprint=None, _render_time=False, _fingerprint_cache=None, _valid_scenarios=None):
        received.append((_fingerprint_cache, _valid_scenarios))
        return {"ok": True, "sidecar_stale": False, "problems": []}

    monkeypatch.setattr(testbatch, "validate_test_doc", fake_validate)
    sentinel: dict = {"marker": "shared"}
    scenarios_sentinel = object()
    testbatch._test_chunk_reuse_ok(
        None, prior_chunks, 1, "same-hash", chunk_path, "python",
        _fingerprint_cache=sentinel, _valid_scenarios=scenarios_sentinel,
    )
    assert received == [(sentinel, scenarios_sentinel)]

    received.clear()
    monkeypatch.setattr(
        testbatch, "_readonly_validate_test_doc",
        lambda conn, path, _render_time=False, _prior_fingerprint=None, _fingerprint_cache=None,
        _valid_scenarios=None: (
            received.append((_fingerprint_cache, _valid_scenarios))
            or {"ok": True, "sidecar_stale": False, "problems": []}
        ),
    )
    testbatch._test_chunk_reuse_ok(
        None, prior_chunks, 1, "same-hash", chunk_path, "python", readonly=True,
        _fingerprint_cache=sentinel, _valid_scenarios=scenarios_sentinel,
    )
    assert received == [(sentinel, scenarios_sentinel)]


def test_chunk_render_surfaces_a_failed_sidecar_invalidation_as_a_chunk_failure(tmp_path, monkeypatch):
    """Copilot review follow-up on issue #195's fix: if
    `_invalidate_sidecar_if_range_changed` can't remove a wrong-range
    sidecar (a transient filesystem lock -- it now raises `OSError`
    instead of swallowing it), the chunked render loop must report that as
    this chunk's own failure rather than pressing on into a render that
    would fail validation on every retry anyway, less legibly."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 5)

    def exploding_invalidate(chunk_path, language, expected_ids):
        raise OSError("simulated: file is locked by another process")

    monkeypatch.setattr(testbatch, "_invalidate_sidecar_if_range_changed", exploding_invalidate)

    caller = _chunk_aware_caller("python", "pytest")
    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert result.ok is False
    assert any("could not invalidate stale chunk sidecar" in p for p in result.problems)


def test_chunked_render_surfaces_a_failed_stale_index_sidecar_removal(tmp_path, monkeypatch):
    """Copilot review follow-up on issue #195's fix: if the leftover
    single-document sidecar a member leaves behind after growing past the
    chunking threshold can't be removed (a transient filesystem lock), a
    later standalone `mfdoc test-validate` sweep would still find it at
    the exact path the index document's own sidecar lookup resolves to,
    and could cross-check the index's aggregate manifest against that
    unrelated leftover content -- the same false missing-id failure this
    whole mechanism exists to prevent. Must be recorded as a problem
    (`ok=False`) instead of reported as a clean render while the stale
    sidecar remains."""
    import sqlite3
    from pathlib import Path

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    # A leftover single-document sidecar at the exact path sidecar_path_for
    # would resolve for the index document itself.
    stale_sidecar = out_path.with_suffix(".py")
    stale_sidecar.write_text("# leftover from a prior single-document render\n", encoding="utf-8")

    real_replace = Path.replace

    def exploding_replace(self, target):
        if self == stale_sidecar:
            raise OSError("simulated: file is locked by another process")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", exploding_replace)

    caller = _chunk_aware_caller("python", "pytest")
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert result.ok is False
    assert any("could not remove stale single-document sidecar" in p for p in result.problems)
    assert stale_sidecar.exists()


def test_chunked_render_index_write_failure_does_not_leave_a_truncated_index(tmp_path, monkeypatch):
    """Copilot review follow-up: the chunk index document is written to a
    `.tmp` sibling and replaced atomically, not a direct `write_text` onto
    `out_path` -- if that write failed straight into `out_path` (disk-full
    mid-write), the `finally` block that restores `index_sidecar_backup`
    (since `index_written` is only set *after* a successful write) would
    still fire, but `out_path` itself would be left holding a partial/
    truncated new index instead of its old, still-intact content --
    pairing the restored old sidecar with a broken new index rather than
    the fully-old pair this rollback is meant to leave behind."""
    import sqlite3
    from pathlib import Path

    import pytest

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    stale_sidecar = out_path.with_suffix(".py")
    old_stale_sidecar_text = "# leftover from a prior single-document render\n"
    stale_sidecar.write_text(old_stale_sidecar_text, encoding="utf-8")

    real_write_text = Path.write_text

    def exploding_write_text(self, content, *args, **kwargs):
        if self.name == "FAKEMOD.md.tmp":
            raise OSError("simulated: disk full while writing the chunk index")
        return real_write_text(self, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", exploding_write_text)

    caller = _chunk_aware_caller("python", "pytest")
    with pytest.raises(OSError):
        testbatch.generate_member_test_doc(
            conn, "FAKEMOD", "python", "pytest", out_path, caller,
            "writing rules text", "template text", max_scenarios_per_call=2,
        )

    assert not out_path.exists(), "out_path must never be touched by a failed index write"
    assert stale_sidecar.exists()
    assert stale_sidecar.read_text(encoding="utf-8") == old_stale_sidecar_text, (
        "the old single-document sidecar must be restored, not left as a partial replacement"
    )
    assert not out_path.with_name("FAKEMOD.md.tmp").exists(), "no leftover index temp file should remain"


def test_single_doc_render_leaves_old_chunk_output_untouched_when_the_new_render_fails(tmp_path):
    """Copilot review follow-up: a member shrinking back under the
    chunking threshold must not have its old `.chunk<N>` files (its own
    *last successful* output) pruned before the new single-document
    render has even been attempted -- if that new render then fails (a
    model timeout, exhausted invalid retries), the old chunk files are
    exactly what a caller reading `out_path` (still the old chunked
    index, untouched, still naming them) needs -- destroying them first
    would leave nothing behind at all instead of a stale-but-real result."""
    from mfdoc import testbatch

    conn = _sqlite_conn()
    _seed_fakemod_scenarios(conn, 4)

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True
    assert first.chunked is True
    chunk1_path = tmp_path / "FAKEMOD.chunk1.md"
    chunk1_sidecar = tmp_path / "FAKEMOD.chunk1.py"
    assert chunk1_path.exists() and chunk1_sidecar.exists()
    old_chunk1_text = chunk1_path.read_text(encoding="utf-8")
    old_index_text = out_path.read_text(encoding="utf-8")

    def exploding_caller(prompt: str) -> ModelResponse:
        raise RuntimeError("simulated: model call always fails")

    # Threshold raised past 4 -- this member now goes through the
    # single-document path, which would prune the (now-orphaned) chunk
    # files; the render itself fails outright.
    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, exploding_caller,
        "writing rules text", "template text", max_scenarios_per_call=10,
    )
    assert second.ok is False
    assert chunk1_path.exists(), "the old chunk document must survive a failed replacement render"
    assert chunk1_sidecar.exists(), "the old chunk sidecar must survive a failed replacement render too"
    assert chunk1_path.read_text(encoding="utf-8") == old_chunk1_text
    assert out_path.read_text(encoding="utf-8") == old_index_text, (
        "out_path itself (the old chunked index) must be untouched by a render that never wrote to it"
    )


def test_single_doc_render_surfaces_a_failed_orphaned_chunk_file_removal(tmp_path, monkeypatch):
    """Copilot review follow-up on issue #195's fix: a member that shrinks
    back under the chunking threshold leaves its old `.chunk<N>.md` files
    orphaned -- `generate_member_test_doc`'s single-document path cleans
    those up, but if a removal fails (a transient filesystem lock),
    `validate_tests_tree` still walks and validates the leftover chunk
    document independently, where it can fail on stale content nothing
    renders into any more. Must be recorded as a problem (`ok=False`)
    instead of reported as a clean render while the orphan remains."""
    from pathlib import Path

    from mfdoc import testbatch

    conn = _sqlite_conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    rc1 = _insert_rc(conn, 1, 10)
    from mfdoc.db import insert
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "COND", "citation": "[[FAKEMOD:10]]"}',
        then_json='{"citation": "[[FAKEMOD:10]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:10", confidence="verified",
    )
    conn.commit()

    out_path = tmp_path / "FAKEMOD.md"
    orphaned_chunk = tmp_path / "FAKEMOD.chunk1.md"
    orphaned_chunk.write_text("# leftover from a prior chunked render\n", encoding="utf-8")

    real_unlink = Path.unlink

    def exploding_unlink(self, *args, **kwargs):
        if self == orphaned_chunk:
            raise OSError("simulated: file is locked by another process")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", exploding_unlink)

    caller = _chunk_aware_caller("python", "pytest")
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, caller,
        "writing rules text", "template text", max_scenarios_per_call=10,
    )
    assert result.ok is False
    assert any("could not remove orphaned chunk file" in p for p in result.problems)
    assert orphaned_chunk.exists()


def test_chunk_reuse_ok_rejects_a_split_chunk_whose_sidecar_is_missing(tmp_path, monkeypatch):
    """Copilot review follow-up: a *split* chunk document (one that already
    references its sidecar by name in a `## Scenarios covered` manifest,
    its actual test source moved out) whose sidecar has since gone
    missing on disk must never be treated as reusable, even when
    `validate_test_doc` itself reports `ok=True` (it falls back to
    scanning the document's own body, and the manifest's ids still
    resolve against `test_case` regardless of whether the sidecar file
    exists) -- reusing it would carry the missing-source problem forward
    indefinitely, since nothing would ever call `write_test_doc_with_
    sidecar` again to recreate it."""
    from mfdoc import testbatch

    chunk_path = tmp_path / "FAKEMOD.chunk1.md"
    chunk_path.write_text(
        "---\nsources: [\"FAKEMOD\"]\nlanguage: python\n---\n\n"
        "# FAKEMOD tests\n\n"
        "See [`FAKEMOD.chunk1.py`](./FAKEMOD.chunk1.py) for the generated test source.\n\n"
        "## Scenarios covered\n\n- FAKEMOD:BR-001\n",
        encoding="utf-8",
    )
    # No FAKEMOD.chunk1.py written -- the sidecar this document's own
    # manifest points at is missing.
    prior_chunks = {"1": {"ok": True, "brief_sha256": "same-hash"}}

    monkeypatch.setattr(
        testbatch, "validate_test_doc",
        lambda conn, path, _text=None, _prior_fingerprint=None, _render_time=False, _fingerprint_cache=None, _valid_scenarios=None: {"ok": True, "sidecar_stale": False, "problems": []},
    )
    assert testbatch._test_chunk_reuse_ok(None, prior_chunks, 1, "same-hash", chunk_path, "python") is False


def test_chunk_reuse_ok_still_allows_an_embedded_fence_chunk_with_no_sidecar(tmp_path, monkeypatch):
    """The other half: a document that was *never* split (still embeds its
    own code fence, or has no `MEMBER:BR-nnn` references to split out at
    all) legitimately has no sidecar -- that must not be confused with
    the missing-artifact case above."""
    from mfdoc import testbatch

    chunk_path = tmp_path / "FAKEMOD.chunk1.md"
    chunk_path.write_text(
        "---\nsources: [\"FAKEMOD\"]\nlanguage: python\n---\n\n"
        "# FAKEMOD tests\n\n"
        "```python\ndef test_one():\n    # FAKEMOD:BR-001\n    ...\n```\n",
        encoding="utf-8",
    )
    prior_chunks = {"1": {"ok": True, "brief_sha256": "same-hash"}}

    monkeypatch.setattr(
        testbatch, "validate_test_doc",
        lambda conn, path, _text=None, _prior_fingerprint=None, _render_time=False, _fingerprint_cache=None, _valid_scenarios=None: {"ok": True, "sidecar_stale": False, "problems": []},
    )
    assert testbatch._test_chunk_reuse_ok(None, prior_chunks, 1, "same-hash", chunk_path, "python") is True


def test_chunk_reuse_forces_a_re_render_for_a_legacy_chunk_with_no_fingerprint(tmp_path):
    """Copilot review follow-up on issue #195: a *legacy* chunk file (no
    `test_case_fingerprint` anywhere -- written before this fix existed)
    whose own cached `brief_sha256` still matches (its own routine's
    content is unaffected) must still be forced through a real re-render
    if the corpus has shifted elsewhere in the member -- `brief_hash`
    alone can't see a `rule_candidate` change outside this chunk's own
    range, and without `_test_chunk_reuse_ok` passing `_render_time=True`
    into its revalidation, a legacy chunk's old ids remaining a
    coincidental subset of the current valid set would read as "still
    current" via the id-overlap fallback, reusing a chunk that can never
    earn a real fingerprint because nothing ever re-renders it."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 40, 'irrelevant')")
    rc1 = insert(
        conn, "rule_candidate", member_id=1, line_no=10, construct="IF",
        condition="COND-1", raw="IF COND-1",
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    # A legacy chunk file: no test_case_fingerprint, written directly
    # (never through write_test_doc_with_sidecar).
    chunk_path = tmp_path / "FAKEMOD.chunk1.md"
    chunk_path.write_text(
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

See [`FAKEMOD.chunk1.py`](./FAKEMOD.chunk1.py) for the generated test source.

## Scenarios covered

- FAKEMOD:BR-001
""",
        encoding="utf-8",
    )
    (tmp_path / "FAKEMOD.chunk1.py").write_text(
        "def test_one():\n    # FAKEMOD:BR-001\n    ...\n", encoding="utf-8",
    )

    # A rule inserted elsewhere in the member -- this chunk's own brief
    # text is unaffected, so its cached brief_sha256 would still match.
    rc2 = insert(
        conn, "rule_candidate", member_id=1, line_no=20, construct="IF",
        condition="COND-2", raw="IF COND-2",
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc2,
        scenario_name="FAKEMOD:BR-002",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    prior_chunks = {"1": {"ok": True, "brief_sha256": "unchanged-hash"}}
    assert testbatch._test_chunk_reuse_ok(conn, prior_chunks, 1, "unchanged-hash", chunk_path, "python") is False, (
        "a legacy chunk with no fingerprint must not be reused once the "
        "corpus has shifted, even if its own cached brief hash matches"
    )


def test_chunk_resume_regenerates_a_reused_chunk_that_fails_revalidation(tmp_path):
    """The other half of the reuse guard: a chunk the prior run recorded as
    clean, but whose cached file no longer validates (its content was
    tampered with, or validate_test_doc's own rules tightened since), must
    fall back to a normal regeneration rather than being reported as a
    terminal failure."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA
    from mfdoc.validate import validate_test_doc

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 5)

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True, first.problems
    (tmp_path / "FAKEMOD.chunk2.md").write_text("not a valid document", encoding="utf-8")

    second_caller = _counting_caller(_chunk_aware_caller("python", "pytest"))
    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, second_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True, second.problems
    assert second_caller.calls == 1, "the chunk failing re-validation must be regenerated"
    assert validate_test_doc(conn, tmp_path / "FAKEMOD.chunk2.md")["ok"]


def test_run_test_batch_persists_chunk_state_and_reuses_it_across_calls(tmp_path):
    """End-to-end through run_test_batch's own state file, not just the
    lower-level generate_member_test_doc -- a second run against unchanged
    facts must make zero model calls for the already-chunked member's
    chunks."""
    import json
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 5)

    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"
    first_caller = _counting_caller(_chunk_aware_caller("python", "pytest"))
    summary1 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, first_caller,
        "writing rules text", "template text", state_path=state_path, max_scenarios_per_call=2,
    )
    assert summary1.failed == 0
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    state_key = "natural::FAKEMOD::python::pytest"
    assert "chunks" in saved[state_key]
    assert first_caller.calls > 0

    second_caller = _counting_caller(_chunk_aware_caller("python", "pytest"))
    summary2 = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, second_caller,
        "writing rules text", "template text", state_path=state_path, max_scenarios_per_call=2,
    )
    assert summary2.failed == 0
    assert second_caller.calls == 0, "every chunk should be reused, not re-rendered"


def test_run_test_batch_chunks_a_large_member_and_still_batches_small_ones(tmp_path):
    """End-to-end through run_test_batch (the `mfdoc test-batch` path, not
    just single-member test-gen): a large member routes to the serial
    chunked path while a normal-sized member in the same call still goes
    through the concurrent thread-pool path -- and neither one breaks the
    other (this is also the regression guard for the sqlite3
    same-thread requirement: generate_member_test_doc touches `conn`, so a
    large member must never run inside the thread pool)."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 5)

    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'SMALLMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (2, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=2, kind="unit", scenario_name="SMALLMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[SMALLMOD:1]]"}',
        then_json='{"citation": "[[SMALLMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="SMALLMOD:1", confidence="verified",
    )
    conn.commit()

    def caller(prompt: str) -> ModelResponse:
        if "SMALLMOD" in prompt:
            text = _valid_test_doc_text("python", "pytest").replace("FAKEMOD", "SMALLMOD")
            return ModelResponse(text=text, input_tokens=1, output_tokens=2)
        return _chunk_aware_caller("python", "pytest")(prompt)

    out_dir = tmp_path / "out"
    summary = testbatch.run_test_batch(
        conn, ["FAKEMOD", "SMALLMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
    )

    assert summary.failed == 0, [r.problems for r in summary.results if not r.ok]
    assert summary.ok == 2

    fakemod_path = out_dir / "natural" / "python" / "pytest" / "FAKEMOD.md"
    smallmod_path = out_dir / "natural" / "python" / "pytest" / "SMALLMOD.md"
    assert "doc_type: generated_test" in fakemod_path.read_text(encoding="utf-8")
    assert (out_dir / "natural" / "python" / "pytest" / "FAKEMOD.chunk3.md").exists()
    assert smallmod_path.exists()
    assert not (out_dir / "natural" / "python" / "pytest" / "SMALLMOD.chunk1.md").exists()


# --- Issue #87: a caller exception must not crash the whole run, and state
# must be saved incrementally so a retry only redoes what didn't finish. ---

def _seed_two_members(conn):
    from mfdoc.db import insert

    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'GOODMOD', 'natural')")
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (2, 'BADMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (2, 1, 'irrelevant')")
    for member_id, name in ((1, "GOODMOD"), (2, "BADMOD")):
        insert(
            conn, "test_case", member_id=member_id, kind="unit", scenario_name=f"{name}:BR-001",
            given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
            when_json=f'{{"construct": "IF", "condition": "X", "citation": "[[{name}:1]]"}}',
            then_json=f'{{"citation": "[[{name}:1]]", "source_excerpt": []}}',
            status="characterization", citation=f"{name}:1", confidence="verified",
        )
    conn.commit()


def test_run_test_batch_survives_a_caller_exception_partway_through(tmp_path):
    """A single member whose model call raises (simulating a `claude -p`
    timeout -- see claude_cli_caller.py's subprocess.TimeoutExpired ->
    RuntimeError) must not crash run_test_batch or discard the other
    member's already-finished, successful result. The failing member is
    reported as an ordinary ok=False DocResult, not an unhandled exception,
    and both members' state is persisted -- proving state is saved
    incrementally, not only in one final call after every member finishes
    (a crash this test doesn't simulate, e.g. a hard kill, would otherwise
    lose it)."""
    import json
    import sqlite3

    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_two_members(conn)

    def caller(prompt: str) -> ModelResponse:
        if "BADMOD" in prompt:
            raise RuntimeError("`claude -p` timed out after 600s")
        return ModelResponse(text=_valid_test_doc_text("python", "pytest").replace("FAKEMOD", "GOODMOD"),
                              input_tokens=1, output_tokens=2)

    from mfdoc import testbatch

    out_dir = tmp_path / "out"
    state_path = tmp_path / "state.json"

    summary = testbatch.run_test_batch(
        conn, ["GOODMOD", "BADMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
    )

    assert summary.ok == 1
    assert summary.failed == 1
    good = next(r for r in summary.results if r.member == "GOODMOD")
    bad = next(r for r in summary.results if r.member == "BADMOD")
    assert good.ok is True
    assert bad.ok is False
    assert any("RuntimeError" in p for p in bad.problems)

    # State was persisted for *both* members, not just the one that
    # finished cleanly -- proving the exception was caught and recorded,
    # rather than propagating past the point where state gets saved.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    good_key = "natural::GOODMOD::python::pytest"
    bad_key = "natural::BADMOD::python::pytest"
    assert state[good_key]["ok"] is True
    assert state[bad_key]["ok"] is False

    # A retry (same caller, still raising for BADMOD) must not need to
    # redo GOODMOD -- it's already recorded as ok in state, with a real
    # output file on disk, so it's skipped this time.
    retry_summary = testbatch.run_test_batch(
        conn, ["GOODMOD", "BADMOD"], "python", "pytest", out_dir, caller,
        "writing rules text", "template text", state_path=state_path,
    )
    retry_good = next(r for r in retry_summary.results if r.member == "GOODMOD")
    retry_bad = next(r for r in retry_summary.results if r.member == "BADMOD")
    assert retry_good.skipped is True, "GOODMOD's prior success must be reused, not regenerated"
    assert retry_bad.ok is False


def test_run_test_batch_retry_call_exception_is_reported_not_raised(tmp_path):
    """The retry call (after a first attempt fails validate_test_doc, not
    the initial call) raising must be caught the same way -- the member
    still reports ok=False with the exception text in its problems, and
    the batch as a whole keeps running."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_two_members(conn)
    # GOODMOD is seeded but not selected below -- only BADMOD is under test here.

    calls = {"n": 0}

    def caller(prompt: str) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(text="not a valid document at all", input_tokens=1, output_tokens=1)
        raise RuntimeError("second call also failed")

    summary = testbatch.run_test_batch(
        conn, ["BADMOD"], "python", "pytest", tmp_path / "out", caller,
        "writing rules text", "template text",
    )
    assert summary.failed == 1
    bad = summary.results[0]
    assert bad.ok is False
    assert bad.attempts == 2
    assert any("RuntimeError" in p for p in bad.problems)


def test_run_test_batch_pool_loop_strips_a_leftover_fingerprint_from_a_failed_candidate(tmp_path):
    """Copilot review follow-up (round 43): the same gap
    `test_generate_member_test_doc_strips_a_leftover_fingerprint_from_a_
    failed_candidate` closes for `generate_member_test_doc`'s own give-up
    path exists in `run_test_batch`'s separate pooled-dispatch
    implementation for ordinary (non-chunked) members too -- a candidate
    that never validates never reaches `write_test_doc_with_sidecar`'s
    own stamping, so a `test_case_fingerprint` it happens to carry
    (echoed/hallucinated) is left behind untouched unless this loop's own
    give-up path strips it too."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_two_members(conn)

    def always_failing_caller(prompt: str) -> ModelResponse:
        # Deliberately missing language/framework front matter -- fails
        # validate_test_doc on every attempt.
        text = """---
title: "BADMOD -- generated tests"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-09-02"
review_status: draft
confidence_summary:
  verified: 0
sources: ["BADMOD"]
test_case_fingerprint: "hallucinated-leftover-value"
---

# BADMOD tests

```python
def test_x():
    # BADMOD:BR-001 [[BADMOD:1]]
    pass
```
"""
        return ModelResponse(text=text, input_tokens=1, output_tokens=1)

    out_dir = tmp_path / "out"
    summary = testbatch.run_test_batch(
        conn, ["BADMOD"], "python", "pytest", out_dir, always_failing_caller,
        "writing rules text", "template text",
    )
    assert summary.failed == 1
    out_path = out_dir / "natural" / "python" / "pytest" / "BADMOD.md"
    written = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint" not in written, (
        "a leftover fingerprint on a failed, never-validated candidate must be "
        "stripped before this loop's own give-up path leaves it on disk"
    )
    from mfdoc.testbatch import _prior_fingerprint_for
    assert _prior_fingerprint_for(out_path) is None


def test_run_test_batch_initial_call_exception_is_retried_and_can_still_succeed(tmp_path):
    """The pooled path's *first* model call raising must be retried once,
    the same second chance a bad-but-successful response already gets --
    not an immediate ok=False. A caller that raises only on its first
    invocation for a member and returns a valid document on the retry must
    end up ok=True with attempts=2."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_two_members(conn)

    calls = {"n": 0}

    def caller(prompt: str) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient failure on the first call")
        return ModelResponse(text=_valid_test_doc_text("python", "pytest").replace("FAKEMOD", "GOODMOD"),
                              input_tokens=1, output_tokens=2)

    summary = testbatch.run_test_batch(
        conn, ["GOODMOD"], "python", "pytest", tmp_path / "out", caller,
        "writing rules text", "template text",
    )
    assert summary.ok == 1
    good = summary.results[0]
    assert good.ok is True
    assert good.attempts == 2
    assert calls["n"] == 2, "the retry must actually call the model again, not give up after the first exception"


def test_run_test_batch_near_miss_uncited_assertion_gets_a_targeted_patch_not_a_full_retry(tmp_path):
    """Issue #188 code review: `run_test_batch`'s own non-chunked pool loop
    -- the ordinary `mfdoc test-batch` path for every member at or below
    max_scenarios_per_call, which is *not* routed through
    `_generate_test_doc_from_brief` -- must get the same near-miss/
    targeted-patch treatment as the direct-call and chunked paths, or the
    command this issue's own evidence was measured against never actually
    benefits from the fix."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    near_miss_text = _valid_test_doc_text("python", "pytest").replace(
        "Covers the module as a whole [[FAKEMOD:1]].",
        "Covers the module as a whole [[FAKEMOD:1]]. The system also "
        "validates the account balance before posting.",
    )
    patched_text = _valid_test_doc_text("python", "pytest").replace(
        "Covers the module as a whole [[FAKEMOD:1]].",
        "Covers the module as a whole [[FAKEMOD:1]]. The system also "
        "validates the account balance before posting [[FAKEMOD:1]].",
    )
    calls = {"n": 0}

    def caller(prompt: str) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(text=near_miss_text, input_tokens=10, output_tokens=20)
        return ModelResponse(text=patched_text, input_tokens=5, output_tokens=8)

    summary = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", tmp_path / "out", caller,
        "writing rules text", "template text", concurrency=1,
    )
    result = summary.results[0]
    assert result.ok, result.problems
    assert calls["n"] == 2
    assert result.attempts == 1  # the patch call doesn't count as a full-retry attempt


def test_run_test_batch_near_miss_patch_failure_falls_back_to_full_retry(tmp_path):
    """Same non-chunked pool loop: when the targeted patch attempt itself
    doesn't resolve validation, the member must still fall back to the
    existing full-retry path (attempts=2) rather than being reported
    ok=False after only the one patch attempt."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    near_miss_text = _valid_test_doc_text("python", "pytest").replace(
        "Covers the module as a whole [[FAKEMOD:1]].",
        "Covers the module as a whole [[FAKEMOD:1]]. The system also "
        "validates the account balance before posting.",
    )
    fixed_text = _valid_test_doc_text("python", "pytest").replace(
        "Covers the module as a whole [[FAKEMOD:1]].",
        "Covers the module as a whole [[FAKEMOD:1]]. The system also "
        "validates the account balance before posting [[FAKEMOD:1]].",
    )
    calls = {"n": 0}

    def caller(prompt: str) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(text=near_miss_text, input_tokens=10, output_tokens=20)
        if calls["n"] == 2:
            # Patch attempt: model fails to actually fix it.
            return ModelResponse(text=near_miss_text, input_tokens=5, output_tokens=8)
        # Full retry: succeeds.
        return ModelResponse(text=fixed_text, input_tokens=1, output_tokens=1)

    summary = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", tmp_path / "out", caller,
        "writing rules text", "template text", concurrency=1,
    )
    result = summary.results[0]
    assert result.ok, result.problems
    assert calls["n"] == 3
    assert result.attempts == 2


def test_generate_member_test_doc_reports_caller_exception_without_crashing(tmp_path):
    """The single-member (non-batch) path -- used by chunked members inside
    run_test_batch's serial loop, and by `mfdoc test-gen` directly -- must
    give the same treatment: a caller exception on every attempt is a
    normal ok=False result, not a propagated exception."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    def always_raises(prompt: str) -> ModelResponse:
        raise TimeoutError("simulated `claude -p` timeout")

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, always_raises,
        "writing rules text", "template text",
    )
    assert result.ok is False
    assert result.attempts == 2
    assert any("TimeoutError" in p for p in result.problems)
    assert not out_path.exists(), "no attempt ever wrote a response -- nothing should be on disk"


def test_generate_member_test_doc_preserves_validation_problems_when_retry_raises(tmp_path):
    """A prior attempt's real validation problems must survive a later
    attempt raising instead of being silently replaced by only the
    exception text -- the failure mode Copilot's PR #101 review flagged at
    testbatch.py:193: `_generate_test_doc_from_brief`'s exception handler
    used to overwrite `problems` outright, discarding whatever the first
    (validation-failing) attempt already recorded. The final DocResult must
    report both."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    calls = {"n": 0}

    def caller(prompt: str) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(text="not a valid document at all", input_tokens=1, output_tokens=1)
        raise RuntimeError("second call also failed")

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, caller,
        "writing rules text", "template text",
    )
    assert result.ok is False
    assert result.attempts == 2
    assert any("RuntimeError" in p for p in result.problems), (
        "the retry's own exception must be reported"
    )
    assert len(result.problems) >= 2, (
        "the first attempt's real validation problem(s) must survive alongside "
        "the retry's exception, not be overwritten by it"
    )


def test_generate_member_test_doc_strips_a_leftover_fingerprint_from_a_failed_candidate(tmp_path):
    """Copilot review follow-up (round 43): every attempt this render
    makes returns a candidate that never validates (missing `language`/
    `framework` front matter, deterministically invalid on every retry)
    but *does* carry its own `test_case_fingerprint` -- a model can echo/
    hallucinate this field even in a candidate that fails validation for
    an unrelated reason. `write_test_doc_with_sidecar` never runs for a
    candidate that never validates, so nothing else would ever strip it;
    left in place, a *later* invocation's `_prior_fingerprint_for` would
    read this leftover value back as if it were a genuinely-stamped
    prior, risking exactly the stale-sidecar-looks-authoritative deadlock
    this whole mechanism exists to prevent. The give-up path itself must
    strip it before leaving the failed candidate on disk."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    def always_failing_caller(prompt: str) -> ModelResponse:
        # Deliberately missing language/framework front matter -- fails
        # validate_test_doc every attempt, regardless of retry content.
        text = """---
title: "FAKEMOD -- generated tests"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-09-02"
review_status: draft
confidence_summary:
  verified: 0
sources: ["FAKEMOD"]
test_case_fingerprint: "hallucinated-leftover-value"
---

# FAKEMOD tests

```python
def test_x():
    # FAKEMOD:BR-001 [[FAKEMOD:1]]
    pass
```
"""
        return ModelResponse(text=text, input_tokens=1, output_tokens=1)

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, always_failing_caller,
        "writing rules text", "template text",
    )
    assert result.ok is False
    written = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint" not in written, (
        "a leftover fingerprint on a failed, never-validated candidate must be "
        "stripped before the give-up path leaves it on disk"
    )
    from mfdoc.testbatch import _prior_fingerprint_for
    assert _prior_fingerprint_for(out_path) is None, (
        "a later invocation reading this same path must find nothing to recover, "
        "not this failed candidate's own untrusted leftover value"
    )


def test_generate_member_test_doc_does_not_strip_an_untouched_prior_successful_document(tmp_path):
    """Copilot review follow-up (round 44): the give-up cleanup added above
    must not run at all when this invocation never actually wrote a
    candidate to `out_path` -- if every model call raises before a
    single response comes back, `out_path` is left exactly as it was
    *before* this call, which for a re-render of an already-successful
    member is that prior render's own known-good document, still
    carrying a legitimately-stamped, trustworthy `test_case_fingerprint`.
    Stripping it here would mutate a known-good document over a failure
    that was never its own, forcing every future validation back onto
    the weaker id-overlap fallback."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    out_path = tmp_path / "FAKEMOD.md"
    successful_result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _valid_test_doc_caller("python", "pytest"),
        "writing rules text", "template text",
    )
    assert successful_result.ok is True
    original_text = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint:" in original_text, "sanity check: the prior render stamped a real fingerprint"

    def exploding_caller(prompt: str) -> ModelResponse:
        raise RuntimeError("simulated: model call always fails")

    second_result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, exploding_caller,
        "writing rules text", "template text",
    )
    assert second_result.ok is False
    assert out_path.read_text(encoding="utf-8") == original_text, (
        "an untouched, already-successful document must survive a re-render where every "
        "model call raises -- its legitimately-stamped fingerprint must not be stripped"
    )


def test_generate_member_test_doc_preserves_exception_when_retry_fails_validation(tmp_path):
    """The reverse ordering of the case above -- an attempt raises first,
    and the retry then comes back with a response that still fails
    validation. The unguarded `problems = result["problems"]` line Copilot's
    PR #101 review flagged at testbatch.py:193-197 (commit 41adaba) would
    drop the first attempt's exception text here, since only the exception
    branch appended rather than the validation-failure branch. The final
    DocResult must report both."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    calls = {"n": 0}

    def caller(prompt: str) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first call timed out")
        return ModelResponse(text="not a valid document at all", input_tokens=1, output_tokens=1)

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, caller,
        "writing rules text", "template text",
    )
    assert result.ok is False
    assert result.attempts == 2
    assert any("RuntimeError" in p for p in result.problems), (
        "the first attempt's exception must survive the retry's validation failure"
    )
    assert len(result.problems) >= 2, (
        "the retry's real validation problem(s) must be reported alongside "
        "the first attempt's exception, not overwrite it"
    )


def test_run_test_batch_chunked_member_caller_exception_does_not_abort_other_chunks(tmp_path):
    """One chunk's model call raising must fail only that chunk (and thus
    the member overall), not crash the run or prevent the other chunks
    from rendering and validating on their own -- mirrors the existing
    validation-failure guarantee (test_generate_member_test_doc_reports_
    failure_when_one_chunk_fails) but for a raised exception instead of an
    invalid response."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    good_caller = _chunk_aware_caller("python", "pytest")

    def flaky_caller(prompt: str) -> ModelResponse:
        if "BR-003" in prompt:
            raise RuntimeError("simulated timeout for this chunk")
        return good_caller(prompt)

    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, flaky_caller,
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert result.ok is False
    assert any("chunk 2" in p for p in result.problems)

    assert (tmp_path / "FAKEMOD.chunk1.md").exists()
    from mfdoc.validate import validate_test_doc
    assert validate_test_doc(conn, tmp_path / "FAKEMOD.chunk1.md")["ok"]

    index_text = out_path.read_text(encoding="utf-8")
    assert "FAKEMOD:BR-001" in index_text and "FAKEMOD:BR-002" in index_text
    assert "FAKEMOD:BR-003" not in index_text, "a chunk whose call raised must not be claimed as covered"


def test_checkpoint_never_leaves_a_truncated_state_file_on_a_mid_write_crash(tmp_path):
    """testbatch._checkpoint persists via batch._save_state (imported, not
    reimplemented) -- this proves that atomic-write guarantee actually
    reaches testbatch's own checkpoint path, not just batch.run_batch's.
    Before issue #78's fix, a process killed mid-write here would leave a
    truncated/invalid JSON file in place of the last good checkpoint, and
    the next run's _load_state would raise JSONDecodeError, defeating the
    whole point of checkpointing after every member. The atomic rename is
    made to raise partway through a second checkpoint, and the prior good
    state file is asserted to survive untouched, with no leftover temp
    file."""
    import json
    import os as os_mod

    import pytest

    from mfdoc.testbatch import _checkpoint

    state_path = tmp_path / "state.json"
    good_state = {"natural::GOODMOD::python::pytest": {"ok": True, "attempts": 1}}
    _checkpoint(dict(good_state), state_path, "sig-1")
    assert json.loads(state_path.read_text(encoding="utf-8"))["natural::GOODMOD::python::pytest"]["ok"] is True

    real_replace = os_mod.replace

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash before the atomic rename completes")

    os_mod.replace = boom
    try:
        with pytest.raises(RuntimeError):
            _checkpoint(
                {"natural::GOODMOD::python::pytest": {"ok": True, "attempts": 1},
                 "natural::BADMOD::python::pytest": {"ok": False, "attempts": 2}},
                state_path, "sig-2",
            )
    finally:
        os_mod.replace = real_replace

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == good_state | {"_corpus_sha256": "sig-1"}
    leftover_tmp_files = [p for p in tmp_path.iterdir() if p.name != "state.json"]
    assert leftover_tmp_files == [], f"temp file(s) leaked: {leftover_tmp_files}"


def test_checkpoint_is_member_granular_not_chunk_granular_for_chunked_members(tmp_path):
    """_checkpoint's docstring says persistence happens per member
    (single-call or chunked), not per chunk within a chunked member -- a
    chunked member's chunks are all rendered by one
    generate_member_test_doc call before run_test_batch checkpoints that
    member's result (mirrors batch.run_batch's identical member-wide
    trade-off for module docs). Proven here by counting how many times
    state actually hits disk while running one chunked member with 4
    scenarios (2 chunks at threshold=2): exactly one save for that member,
    not one per chunk."""
    import sqlite3
    from unittest.mock import patch

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios(conn, 4)

    save_calls = []
    real_save_state = testbatch._save_state

    def counting_save_state(state_path, state):
        save_calls.append(dict(state))
        return real_save_state(state_path, state)

    with patch("mfdoc.testbatch._save_state", side_effect=counting_save_state):
        summary = testbatch.run_test_batch(
            conn, ["FAKEMOD"], "python", "pytest", tmp_path / "out",
            _chunk_aware_caller("python", "pytest"),
            "writing rules text", "template text",
            state_path=tmp_path / "state.json", max_scenarios_per_call=2,
        )

    assert summary.ok == 1
    # One save for FAKEMOD's own checkpoint, plus run_test_batch's final
    # unconditional save at the very end -- never one per chunk (which
    # would be 2+ here, one per chunk, on top of those).
    assert len(save_calls) == 2, (
        f"expected exactly 2 _save_state calls (per-member checkpoint + final), got {len(save_calls)}"
    )


# --- Issue #89: namespace test-batch state/output per project, so two
# project configs pointing at the same working directory don't silently
# share (and clobber) one another's resume-state/output tree. ---

def test_project_namespace_derives_from_system_then_project_then_default():
    from mfdoc.cli import _project_namespace

    assert _project_namespace({"system": "MOM"}) == "mom"
    assert _project_namespace({"project": "Mill Order Management"}) == "mill-order-management"
    # `system` wins over `project` when both are set -- the shorter code is
    # the more filesystem-friendly of the two, and matches which one a
    # human would actually use to tell two configs apart at a glance.
    assert _project_namespace({"system": "MOM", "project": "Mill Order Management"}) == "mom"
    assert _project_namespace({}) == "default"
    # Non-alphanumeric characters (spaces, slashes, punctuation) collapse to
    # single hyphens rather than propagating into a path segment that could
    # be misread as introducing a subdirectory or trailing/leading noise.
    assert _project_namespace({"system": "  Weird/Chars!! "}) == "weird-chars"


def test_project_namespace_never_produces_a_path_traversal_segment():
    """A `system`/`project` value that slugifies to "." or ".." must not be
    used as-is: as a single path segment (no "/" survives the sub above),
    either one still means "this directory" / "the parent directory" to
    the filesystem, which would silently point test-batch's output/state
    at an unrelated location instead of a real per-project subfolder --
    the opposite of what this namespacing exists to guarantee."""
    from mfdoc.cli import _project_namespace

    assert _project_namespace({"system": "."}) == "default"
    assert _project_namespace({"system": ".."}) == "default"
    assert _project_namespace({"system": "..."}) not in (".", "..")
    assert _project_namespace({"system": "  ..  "}) == "default"
    # A leading/trailing run of dots around otherwise-real content must be
    # trimmed, not just rejected wholesale -- ".." isn't the *only*
    # substring that must never survive to become the whole segment.
    assert _project_namespace({"system": "..sysa.."}) == "sysa"


def _seed_fakemod_for_cli(config_path: Path) -> None:
    """Minimal index_db content for one config: a single FAKEMOD member
    with one derived test_case row -- just enough for `cmd_test_batch` to
    find a batchable member and actually write output/state, without
    running the full ingest/derive/test-plan pipeline."""
    import sqlite3

    from mfdoc.db import SCHEMA, insert

    cfg = cli.load_config(str(config_path))
    db_path = config_path.parent / cfg["index_db"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()
    conn.close()


def test_two_project_configs_sharing_a_working_directory_get_separate_state_and_output(tmp_path):
    """Two project.yml files in the very same directory, differing only in
    `system` (and, per the existing index_db convention, their own
    index_db path) -- neither --out nor --state given for either run, so
    both rely entirely on the computed defaults. Must produce two distinct
    output trees and two distinct resume-state files: a `rm -f` intended to
    force a clean retry for one project must never touch the other's."""
    import json

    import yaml

    project_dir = tmp_path / "shared-workdir"
    shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
    shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")
    base_cfg = yaml.safe_load((REPO_ROOT / "project.yml").read_text(encoding="utf-8"))

    def write_config(name: str, system: str) -> Path:
        cfg = dict(base_cfg)
        cfg["system"] = system
        cfg["sources"] = []
        cfg["index_db"] = f".mfdoc/{name}.db"
        path = project_dir / f"{name}.yml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return path

    config_a = write_config("project-a", "SYSA")
    config_b = write_config("project-b", "SYSB")
    _seed_fakemod_for_cli(config_a)
    _seed_fakemod_for_cli(config_b)

    def run(config_path: Path) -> int:
        args = SimpleNamespace(
            config=str(config_path), out=None, members=None,
            language="python", framework="pytest", template=None, model=None,
            caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
            concurrency=1, state=None, matrix=False,
        )
        return cli.cmd_test_batch(args)

    run(config_a)
    run(config_b)

    ns_a, ns_b = "sysa", "sysb"
    assert ns_a != ns_b

    out_a = project_dir / "tests_generated" / ns_a / "natural" / "python" / "pytest" / "FAKEMOD.md"
    out_b = project_dir / "tests_generated" / ns_b / "natural" / "python" / "pytest" / "FAKEMOD.md"
    assert out_a.exists()
    assert out_b.exists()
    assert out_a != out_b

    state_a = project_dir / ".mfdoc" / f"{ns_a}-test-batch-state.json"
    state_b = project_dir / ".mfdoc" / f"{ns_b}-test-batch-state.json"
    assert state_a.exists()
    assert state_b.exists()
    saved_a = json.loads(state_a.read_text(encoding="utf-8"))
    saved_b = json.loads(state_b.read_text(encoding="utf-8"))
    assert "natural::FAKEMOD::python::pytest" in saved_a
    assert "natural::FAKEMOD::python::pytest" in saved_b

    # The crux of the bug this closes: deleting one project's resume-state
    # file (a common "force a clean retry" move) must never remove or
    # otherwise disturb the other project's.
    state_a.unlink()
    assert state_b.exists()
    assert json.loads(state_b.read_text(encoding="utf-8")) == saved_b


def test_test_batch_explicit_out_and_state_are_never_namespaced(tmp_path):
    """An explicit --out/--state is a full override, like index_db always
    is -- it must be used exactly as given, with no project-namespace
    subdirectory/prefix inserted, regardless of --config's system/project."""
    shutil.copytree(REPO_ROOT / "reference", tmp_path / "reference")
    shutil.copytree(REPO_ROOT / "templates", tmp_path / "templates")
    import yaml

    base_cfg = yaml.safe_load((REPO_ROOT / "project.yml").read_text(encoding="utf-8"))
    base_cfg["system"] = "SYSA"
    base_cfg["sources"] = []
    base_cfg["index_db"] = ".mfdoc/index.db"
    config_path = tmp_path / "project.yml"
    config_path.write_text(yaml.safe_dump(base_cfg), encoding="utf-8")
    _seed_fakemod_for_cli(config_path)

    args = SimpleNamespace(
        config=str(config_path), out=str(tmp_path / "explicit-out"), members=None,
        language="python", framework="pytest", template=None, model=None,
        caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
        concurrency=1, state=str(tmp_path / "explicit-state.json"), matrix=False,
    )
    cli.cmd_test_batch(args)

    assert (tmp_path / "explicit-out" / "natural" / "python" / "pytest" / "FAKEMOD.md").exists()
    assert (tmp_path / "explicit-state.json").exists()
    assert not (tmp_path / "explicit-out" / "sysa").exists()


def test_testgen_default_out_dir_nests_under_docs_root_when_set():
    """See #142: a project.yml that sets docs_root should get its generated
    tests nested under it, not siblinged off in an unrelated top-level
    tests_generated/ tree."""
    from mfdoc.cli import _testgen_default_out_dir

    assert Path(_testgen_default_out_dir({"docs_root": "docs/functional"})) == Path("docs/functional/tests")


def test_testgen_default_out_dir_falls_back_to_tests_generated_without_docs_root():
    """A config that never sets docs_root at all must keep today's bare
    top-level literal, unchanged -- this is a default-only change, not a
    breaking one for existing setups."""
    from mfdoc.cli import _testgen_default_out_dir

    assert _testgen_default_out_dir({}) == "tests_generated"
    assert _testgen_default_out_dir({"docs_root": None}) == "tests_generated"
    assert _testgen_default_out_dir({"docs_root": ""}) == "tests_generated"


# A malformed docs_root (a non-string, non-None value) is rejected by
# config_validate.py's OPTION_SPECS at load_config time, before any command
# reaches _testgen_default_out_dir -- see
# test_config_validate.py::test_docs_root_must_be_a_string. This function
# itself does no type-checking of its own, the same "validate once, trust
# everywhere after" convention dispatch_field_from_options/
# mode_field_from_options already follow, so there's no equivalent case to
# test at this layer.


def _write_config_for_default_out_dir_case(
    project_dir: Path, *, set_docs_root: bool, out_dir: str | None,
) -> Path:
    import yaml

    shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
    shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")
    base_cfg = yaml.safe_load((REPO_ROOT / "project.yml").read_text(encoding="utf-8"))
    base_cfg["system"] = "SYSA"
    base_cfg["sources"] = []
    base_cfg["index_db"] = ".mfdoc/index.db"
    if set_docs_root:
        base_cfg["docs_root"] = "docs/functional"
    else:
        base_cfg.pop("docs_root", None)
    if out_dir is None:
        base_cfg["options"]["testgen"].pop("out_dir", None)
    else:
        base_cfg["options"]["testgen"]["out_dir"] = out_dir
    config_path = project_dir / "project.yml"
    config_path.write_text(yaml.safe_dump(base_cfg), encoding="utf-8")
    _seed_fakemod_for_cli(config_path)
    return config_path


def _run_test_batch_default(config_path: Path, monkeypatch) -> int:
    def valid_caller(_args):
        return lambda _prompt: ModelResponse(
            text=_valid_test_doc_text("python", "pytest"),
            input_tokens=0,
            output_tokens=0,
        )

    monkeypatch.setattr(cli, "_build_model_caller", valid_caller)
    args = SimpleNamespace(
        config=str(config_path), out=None, members=None,
        language="python", framework="pytest", template=None, model=None,
        caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
        concurrency=1, state=None, matrix=False,
    )
    return cli.cmd_test_batch(args)


def test_test_batch_nests_default_out_dir_under_docs_root(tmp_path, monkeypatch):
    """docs_root set, options.testgen.out_dir NOT set, no --out -- the
    default output tree must land under <docs_root>/tests instead of the
    disconnected top-level tests_generated/ (see #142)."""
    project_dir = tmp_path / "nested-docs-root"
    config_path = _write_config_for_default_out_dir_case(
        project_dir, set_docs_root=True, out_dir=None,
    )

    assert _run_test_batch_default(config_path, monkeypatch) == 0

    out = (project_dir / "docs" / "functional" / "tests" / "sysa"
           / "natural" / "python" / "pytest" / "FAKEMOD.md")
    assert out.exists()
    assert not (project_dir / "tests_generated").exists()


def test_test_batch_default_out_dir_stays_top_level_without_docs_root(tmp_path, monkeypatch):
    """docs_root unset entirely (and options.testgen.out_dir also unset), no
    --out -- must preserve today's bare top-level tests_generated/ default
    unchanged, so a config without docs_root sees no behavior change."""
    project_dir = tmp_path / "no-docs-root"
    config_path = _write_config_for_default_out_dir_case(
        project_dir, set_docs_root=False, out_dir=None,
    )

    assert _run_test_batch_default(config_path, monkeypatch) == 0

    out = (project_dir / "tests_generated" / "sysa"
           / "natural" / "python" / "pytest" / "FAKEMOD.md")
    assert out.exists()


def test_test_batch_explicit_out_dir_wins_over_docs_root_default(tmp_path, monkeypatch):
    """options.testgen.out_dir, when explicitly set, always wins over the
    docs_root-derived default -- exactly as an explicit out_dir already won
    over the old bare "tests_generated" default before this change."""
    project_dir = tmp_path / "explicit-out-dir-wins"
    config_path = _write_config_for_default_out_dir_case(
        project_dir, set_docs_root=True, out_dir="explicit-tests",
    )

    assert _run_test_batch_default(config_path, monkeypatch) == 0

    out = (project_dir / "explicit-tests" / "sysa"
           / "natural" / "python" / "pytest" / "FAKEMOD.md")
    assert out.exists()
    assert not (project_dir / "docs" / "functional" / "tests").exists()


# --- Issue #190: port batch.py's plan_batch/--dry-run resume preview
# (issue #160) to testbatch.py's own independent prior_chunks/brief_sha256
# resume state, mirroring tests/test_batch.py's test_plan_batch_* tests. ---

def _valid_test_doc_caller(language: str, framework: str):
    """A fake caller that returns a fully valid document citing exactly the
    `MEMBER:BR-nnn` scenario ids present in the prompt it was sent, for any
    member name -- unlike `_chunk_aware_caller` above (which only ever
    recognises `FAKEMOD:BR-...`), this is used against the `indexed_db`
    fixture's own bundled, invented example members (e.g. MMP0100 --
    see `examples/inputs/` and this repo's "never commit client-specific
    content" policy: these are this repo's own synthetic worked example,
    not derived from any real engagement)."""
    import re as _re

    ids_re = _re.compile(r"\b[A-Z][A-Z0-9_]*:BR-\d+\b")

    def caller(prompt: str) -> ModelResponse:
        ids = sorted(set(ids_re.findall(prompt)))
        fence_lines = "\n".join(
            f"def test_{i.split(':BR-')[-1]}():\n    # {i} [[{i.split(':BR-')[0]}:1]]\n    pass"
            for i in ids
        )
        text = f"""---
title: "generated tests"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-09-10"
review_status: draft
confidence_summary:
  verified: {len(ids)}
language: {language}
framework: {framework}
sources: {json.dumps(sorted({i.split(':BR-')[0] for i in ids}) or ["UNKNOWN"])}
---

# generated tests

Covers the module as a whole.

```python
{fence_lines}
```
"""
        return ModelResponse(text=text, input_tokens=1, output_tokens=2)
    return caller


def test_plan_test_batch_reports_a_member_with_no_prior_state_as_render(indexed_db, tmp_path):
    """No --state file at all -- the member is a fresh render, never
    chunked here (MMP0100's test_case count is under any reasonable
    default threshold)."""
    from mfdoc import testbatch

    testplan.run_all(indexed_db, member_name="MMP0100")

    plan = testbatch.plan_test_batch(indexed_db, ["MMP0100"], "python", "pytest", tmp_path / "out")
    assert plan.language == "python" and plan.framework == "pytest"
    assert plan.corpus_unchanged is False
    assert len(plan.members) == 1
    assert plan.members[0].status == "render"
    assert plan.members_render == 1 and plan.members_skip == 0 and plan.members_chunked == 0


def test_plan_test_batch_reports_skip_after_a_real_run_with_unchanged_facts(indexed_db, tmp_path):
    from mfdoc import testbatch

    testplan.run_all(indexed_db, member_name="MMP0100")
    members = ["MMP0100"]
    state_path = tmp_path / "state.json"
    caller = _valid_test_doc_caller("python", "pytest")
    first = testbatch.run_test_batch(
        indexed_db, members, "python", "pytest", tmp_path / "out", caller,
        "writing rules text", "template text", state_path=state_path,
    )
    assert first.ok == 1

    plan = testbatch.plan_test_batch(
        indexed_db, members, "python", "pytest", tmp_path / "out", state_path=state_path,
    )
    assert plan.corpus_unchanged is True
    assert plan.members[0].status == "skip"
    assert plan.members_skip == 1 and plan.members_render == 0


def test_plan_test_batch_matches_generate_member_test_doc_chunk_reuse(tmp_path):
    """The core promise of the dry-run preview: its chunk-level reuse count
    for a chunked member must match what a real generate_member_test_doc
    call would actually do -- computed via the same _test_chunk_reuse_ok,
    not a second, potentially-drifting copy of the reuse rule. Also proves
    the "fresh vs. stale prior_chunks state" cases the dry-run exists to
    distinguish: a fresh prior_chunks (from the run immediately before)
    reports every chunk reusable; a stale one (one row changed since) reports
    exactly the affected chunk as needing to render."""
    import json
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios_with_routines(conn, {"X": 3, "Y": 2})

    out_dir = tmp_path / "out"
    subdir = testbatch._output_subdir(conn, "FAKEMOD")
    out_path = out_dir / subdir / "python" / "pytest" / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True
    assert first.chunked is True

    state_key = f"{subdir.as_posix()}::FAKEMOD::python::pytest"
    state = {state_key: {"ok": True, "chunks": first.chunk_state}}
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    # Fresh prior_chunks state (nothing changed since it was recorded):
    # every chunk must report reusable.
    fresh_plan = testbatch.plan_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, state_path=state_path,
        max_scenarios_per_call=2,
    )
    assert fresh_plan.members[0].status == "chunked"
    assert fresh_plan.members[0].chunk_count == 2
    assert fresh_plan.members[0].chunks_reusable == 2
    assert fresh_plan.members[0].chunks_to_render == 0

    # BR-004 is Y's first scenario, in chunk 2's range -- change its
    # condition (same perturbation as
    # test_chunk_resume_only_regenerates_the_chunk_whose_own_test_case_changed)
    # so the recorded prior_chunks state is now stale for chunk 2 only.
    conn.execute(
        "UPDATE test_case SET when_json=json_set(when_json, '$.condition', 'COND-4-CHANGED') "
        "WHERE scenario_name='FAKEMOD:BR-004'"
    )
    conn.commit()

    stale_plan = testbatch.plan_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, state_path=state_path,
        max_scenarios_per_call=2,
    )
    assert stale_plan.members[0].status == "chunked"
    assert stale_plan.members[0].chunk_count == 2
    assert stale_plan.members[0].chunks_reusable == 1
    assert stale_plan.members[0].chunks_to_render == 1

    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
        prior_chunks=first.chunk_state,
    )
    actually_reused = sum(
        1 for i in range(1, 3)
        if second.chunk_state[str(i)]["brief_sha256"] == first.chunk_state[str(i)]["brief_sha256"]
    )
    assert actually_reused == stale_plan.members[0].chunks_reusable


def test_test_batch_command_dry_run_reports_a_plan_and_makes_no_model_calls(
    cli_args, indexed_db, tmp_path, capsys, monkeypatch,
):
    """--dry-run must never reach _build_model_caller (no --model/--provider/
    API key needed) and must print a plan, not run a real test-batch --
    mirrors test_cli_batch.py's identical guard for `mfdoc batch --dry-run`."""
    _with_reference_and_templates(cli_args, tmp_path)
    testplan.run_all(indexed_db, member_name="MMP0100")

    def exploding_build_caller(args):
        raise AssertionError("--dry-run must never build a real model caller")

    monkeypatch.setattr(cli, "_build_model_caller", exploding_build_caller)

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members=None,
        language="python", framework="pytest", template=None, matrix=False,
        concurrency=1, state="", dry_run=True,
    )
    rc = cli.cmd_test_batch(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "corpus signature:" in out
    assert "MMP0100" in out
    assert not (tmp_path / "out").exists(), "--dry-run must not write any output"


def test_test_batch_command_dry_run_exits_2_for_a_missing_template_single_target(
    cli_args, indexed_db, tmp_path, monkeypatch,
):
    """Issue #190 review: --dry-run must reflect a runnable invocation, not
    just report RENDER/SKIP for a target that would actually fail to run --
    a missing template must exit the same way (2, single target) the real
    (non-dry-run) loop already does, not silently report a plan."""
    _with_reference_and_templates(cli_args, tmp_path)
    testplan.run_all(indexed_db, member_name="MMP0100")

    def exploding_build_caller(args):
        raise AssertionError("--dry-run must never build a real model caller")

    monkeypatch.setattr(cli, "_build_model_caller", exploding_build_caller)

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members=None,
        language="cobol", framework="nonexistent", template=None, matrix=False,
        concurrency=1, state="", dry_run=True,
    )
    rc = cli.cmd_test_batch(args)
    assert rc == 2


def test_plan_test_batch_chunk_reuse_check_does_not_mutate_doc_claim(tmp_path):
    """Issue #190 review: `plan_test_batch`'s chunk-reuse check must be a
    true dry-run -- `_test_chunk_reuse_ok`'s revalidation of a cached chunk
    calls `validate_test_doc`, which (via `validate.validate_doc`) deletes
    and reinserts that path's `doc_claim` rows and commits as a side
    effect. A plan run must leave that table exactly as it found it, even
    though every chunk here is cache-reusable and gets revalidated."""
    import sqlite3

    from mfdoc import testbatch
    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_fakemod_scenarios_with_routines(conn, {"X": 3, "Y": 2})

    out_dir = tmp_path / "out"
    subdir = testbatch._output_subdir(conn, "FAKEMOD")
    out_path = out_dir / subdir / "python" / "pytest" / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True

    # A state file recording every chunk as ok, so plan_test_batch's
    # chunk-reuse check actually attempts a revalidation (rather than
    # short-circuiting on "no prior state") -- the case this bug affects.
    state_key = f"{subdir.as_posix()}::FAKEMOD::python::pytest"
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({state_key: {"ok": True, "chunks": first.chunk_state}}), encoding="utf-8",
    )

    before = conn.execute(
        "SELECT doc_path, claim_id, confidence, citation, member_name, line_from, line_to, "
        "valid, note FROM doc_claim ORDER BY doc_path, claim_id"
    ).fetchall()
    assert len(before) > 0, "the real chunked render must have populated doc_claim"

    testbatch.plan_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, state_path=state_path,
        max_scenarios_per_call=2,
    )

    after = conn.execute(
        "SELECT doc_path, claim_id, confidence, citation, member_name, line_from, line_to, "
        "valid, note FROM doc_claim ORDER BY doc_path, claim_id"
    ).fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before], (
        "plan_test_batch must not change doc_claim, even though its chunk-reuse "
        "check calls validate_test_doc under the hood"
    )


def test_invalidate_sidecar_if_range_changed_removes_a_wrong_range_sidecar(tmp_path):
    """Direct unit test of `_invalidate_sidecar_if_range_changed` (Copilot
    review, issue #195): a chunk's on-disk sidecar whose own BR-nnn ids no
    longer match this run's freshly-computed row range for that same chunk
    index must be removed -- so a subsequent render/validate of that chunk
    can't wrongly cross-check against content scoped to a range this index
    no longer covers."""
    from mfdoc import testbatch

    chunk_path = tmp_path / "FAKEMOD.chunk1.md"
    sidecar = tmp_path / "FAKEMOD.chunk1.py"
    sidecar.write_text(
        "def test_one():\n    # FAKEMOD:BR-001\n    ...\n"
        "def test_two():\n    # FAKEMOD:BR-002\n    ...\n",
        encoding="utf-8",
    )

    # Unchanged range: same ids, sidecar must survive.
    testbatch._invalidate_sidecar_if_range_changed(
        chunk_path, "python", {"FAKEMOD:BR-001", "FAKEMOD:BR-002"},
    )
    assert sidecar.exists()

    # Range shifted (this chunk index now only covers BR-001): sidecar must
    # be removed rather than left to be read as authoritative for the wrong
    # range.
    testbatch._invalidate_sidecar_if_range_changed(chunk_path, "python", {"FAKEMOD:BR-001"})
    assert not sidecar.exists()

    # No sidecar at all: a no-op, not an error.
    testbatch._invalidate_sidecar_if_range_changed(chunk_path, "python", {"FAKEMOD:BR-001"})


def test_invalidate_sidecar_if_range_changed_renames_not_deletes(tmp_path):
    """Copilot review follow-up on issue #195's fix: a wrong-range sidecar
    is renamed to a `.stale` sibling, not deleted outright, and the backup
    path is returned so the caller can restore it if the render that
    follows doesn't succeed -- a plain delete would leave the chunk
    document (if its own render never gets far enough to write anything
    at all) with no sidecar next to it whatsoever, which `validate_test_
    doc` treats as an embedded-fence document and can validate clean from
    a stale manifest alone."""
    from mfdoc import testbatch

    chunk_path = tmp_path / "FAKEMOD.chunk1.md"
    sidecar = tmp_path / "FAKEMOD.chunk1.py"
    sidecar.write_text(
        "def test_one():\n    # FAKEMOD:BR-001\n    ...\n"
        "def test_two():\n    # FAKEMOD:BR-002\n    ...\n",
        encoding="utf-8",
    )

    backup = testbatch._invalidate_sidecar_if_range_changed(chunk_path, "python", {"FAKEMOD:BR-001"})
    assert backup is not None
    assert not sidecar.exists()
    assert backup.exists()
    assert "BR-002" in backup.read_text(encoding="utf-8")


def test_chunk_boundary_shift_does_not_deadlock_on_a_stale_wrong_range_sidecar(tmp_path):
    """End-to-end regression for the chunk-boundary case Copilot review
    flagged on issue #195: lowering `max_scenarios_per_call` between runs
    (with the member's own `rule_candidate` ordering completely unchanged)
    moves which scenarios chunk index 1 covers, even though the member-wide
    `test_case_fingerprint` `write_test_doc_with_sidecar` stamped for the
    *old* chunk 1 still matches today's fingerprint exactly (nothing about
    `rule_candidate` itself changed). Before the fix, `validate_test_doc`
    would read that match as "the old sidecar is still authoritative" and
    cross-check the freshly-rendered (narrower) candidate against the old,
    wider-range sidecar -- reporting the scenario that moved to a different
    chunk as "missing from the manifest" and failing validation on every
    retry, since a failed validation never lets `write_test_doc_with_sidecar`
    refresh the stale sidecar. This must now succeed cleanly instead."""
    from mfdoc import testbatch

    conn = _sqlite_conn()
    _seed_fakemod_scenarios(conn, 4)

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True
    assert first.chunk_state is not None and set(first.chunk_state) == {"1", "2"}
    old_chunk1_sidecar_ids = {
        line.strip() for line in
        (tmp_path / "FAKEMOD.chunk1.py").read_text(encoding="utf-8").splitlines()
        if "FAKEMOD:BR" in line
    }
    assert any("BR-002" in i for i in old_chunk1_sidecar_ids), (
        "sanity check: chunk 1 originally covered BR-001 and BR-002"
    )

    # Shrink the threshold to 1 -- chunk boundaries move (4 single-scenario
    # chunks instead of 2 pairs) even though no rule_candidate row changed
    # at all, so the member-wide fingerprint is identical to the first run's.
    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=1,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True, second.problems
    assert (tmp_path / "FAKEMOD.chunk1.py").read_text(encoding="utf-8").count("FAKEMOD:BR-002") == 0, (
        "chunk 1's sidecar must now only cover its own (narrower) range"
    )


def test_chunk_boundary_shift_restores_the_old_sidecar_when_the_rerender_fails(tmp_path):
    """Copilot review follow-up: the same boundary-shift setup as above, but
    the re-render's own model call fails outright this time (every attempt
    raises, so _generate_test_doc_from_brief never gets far enough to
    write anything new to chunk_path at all). The old, wrong-range sidecar
    _invalidate_sidecar_if_range_changed moved aside must be restored, not
    left removed -- otherwise chunk_path (still its own unchanged, valid-
    looking prior content) would be paired with no sidecar whatsoever."""
    from mfdoc import testbatch

    conn = _sqlite_conn()
    _seed_fakemod_scenarios(conn, 4)

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True
    chunk1_sidecar = tmp_path / "FAKEMOD.chunk1.py"
    old_sidecar_text = chunk1_sidecar.read_text(encoding="utf-8")

    def exploding_caller(prompt: str) -> ModelResponse:
        raise RuntimeError("simulated: model call always fails")

    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, exploding_caller,
        "writing rules text", "template text", max_scenarios_per_call=1,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is False
    assert chunk1_sidecar.exists(), "the old sidecar must be restored, not left missing"
    assert chunk1_sidecar.read_text(encoding="utf-8") == old_sidecar_text
    assert not chunk1_sidecar.with_name(chunk1_sidecar.name + ".stale").exists(), (
        "the backup must not be left behind once restored"
    )


def test_chunk_boundary_shift_restores_both_the_old_document_and_sidecar_on_a_failed_render(tmp_path):
    """Copilot review follow-up (round 42): unlike the "exploding caller"
    test above (every model call raises -- chunk_path is never touched
    at all by the failed re-render), a re-render that gets a response on
    every attempt but never validates still leaves that last, invalid
    candidate written to `chunk_path` -- `_generate_test_doc_from_brief`'s
    own retry loop writes each attempt's raw text to `out_path` *before*
    validating it. Restoring only the old (correctly-scoped) sidecar in
    that case, as an earlier version of this fix did, would pair it with
    the new, failing candidate -- the exact mismatched pair this whole
    invalidate-before-render mechanism exists to prevent, just appearing
    one step later than the fingerprint check alone can see. Both
    `chunk_path` and its sidecar must be restored together."""
    from mfdoc import testbatch

    conn = _sqlite_conn()
    _seed_fakemod_scenarios(conn, 4)

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True
    chunk1_path = tmp_path / "FAKEMOD.chunk1.md"
    chunk1_sidecar = tmp_path / "FAKEMOD.chunk1.py"
    old_chunk1_text = chunk1_path.read_text(encoding="utf-8")
    old_sidecar_text = chunk1_sidecar.read_text(encoding="utf-8")
    assert "BR-002" in old_sidecar_text, "sanity check: chunk 1 originally covered BR-001 and BR-002"

    good_caller = _chunk_aware_caller("python", "pytest")

    def flaky_caller(prompt: str) -> ModelResponse:
        # The re-render's new chunk 1 (max_scenarios_per_call=1 below)
        # covers only BR-001 -- its boundary has shifted from the
        # original chunk 1's {BR-001, BR-002}, triggering the range-
        # changed sidecar invalidation this fix guards. Every attempt
        # gets a response, but this one never validates (no exception).
        if "BR-001" in prompt and "BR-002" not in prompt:
            return ModelResponse(text="not a valid document", input_tokens=1, output_tokens=1)
        return good_caller(prompt)

    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, flaky_caller,
        "writing rules text", "template text", max_scenarios_per_call=1,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is False
    assert chunk1_path.exists(), "the old chunk document must be restored, not left with a failing candidate"
    assert chunk1_path.read_text(encoding="utf-8") == old_chunk1_text
    assert chunk1_sidecar.exists(), "the old sidecar must be restored, not left missing"
    assert chunk1_sidecar.read_text(encoding="utf-8") == old_sidecar_text
    assert not chunk1_path.with_name(chunk1_path.name + ".stale").exists(), (
        "the document backup must not be left behind once restored"
    )
    assert not chunk1_sidecar.with_name(chunk1_sidecar.name + ".stale").exists(), (
        "the sidecar backup must not be left behind once restored"
    )


def test_chunk_boundary_shift_discards_the_old_sidecar_when_the_rerender_writes_no_sidecar(tmp_path):
    """Copilot review follow-up: `result.ok` alone doesn't prove a fresh
    sidecar now exists -- `write_test_doc_with_sidecar` silently returns
    without writing one when the validated candidate's own code fence
    has no `MEMBER:BR-nnn` references at all (an edge case, but a real
    one: `validate_test_doc` has nothing to flag as invalid in that
    shape either). The backup must be *discarded* in this case, not
    restored: this render genuinely, correctly validated with no
    references at all, and restoring the old, unrelated backup would
    pair that legitimately-refless accepted document with a stale
    sidecar a later validation would then wrongly flag as a real
    mismatch -- an accepted render's own manifest and its sidecar must
    end up consistent with *each other*, not artificially reunited with
    whatever used to be there before."""
    from mfdoc import testbatch

    conn = _sqlite_conn()
    _seed_fakemod_scenarios(conn, 4)

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True
    chunk1_sidecar = tmp_path / "FAKEMOD.chunk1.py"
    old_sidecar_text = chunk1_sidecar.read_text(encoding="utf-8")

    def no_br_refs_caller(prompt: str) -> ModelResponse:
        text = """---
title: "FAKEMOD — generated tests"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-09-02"
review_status: draft
confidence_summary:
  verified: 0
language: python
framework: pytest
sources: ["FAKEMOD"]
---

# FAKEMOD tests

Covers the module as a whole [[FAKEMOD:1]].

```python
def test_placeholder():
    pass
```
"""
        return ModelResponse(text=text, input_tokens=1, output_tokens=2)

    second = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, no_br_refs_caller,
        "writing rules text", "template text", max_scenarios_per_call=1,
        prior_chunks=first.chunk_state,
    )
    assert second.ok is True, second.problems
    assert not chunk1_sidecar.exists(), (
        "no sidecar should be left at all -- the accepted document genuinely has no BR references"
    )
    assert not chunk1_sidecar.with_name(chunk1_sidecar.name + ".stale").exists(), (
        "the backup must be discarded, not left behind"
    )


def test_chunk_render_restores_the_old_sidecar_if_write_test_doc_with_sidecar_itself_raises(
    tmp_path, monkeypatch,
):
    """Copilot review follow-up: `_generate_test_doc_from_brief` can itself
    raise -- `write_test_doc_with_sidecar`'s own temp-write/replace
    failure propagates uncaught rather than being swallowed into a
    `DocResult` -- which must not skip the backup cleanup entirely and
    strand it at `.stale` with nothing at the real sidecar path either."""
    import pytest

    from mfdoc import testbatch

    conn = _sqlite_conn()
    _seed_fakemod_scenarios(conn, 4)

    out_path = tmp_path / "FAKEMOD.md"
    first = testbatch.generate_member_test_doc(
        conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
        "writing rules text", "template text", max_scenarios_per_call=2,
    )
    assert first.ok is True
    chunk1_sidecar = tmp_path / "FAKEMOD.chunk1.py"
    old_sidecar_text = chunk1_sidecar.read_text(encoding="utf-8")

    real_write_text = Path.write_text

    def exploding_write_text(self, content, *args, **kwargs):
        if self.suffix == ".tmp" and ".chunk1." in self.name:
            raise OSError("simulated: disk full while splitting the sidecar")
        return real_write_text(self, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", exploding_write_text)

    with pytest.raises(OSError):
        testbatch.generate_member_test_doc(
            conn, "FAKEMOD", "python", "pytest", out_path, _chunk_aware_caller("python", "pytest"),
            "writing rules text", "template text", max_scenarios_per_call=1,
            prior_chunks=first.chunk_state,
        )

    assert chunk1_sidecar.exists(), "the old sidecar must be restored, not left missing"
    assert chunk1_sidecar.read_text(encoding="utf-8") == old_sidecar_text
    assert not chunk1_sidecar.with_name(chunk1_sidecar.name + ".stale").exists(), (
        "the backup must not be left behind once restored"
    )


def _sqlite_conn():
    import sqlite3

    from mfdoc.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def test_write_test_doc_with_sidecar_tolerates_non_mapping_front_matter(tmp_path):
    """Copilot review, issue #195: `split_frontmatter` can come back with a
    truthy scalar or list for syntactically-valid-but-non-mapping YAML
    between the `---` markers (e.g. a bare string with no `key:` at all) --
    `.get` on that raises `AttributeError` instead of just leaving the
    fingerprint unstamped. Must not crash; must still perform the sidecar
    split itself (the code fence is valid regardless of the front matter
    shape) and simply omit `test_case_fingerprint`."""
    from mfdoc import testbatch

    conn = _sqlite_conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.commit()

    # Front matter that parses as a bare YAML string, not a mapping.
    doc_text = (
        "---\n"
        "this is not a mapping, just a scalar string\n"
        "---\n\n"
        "# FAKEMOD tests\n\n"
        "```python\n"
        "def test_one():\n"
        "    # FAKEMOD:BR-001\n"
        "    ...\n"
        "```\n"
    )
    out_path = tmp_path / "FAKEMOD.md"
    result = testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")
    assert result is not None
    assert result.exists()
    written = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint" not in written
    assert "FAKEMOD:BR-001" in result.read_text(encoding="utf-8")


def test_write_test_doc_with_sidecar_leaves_both_files_untouched_if_doc_write_fails(tmp_path, monkeypatch):
    """Copilot review follow-up on issue #195's fix: if the sidecar content
    write succeeds but the document's own write then fails (disk-full, a
    permissions change mid-run), the *old* pair of files must remain
    exactly as they were -- not a freshly-written sidecar paired with the
    stale document, which `validate_test_doc` would cross-check as a
    genuine mismatch. Writing to `.tmp` siblings first means the failure
    this test simulates (the second temp-file write) never touches either
    final path at all."""
    import pytest

    from mfdoc import testbatch
    from mfdoc.db import insert

    conn = _sqlite_conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    rc1 = _insert_rc(conn, 1, 10)
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "COND", "citation": "[[FAKEMOD:10]]"}',
        then_json='{"citation": "[[FAKEMOD:10]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:10", confidence="verified",
    )
    conn.commit()

    doc_text = (
        "---\nsources: [\"FAKEMOD\"]\n---\n\n"
        "# FAKEMOD tests\n\n"
        "```python\ndef test_one():\n    # FAKEMOD:BR-001\n    ...\n```\n"
    )
    out_path = tmp_path / "FAKEMOD.md"
    old_text = "old document content, must survive untouched"
    out_path.write_text(old_text, encoding="utf-8")

    real_write_text = Path.write_text

    def exploding_write_text(self, content, *args, **kwargs):
        if self.name == "FAKEMOD.md.tmp":
            raise OSError("simulated: disk full")
        return real_write_text(self, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", exploding_write_text)

    with pytest.raises(OSError):
        testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")

    assert out_path.read_text(encoding="utf-8") == old_text, "the old document must be untouched"
    assert not (tmp_path / "FAKEMOD.py").exists(), "no sidecar should be left behind at its final path"


def test_write_test_doc_with_sidecar_rolls_back_the_sidecar_if_the_doc_replace_fails(tmp_path, monkeypatch):
    """Copilot review follow-up: both temp files can be fully written and
    the sidecar's own `replace` can succeed before the document's
    `replace` then fails (a transient filesystem error striking between
    the two directory-entry updates, not during content writing) --
    without a rollback, that leaves a *new* sidecar paired with the *old*
    document, the exact mismatched pair this whole mechanism exists to
    avoid. The old sidecar's own pre-existing bytes must be restored, not
    the new content left in place."""
    import pytest

    from mfdoc import testbatch
    from mfdoc.db import insert

    conn = _sqlite_conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    rc1 = _insert_rc(conn, 1, 10)
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "COND", "citation": "[[FAKEMOD:10]]"}',
        then_json='{"citation": "[[FAKEMOD:10]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:10", confidence="verified",
    )
    conn.commit()

    doc_text = (
        "---\nsources: [\"FAKEMOD\"]\n---\n\n"
        "# FAKEMOD tests\n\n"
        "```python\ndef test_one():\n    # FAKEMOD:BR-001\n    ...\n```\n"
    )
    out_path = tmp_path / "FAKEMOD.md"
    sidecar_path = tmp_path / "FAKEMOD.py"
    old_doc_text = "old document content, must survive untouched"
    old_sidecar_text = "old sidecar content, must be restored"
    out_path.write_text(old_doc_text, encoding="utf-8")
    sidecar_path.write_text(old_sidecar_text, encoding="utf-8")

    real_replace = Path.replace

    def exploding_replace(self, target):
        if self.name == "FAKEMOD.md.tmp":
            raise OSError("simulated: filesystem error between the two replaces")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", exploding_replace)

    with pytest.raises(OSError):
        testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")

    assert out_path.read_text(encoding="utf-8") == old_doc_text, "the old document must be untouched"
    assert sidecar_path.read_text(encoding="utf-8") == old_sidecar_text, (
        "the old sidecar must be restored, not left as the new (now-orphaned) content"
    )
    # Copilot review: the rollback itself is a temp-write-then-replace, not
    # a direct write_bytes onto sidecar_path -- no leftover rollback temp
    # file should remain either way.
    assert not sidecar_path.with_name(sidecar_path.name + ".rollback.tmp").exists()


def test_write_test_doc_with_sidecar_removes_the_sidecar_if_the_doc_replace_fails_and_none_existed(
    tmp_path, monkeypatch,
):
    """The other half of the rollback: when there was no pre-existing
    sidecar to restore (a brand-new document/sidecar pair), the failure
    must leave neither file behind, not a new sidecar with no document to
    pair it with."""
    import pytest

    from mfdoc import testbatch
    from mfdoc.db import insert

    conn = _sqlite_conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    rc1 = _insert_rc(conn, 1, 10)
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "COND", "citation": "[[FAKEMOD:10]]"}',
        then_json='{"citation": "[[FAKEMOD:10]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:10", confidence="verified",
    )
    conn.commit()

    doc_text = (
        "---\nsources: [\"FAKEMOD\"]\n---\n\n"
        "# FAKEMOD tests\n\n"
        "```python\ndef test_one():\n    # FAKEMOD:BR-001\n    ...\n```\n"
    )
    out_path = tmp_path / "FAKEMOD.md"
    sidecar_path = tmp_path / "FAKEMOD.py"
    # Neither final path exists yet -- a brand-new member's first render.

    real_replace = Path.replace

    def exploding_replace(self, target):
        if self.name == "FAKEMOD.md.tmp":
            raise OSError("simulated: filesystem error between the two replaces")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", exploding_replace)

    with pytest.raises(OSError):
        testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")

    assert not out_path.exists()
    assert not sidecar_path.exists(), "no orphaned sidecar should remain with no document to pair it with"
    assert not out_path.with_name("FAKEMOD.md.tmp").exists(), "no leftover .tmp document should remain"
    assert not sidecar_path.with_name("FAKEMOD.py.tmp").exists(), "no leftover .tmp sidecar should remain"


def test_write_test_doc_with_sidecar_cleans_up_tmp_files_when_the_first_write_fails(tmp_path, monkeypatch):
    """Copilot review follow-up: every failure path -- including the
    earliest one, a plain content write to one of the `.tmp` siblings --
    must not leave that `.tmp` file behind. A failed run repeated enough
    times would otherwise accumulate misleading generated-source/markdown
    artifacts in the output tree that no validation ever looks at."""
    import pytest

    from mfdoc import testbatch
    from mfdoc.db import insert

    conn = _sqlite_conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    rc1 = _insert_rc(conn, 1, 10)
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "COND", "citation": "[[FAKEMOD:10]]"}',
        then_json='{"citation": "[[FAKEMOD:10]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:10", confidence="verified",
    )
    conn.commit()

    doc_text = (
        "---\nsources: [\"FAKEMOD\"]\n---\n\n"
        "# FAKEMOD tests\n\n"
        "```python\ndef test_one():\n    # FAKEMOD:BR-001\n    ...\n```\n"
    )
    out_path = tmp_path / "FAKEMOD.md"

    real_write_text = Path.write_text

    def exploding_write_text(self, content, *args, **kwargs):
        if self.name == "FAKEMOD.py.tmp":
            raise OSError("simulated: disk full")
        return real_write_text(self, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", exploding_write_text)

    with pytest.raises(OSError):
        testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")

    assert not (tmp_path / "FAKEMOD.py.tmp").exists()
    assert not (tmp_path / "FAKEMOD.md.tmp").exists()


def test_write_test_doc_with_sidecar_writes_sidecar_and_doc_together(tmp_path):
    """The sidecar file and the rewritten `out_path` must be written as the
    last two steps, after every step that can still bail out early (a
    missing/malformed front matter shape) -- so a document that doesn't
    make it all the way through never ends up with a freshly-written
    sidecar paired with an `out_path` nobody rewrote to reference it."""
    from mfdoc import testbatch
    from mfdoc.db import insert

    conn = _sqlite_conn()
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    rc1 = _insert_rc(conn, 1, 10)
    # A matching test_case row -- member_test_case_aligned_with_rule_
    # candidate (Copilot review) requires test_case to already cover every
    # current rule_candidate id before write_test_doc_with_sidecar will
    # stamp a fingerprint; this fixture's own rule_candidate row (rc1)
    # must therefore have its corresponding scenario already derived,
    # exactly as a real `mfdoc test-plan` run would leave it.
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc1,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "COND", "citation": "[[FAKEMOD:10]]"}',
        then_json='{"citation": "[[FAKEMOD:10]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:10", confidence="verified",
    )
    conn.commit()

    doc_text = (
        "---\n"
        "sources: [\"FAKEMOD\"]\n"
        "---\n\n"
        "# FAKEMOD tests\n\n"
        "```python\n"
        "def test_one():\n"
        "    # FAKEMOD:BR-001\n"
        "    ...\n"
        "```\n"
    )
    out_path = tmp_path / "FAKEMOD.md"
    assert not out_path.exists()
    result = testbatch.write_test_doc_with_sidecar(conn, "FAKEMOD", out_path, doc_text, "python")
    assert result is not None
    assert result.exists()
    assert out_path.exists()
    written = out_path.read_text(encoding="utf-8")
    assert "test_case_fingerprint" in written
    assert "## Scenarios covered" in written


def _insert_rc(conn, member_id, line_no):
    from mfdoc.db import insert

    return insert(
        conn, "rule_candidate", member_id=member_id, line_no=line_no, construct="IF",
        condition="COND", raw="IF COND",
    )
