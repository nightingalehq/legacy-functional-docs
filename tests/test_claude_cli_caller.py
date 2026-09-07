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


def test_timeout_raises_immediately_when_retries_disabled(monkeypatch):
    def _run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
    monkeypatch.setattr(subprocess, "run", _run)
    with pytest.raises(RuntimeError, match="timed out"):
        ClaudeCLICaller(timeout=1, max_retries=0)("ping")


def test_timeout_is_retried_and_can_eventually_succeed(monkeypatch):
    """A timeout is the one `claude -p` failure this caller treats as
    transient (#79's review comment: consistency with
    AnthropicCaller/VertexCaller's retry behavior) -- a network/load blip
    that a retry might not hit again, unlike a bad prompt or broken
    install."""
    attempts = {"n": 0}

    def _run(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
        return SimpleNamespace(
            stdout='{"is_error": false, "result": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}',
            stderr="", returncode=0,
        )
    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr("mfdoc.retry.time.sleep", lambda s: None)

    response = ClaudeCLICaller(timeout=1, max_retries=5)("ping")
    assert response.text == "ok"
    assert attempts["n"] == 3


def test_timeout_gives_up_after_max_retries(monkeypatch):
    attempts = {"n": 0}

    def _run(*a, **kw):
        attempts["n"] += 1
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr("mfdoc.retry.time.sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="timed out"):
        ClaudeCLICaller(timeout=1, max_retries=2)("ping")
    assert attempts["n"] == 3  # initial attempt + 2 retries


def test_a_nonzero_exit_is_not_retried_even_with_retries_enabled(monkeypatch):
    """Only a timeout is treated as transient -- a nonzero exit usually
    means something genuinely wrong (bad prompt, broken install, auth
    failure) that a retry would just repeat identically."""
    attempts = {"n": 0}

    def _run(*a, **kw):
        attempts["n"] += 1
        return SimpleNamespace(stdout="", stderr="boom", returncode=1)
    monkeypatch.setattr(subprocess, "run", _run)

    with pytest.raises(RuntimeError, match="boom"):
        ClaudeCLICaller(max_retries=5)("ping")
    assert attempts["n"] == 1


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


def test_cmd_batch_routes_to_claude_cli_caller_when_provider_is_claude_code(cli_args, tmp_path, monkeypatch):
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    constructed = {}

    class FakeClaudeCLICaller:
        def __init__(self, model=None, timeout=None):
            constructed.update(model=model, timeout=timeout)

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
    assert constructed == {"model": None, "timeout": None}


def test_claude_code_timeout_flag_passed_through(cli_args, tmp_path, monkeypatch):
    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")

    constructed = {}

    class FakeClaudeCLICaller:
        def __init__(self, model=None, timeout=None):
            constructed.update(model=model, timeout=timeout)

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
    assert constructed == {"model": None, "timeout": 1800}


def test_default_timeout_used_when_none_given():
    """The CLI's --claude-code-timeout defaults to None (unset) so
    `_build_model_caller` always passes a `timeout` kwarg; ClaudeCLICaller
    itself must still fall back to DEFAULT_TIMEOUT_S in that case, not pass
    `None` straight to subprocess.run's `timeout=` (which means "no timeout
    at all", not "600s")."""
    from mfdoc.claude_cli_caller import DEFAULT_TIMEOUT_S
    assert ClaudeCLICaller(timeout=None).timeout == DEFAULT_TIMEOUT_S
