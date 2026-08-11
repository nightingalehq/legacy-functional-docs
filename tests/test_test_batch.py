"""End-to-end guard on `mfdoc test-gen`/`mfdoc test-batch`'s own config
wiring (cli.py), mirroring tests/test_cli_batch.py's approach: --caller
fake-echo returns the prompt itself as the response text, so the written
.md *is* the prompt the command actually built -- letting us assert on it
directly without a real model call.
"""

from __future__ import annotations

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
        gcp_project=None, gcp_region=None,
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
        concurrency=1, state="",
    )
    cli.cmd_test_batch(args)
    written = (tmp_path / "out" / "python" / "pytest" / "MMP0100.md").read_text(encoding="utf-8")
    assert "# Test brief: MMP0100" in written


def test_missing_template_exits_cleanly_rather_than_crashing(cli_args, tmp_path):
    project_dir = _with_reference_and_templates(cli_args, tmp_path)
    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out" / "X.md"),
        member="MMP0100", language="cobol", framework="nonexistent", template=None,
        model=None, caller="fake-echo", provider="anthropic",
        gcp_project=None, gcp_region=None,
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

```{language}
def test_x():
    pass
```
"""


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

    pytest_path = out_dir / "python" / "pytest" / "FAKEMOD.md"
    unittest_path = out_dir / "python" / "unittest" / "FAKEMOD.md"
    assert "framework: pytest" in pytest_path.read_text(encoding="utf-8")
    assert "framework: unittest" in unittest_path.read_text(encoding="utf-8")


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
