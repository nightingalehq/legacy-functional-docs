"""End-to-end guard on `mfdoc batch`'s own config wiring (cli.py), as
distinct from tests/test_batch.py's direct calls into batch.run_batch.

Uses --caller fake-echo, which returns the prompt itself as the response
text -- so the written .md *is* the prompt mfdoc batch actually built,
letting us assert on it directly without a real model call.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

from mfdoc import cli

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_batch_command_wires_the_configured_lexicon_into_the_prompt(cli_args, tmp_path):
    """The whole point of issue 4.9: mfdoc batch is headless and has no
    other way to see options.narrative.lexicon -- it must reach the
    prompt through cli.py's own wiring, not just through module_brief's
    optional parameter that nothing calls with a real value."""
    # cmd_batch reads reference/writing-rules.md and templates/module.md
    # relative to --config's directory; the session's tmp project.yml
    # doesn't have those alongside it, so copy them in for this test.
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members="MMP0100",
        model="claude-sonnet-4-5", concurrency=1, state="", caller="fake-echo",
    )
    # fake-echo writes the prompt itself back as the "document", which
    # naturally fails validate_doc (it's a prompt, not a real doc) -- that's
    # fine, exit code isn't the point here. What matters is what actually
    # went into the prompt mfdoc batch built.
    cli.cmd_batch(args)
    written = (tmp_path / "out" / "natural" / "MILLPROD" / "MMP0100.md").read_text(encoding="utf-8")
    assert "## Business vocabulary" in written
    assert "`CONF` -> confirmed" in written
