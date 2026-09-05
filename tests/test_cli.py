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


def test_cmd_validate_reports_omitted_statement_targets_without_failing(cli_args, capsys):
    """The advisory section must print if and only if there is something to
    report, and must never affect the exit code either way.

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
    res = validate_tree(conn, Path(docs))

    exit_code = cli.cmd_validate(args)
    captured = capsys.readouterr()

    assert exit_code == (0 if (
        res["invalid_citations"] == 0
        and res["documents_ok"] == res["documents"]
        and not res["completeness_problems"]
    ) else 1)
    if res["omitted_statement_targets"]:
        assert "advisory, does not fail validation" in captured.out
    else:
        assert "advisory, does not fail validation" not in captured.out
