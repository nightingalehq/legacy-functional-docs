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
    # to its own per-target path.
    python_out = (tmp_path / "out" / "natural" / "MILLPROD" / "python" / "pytest" / "MMP0100.md")
    natural_out = (tmp_path / "out" / "natural" / "MILLPROD" / "natural" / "natunit" / "MMP0100.md")
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
    assert "chunked" in single_path.read_text(encoding="utf-8")


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
