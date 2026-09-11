"""CLI-level smoke tests for commands whose behavior isn't fully covered by
calling the underlying validate.py/graph.py functions directly."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mfdoc import cli  # noqa: E402
from mfdoc.db import connect  # noqa: E402
from mfdoc.validate import validate_tree  # noqa: E402


def test_cmd_validate_reports_omitted_statement_targets_without_failing(indexed_db, cli_args, capsys):
    """The advisory section must print if and only if there is something to
    report, and must never affect the exit code either way.

    Depends on `indexed_db` (not just `cli_args`) so pytest resolves the
    session-scoped `derive_result` fixture chain as a real dependency of
    *this* test -- without it nothing runs `mfdoc ingest`/`derive` against
    `cli_args`'s config before `cmd_validate` executes, and this test only
    "passes" by accident when an earlier-collected test file happens to
    populate the same session-scoped index database first.

    Note: the real bundled fixtures under examples/outputs/docs do currently
    have genuine per-statement completeness findings (paraphrased citations
    that don't literally name their target, e.g. "order lines" for
    ORDLINE) -- confirmed by inspection, not a bug in the new check. So this
    test can't assert the advisory section is always empty; instead it
    derives the expectation from validate_tree's own result, which is the
    real invariant this task cares about.
    """
    docs = str(REPO_ROOT / "examples" / "outputs" / "docs")
    args = SimpleNamespace(config=cli_args.config, docs=docs)

    cfg = cli.load_config(args.config)
    conn = connect(Path(args.config).parent / cfg["index_db"])
    try:
        res = validate_tree(conn, Path(docs))
    finally:
        conn.close()

    exit_code = cli.cmd_validate(args)
    captured = capsys.readouterr()

    assert exit_code == (0 if (
        res["invalid_citations"] == 0
        and res["documents_ok"] == res["documents"]
        and not res["completeness_problems"]
        and not res["statement_coverage_problems"]
        and not res["artifact_problems"]
    ) else 1)
    if res["omitted_statement_targets"]:
        assert "advisory, does not fail validation" in captured.out
        assert f"{len(res['omitted_statement_targets'])} statement(s)" in captured.out
        assert res["omitted_statement_targets"][0] in captured.out
    else:
        assert "advisory, does not fail validation" not in captured.out


def test_sample_citations_accepts_api_timeout_flag(cli_args, tmp_path, capsys):
    """`sample-citations --judge llm` builds its ModelCaller through the same
    `_build_model_caller()` as batch/classify-rules/test-batch, so it should
    accept `--api-timeout` too rather than being the one subcommand where a
    hung LLM-judge call can't be bounded."""
    state_path = tmp_path / "citation-sample-state.json"
    argv = [
        "sample-citations", "--config", cli_args.config, "--judge", "report",
        "--state", str(state_path), "--api-timeout", "45",
    ]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "claim(s) sampled" in out


def test_clean_doc_strips_leaked_preamble_from_an_interactively_written_document(tmp_path, capsys):
    """Issue #197 part 2: `system-overview.md`, `interface-matrix.md`, and
    the other documents written directly by the interactive Claude Code
    path (per SKILL.md's "Write from the brief" step) never pass through
    `generate_module_doc`'s write site, so they never got the issue #150/
    #152 preamble strip. `mfdoc clean-doc --file PATH` is the equivalent
    protection, run explicitly against the file after writing it."""
    doc = tmp_path / "system-overview.md"
    doc.write_text(
        "I'll write the system overview from the brief now.\n\n"
        "---\ntitle: System overview\ngenerated_by: legacy-functional-docs 0.1.0\n---\n"
        "body text here\n",
        encoding="utf-8",
    )

    exit_code = cli.main(["clean-doc", "--file", str(doc)])

    assert exit_code == 0
    from mfdoc import __version__

    cleaned = doc.read_text(encoding="utf-8")
    assert cleaned.startswith("---\n")
    assert f"generated_by: legacy-functional-docs {__version__}" in cleaned
    assert "I'll write the system overview" not in cleaned
    assert "cleaned" in capsys.readouterr().out


def test_clean_doc_is_a_no_op_and_says_so_when_already_clean(tmp_path, capsys):
    doc = tmp_path / "gap-register.md"
    text = "---\ntitle: Gap register\n---\nbody\n"
    doc.write_text(text, encoding="utf-8")

    exit_code = cli.main(["clean-doc", "--file", str(doc)])

    assert exit_code == 0
    assert doc.read_text(encoding="utf-8") == text
    assert "already clean" in capsys.readouterr().out


def test_clean_doc_fails_loudly_instead_of_reporting_already_clean_when_uncleanable(
    tmp_path, capsys,
):
    """Copilot review catch on this PR: `_fix_generated_by_version` returns
    its input unchanged both when the document was already clean *and*
    when no rescuable front matter was found near the start at all (e.g. a
    preamble longer than `_PREAMBLE_SEARCH_WINDOW`, or no front matter
    whatsoever) -- those are very different outcomes for a human/agent
    running this command to see. Reporting "already clean" for the second
    case would be actively misleading: `mfdoc validate` will still reject
    the file exactly as before. clean-doc must tell those apart and fail
    (non-zero exit) on the second."""
    doc = tmp_path / "system-overview.md"
    doc.write_text("Sorry, I can't help with that.\n", encoding="utf-8")

    exit_code = cli.main(["clean-doc", "--file", str(doc)])

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "already clean" not in captured.out
    assert "front matter" in captured.err
