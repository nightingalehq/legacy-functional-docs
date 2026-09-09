"""Guards for the claude-cli ModelCaller and its --provider wiring.

No real `claude` process is invoked here -- these check the JSON-parsing
contract, the error paths (nonzero exit, unparseable output, is_error),
and that cmd_batch actually routes to ClaudeCLICaller, mirroring how
test_vertex_caller.py verifies VertexCaller's wiring.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mfdoc import cli
from mfdoc.claude_cli_caller import ClaudeCLICaller

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_run(stdout="", stderr="", returncode=0):
    def _run(cmd, input, capture_output, text, timeout):
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    return _run


def test_parses_result_and_usage_from_output_json(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        _fake_run(stdout='{"is_error": false, "result": "pong", '
                          '"usage": {"input_tokens": 2, "output_tokens": 4}}'),
    )
    caller = ClaudeCLICaller()
    response = caller("ping")
    assert response.text == "pong"
    assert response.input_tokens == 2
    assert response.output_tokens == 4


def test_nonzero_exit_raises_with_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stderr="boom", returncode=1))
    with pytest.raises(RuntimeError, match="boom"):
        ClaudeCLICaller()("ping")


def test_is_error_flag_raises(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        _fake_run(stdout='{"is_error": true, "result": "overloaded"}'),
    )
    with pytest.raises(RuntimeError, match="overloaded"):
        ClaudeCLICaller()("ping")


def test_unparseable_output_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="not json"))
    with pytest.raises(RuntimeError, match="unparseable"):
        ClaudeCLICaller()("ping")


def test_missing_claude_binary_raises_install_hint(monkeypatch):
    def _run(*a, **kw):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(RuntimeError, match="claude` CLI"):
        ClaudeCLICaller()("ping")


def test_timeout_raises(monkeypatch):
    def _run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(RuntimeError, match="timed out"):
        ClaudeCLICaller(timeout=1)("ping")


def test_model_flag_passed_through(monkeypatch):
    captured = {}

    def _run(cmd, input, capture_output, text, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(
            stdout='{"is_error": false, "result": "ok", "usage": {}}', stderr="", returncode=0,
        )
    monkeypatch.setattr(subprocess, "run", _run)
    ClaudeCLICaller(model="sonnet")("ping")
    assert "--model" in captured["cmd"] and "sonnet" in captured["cmd"]


def test_max_budget_usd_flag_passed_through_when_set(monkeypatch):
    """Issue #150: `claude -p` has no `max_tokens`-equivalent output-length
    cap, but `--max-budget-usd` ("only works with --print", i.e. exactly
    our -p usage) is the closest available lever to bound a runaway
    generation and narrow the class of 600s hangs -- must reach the
    subprocess command when a caller opts in."""
    captured = {}

    def _run(cmd, input, capture_output, text, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(
            stdout='{"is_error": false, "result": "ok", "usage": {}}', stderr="", returncode=0,
        )
    monkeypatch.setattr(subprocess, "run", _run)
    ClaudeCLICaller(max_budget_usd=0.5)("ping")
    assert "--max-budget-usd" in captured["cmd"]
    idx = captured["cmd"].index("--max-budget-usd")
    assert captured["cmd"][idx + 1] == "0.5"


def test_max_budget_usd_flag_omitted_when_not_set(monkeypatch):
    """No behaviour change for anyone not opting in -- omitting the flag
    entirely (not passing some invented default) leaves `claude -p`'s own
    behaviour untouched, per this repo's stance against guessing at a
    business-tuned number nobody asked for."""
    captured = {}

    def _run(cmd, input, capture_output, text, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(
            stdout='{"is_error": false, "result": "ok", "usage": {}}', stderr="", returncode=0,
        )
    monkeypatch.setattr(subprocess, "run", _run)
    ClaudeCLICaller()("ping")
    assert "--max-budget-usd" not in captured["cmd"]


def test_max_budget_usd_rejects_zero():
    """0 is nonsensical for a dollar cap -- fail fast, at construction,
    rather than passing it through to `claude -p` for a confusing failure
    (or, worse, some CLI-specific "no limit" behaviour) later."""
    with pytest.raises(ValueError, match="positive dollar amount"):
        ClaudeCLICaller(max_budget_usd=0)


def test_max_budget_usd_rejects_negative():
    with pytest.raises(ValueError, match="positive dollar amount"):
        ClaudeCLICaller(max_budget_usd=-1.5)


def test_cmd_batch_routes_to_claude_cli_caller_when_provider_is_claude_code(cli_args, tmp_path, monkeypatch):
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    constructed = {}

    class FakeClaudeCLICaller:
        def __init__(self, model=None, timeout=None, max_budget_usd=None):
            constructed.update(model=model, timeout=timeout, max_budget_usd=max_budget_usd)

        def __call__(self, prompt):
            from mfdoc.batch import ModelResponse
            return ModelResponse(text=prompt, input_tokens=1, output_tokens=1)

    monkeypatch.setattr("mfdoc.claude_cli_caller.ClaudeCLICaller", FakeClaudeCLICaller)

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members="MMP0100",
        model=None, concurrency=1, state="", caller="anthropic", provider="claude-code",
        claude_code_timeout=None,
    )
    cli.cmd_batch(args)
    assert constructed == {"model": None, "timeout": None, "max_budget_usd": None}


def test_claude_code_timeout_flag_passed_through(cli_args, tmp_path, monkeypatch):
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    constructed = {}

    class FakeClaudeCLICaller:
        def __init__(self, model=None, timeout=None, max_budget_usd=None):
            constructed.update(model=model, timeout=timeout, max_budget_usd=max_budget_usd)

        def __call__(self, prompt):
            from mfdoc.batch import ModelResponse
            return ModelResponse(text=prompt, input_tokens=1, output_tokens=1)

    monkeypatch.setattr("mfdoc.claude_cli_caller.ClaudeCLICaller", FakeClaudeCLICaller)

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members="MMP0100",
        model=None, concurrency=1, state="", caller="anthropic", provider="claude-code",
        claude_code_timeout=1800,
    )
    cli.cmd_batch(args)
    assert constructed == {"model": None, "timeout": 1800, "max_budget_usd": None}


def test_cli_claude_code_max_budget_usd_flag_passed_through(cli_args, tmp_path, monkeypatch):
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    constructed = {}

    class FakeClaudeCLICaller:
        def __init__(self, model=None, timeout=None, max_budget_usd=None):
            constructed.update(model=model, timeout=timeout, max_budget_usd=max_budget_usd)

        def __call__(self, prompt):
            from mfdoc.batch import ModelResponse
            return ModelResponse(text=prompt, input_tokens=1, output_tokens=1)

    monkeypatch.setattr("mfdoc.claude_cli_caller.ClaudeCLICaller", FakeClaudeCLICaller)

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members="MMP0100",
        model=None, concurrency=1, state="", caller="anthropic", provider="claude-code",
        claude_code_timeout=None, claude_code_max_budget_usd=0.75,
    )
    cli.cmd_batch(args)
    assert constructed == {"model": None, "timeout": None, "max_budget_usd": 0.75}


def test_default_timeout_used_when_none_given():
    """The CLI's --claude-code-timeout defaults to None (unset) so
    `_build_model_caller` always passes a `timeout` kwarg; ClaudeCLICaller
    itself must still fall back to DEFAULT_TIMEOUT_S in that case, not pass
    `None` straight to subprocess.run's `timeout=` (which means "no timeout
    at all", not "600s")."""
    from mfdoc.claude_cli_caller import DEFAULT_TIMEOUT_S
    assert ClaudeCLICaller(timeout=None).timeout == DEFAULT_TIMEOUT_S
