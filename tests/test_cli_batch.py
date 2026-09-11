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


def test_batch_command_reports_per_member_duration_and_retries(cli_args, tmp_path, capsys):
    """cmd_batch's report must surface issue #84's new per-member
    duration_s/retries fields (per-line) and the run-wide totals
    (BatchSummary.total_duration_s/total_retries), not just tokens/cost."""
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members="MMP0100",
        model="claude-sonnet-4-5", concurrency=1, state="", caller="fake-echo",
    )
    cli.cmd_batch(args)
    out = capsys.readouterr().out
    assert "duration=" in out
    assert "retries=" in out
    assert "call time:" in out
    assert "transient retries" in out


def test_batch_command_dry_run_reports_a_plan_and_makes_no_model_calls(
    derive_result, cli_args, tmp_path, capsys, monkeypatch,
):
    """--dry-run must never reach _build_model_caller (no --model/--provider/
    API key needed) and must print a plan, not run a real batch."""
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    def exploding_build_caller(args):
        raise AssertionError("--dry-run must never build a real model caller")

    monkeypatch.setattr(cli, "_build_model_caller", exploding_build_caller)

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members="MMP0100",
        state="", dry_run=True,
    )
    rc = cli.cmd_batch(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "corpus signature:" in out
    assert "MMP0100" in out
    assert not (tmp_path / "out").exists(), "--dry-run must not write any output"


def test_batch_command_dry_run_member_cache_capable_reflects_caller_not_just_provider(
    derive_result, cli_args, tmp_path, monkeypatch,
):
    """Issue #214, Copilot review round 3 on PR #215: _build_model_caller
    checks `--caller fake-echo` before it ever looks at `--provider` --
    `--caller fake-echo --provider anthropic` builds the no-op fake-echo
    callable, which has no set_member_cache_prefixes hook at all. plan_
    batch's `member_cache_capable` must reflect that (False), not just
    `--provider`'s own value (which alone would say True here) -- otherwise
    the dry-run preview hashes as if a member-level prefix would be
    registered for a run that, via --caller fake-echo, never builds a
    caller capable of one."""
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    from mfdoc import batch as batch_mod

    seen = {}
    real_plan_batch = batch_mod.plan_batch

    def spying_plan_batch(*args, **kwargs):
        seen["member_cache_capable"] = kwargs.get("member_cache_capable")
        return real_plan_batch(*args, **kwargs)

    monkeypatch.setattr(batch_mod, "plan_batch", spying_plan_batch)

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members="MMP0100",
        state="", dry_run=True, caller="fake-echo", provider="anthropic",
    )
    rc = cli.cmd_batch(args)
    assert rc == 0
    assert seen["member_cache_capable"] is False

    seen.clear()
    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out2"), members="MMP0100",
        state="", dry_run=True, caller="anthropic", provider="anthropic",
    )
    rc = cli.cmd_batch(args)
    assert rc == 0
    assert seen["member_cache_capable"] is True
