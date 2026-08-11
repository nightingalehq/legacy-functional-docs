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
    written = (tmp_path / "out" / "natural" / "MILLPROD" / "python" / "pytest" / "MMP0100.md").read_text(encoding="utf-8")
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
    # FAKEMOD:BR-001 [[FAKEMOD:1]]
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
    sidecar = write_test_doc_with_sidecar(out_path, doc_text, "cobol")
    assert sidecar is None
    assert out_path.read_text(encoding="utf-8") == doc_text
    assert not (tmp_path / "FAKEMOD.cobol").exists()


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
