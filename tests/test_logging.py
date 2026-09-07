"""Guards for issue #83: `logging`-module diagnostic output for the batch
pipeline (batch.py, testbatch.py, retry.py) and the `--verbose`/`--log-file`
CLI wiring that controls it (cli.py). Deliberately narrow in scope to the
genuinely-diagnostic call sites the issue names as the starting point
(long-running batch/test-batch progress, transient-error retries) -- real
CLI output (coverage numbers, gate pass/fail, the batch summary table) stays
on stdout via `print()` and is not touched here; see tests/test_cli_batch.py
and tests/test_coverage_cli.py for those.

Reuses tests/test_batch.py's `FakeCaller`/`GOOD_FRONTMATTER` fixtures rather
than re-deriving the same well-formed-document shape here -- pytest's
rootless "prepend" import mode makes `test_batch` importable by name once
it's been collected (both files live directly under tests/, no package
`__init__.py`), the same way tests/test_test_batch.py's own helpers are
reused nowhere else but could be.
"""

from __future__ import annotations

import logging

import pytest

from mfdoc import cli
from mfdoc.retry import call_with_retry
from test_batch import FakeCaller


class _Transient(Exception):
    pass


def test_call_with_retry_logs_a_warning_for_each_transient_retry(caplog):
    """The one place both AnthropicCaller and VertexCaller delegate to for
    backoff -- previously silent, so a person watching a long batch run had
    no visibility into a rate limit/connection blip being retried at all."""
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _Transient("simulated rate limit")
        return "ok"

    with caplog.at_level(logging.WARNING, logger="mfdoc.retry"):
        result = call_with_retry(
            fn, is_retryable=lambda exc: isinstance(exc, _Transient),
            max_retries=5, sleep=lambda s: None,
        )
    assert result == "ok"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2  # attempts 1 and 2 failed transiently, attempt 3 succeeded
    assert "retrying" in warnings[0].getMessage()
    assert "simulated rate limit" in warnings[0].getMessage()


def test_call_with_retry_logs_nothing_when_the_first_call_succeeds(caplog):
    with caplog.at_level(logging.DEBUG, logger="mfdoc.retry"):
        call_with_retry(lambda: "ok", is_retryable=lambda exc: True, sleep=lambda s: None)
    assert caplog.records == []


def test_run_batch_logs_debug_on_a_resumed_skip(indexed_db, tmp_path, caplog):
    """A member skipped because its brief is unchanged from a prior
    successful run ("resumed skip") is exactly the kind of routine, silent
    event the issue's own example calls out -- previously reported nowhere,
    not even at DEBUG."""
    from mfdoc import batch as batch_mod

    members = ["MMP0100"]
    state_path = tmp_path / "state.json"
    caller = FakeCaller()
    first = batch_mod.run_batch(
        indexed_db, members, tmp_path / "out", caller, "rules", "template",
        state_path=state_path,
    )
    assert first.ok == 1

    with caplog.at_level(logging.DEBUG, logger="mfdoc.batch"):
        second = batch_mod.run_batch(
            indexed_db, members, tmp_path / "out", caller, "rules", "template",
            state_path=state_path,
        )
    assert second.skipped == 1
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("skip MMP0100" in r.getMessage() and "resumed" in r.getMessage() for r in debug_records)


def test_run_batch_logs_warning_on_a_validation_retry(indexed_db, tmp_path, caplog):
    from mfdoc import batch as batch_mod

    caller = FakeCaller(fail_first=True)
    with caplog.at_level(logging.WARNING, logger="mfdoc.batch"):
        summary = batch_mod.run_batch(
            indexed_db, ["MMP0100"], tmp_path / "out", caller, "rules", "template",
        )
    assert summary.retried == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "MMP0100" in r.getMessage() and "validation failed" in r.getMessage() and "retrying" in r.getMessage()
        for r in warnings
    )


def test_run_batch_logs_error_on_a_model_call_failure(indexed_db, tmp_path, caplog):
    from mfdoc import batch as batch_mod

    def raising_caller(prompt: str):
        raise RuntimeError("simulated transient network error")

    with caplog.at_level(logging.ERROR, logger="mfdoc.batch"):
        summary = batch_mod.run_batch(
            indexed_db, ["MMP0100"], tmp_path / "out", raising_caller, "rules", "template",
        )
    assert summary.failed == 1
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        "MMP0100" in r.getMessage() and "model call failed" in r.getMessage() for r in errors
    )


def _seed_fake_test_case_db():
    """Minimal in-memory index with one member and one test_case row --
    mirrors tests/test_test_batch.py's own
    test_run_test_batch_does_not_reuse_state_or_file_across_frameworks setup,
    which is the established pattern here for a run_test_batch test that
    doesn't need the full session-scoped fixture project."""
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


def _valid_test_doc_text() -> str:
    return """---
title: "FAKEMOD — generated tests"
doc_type: generated_test
system: "MOM"
generated_by: mfdoc
generated_at: "2026-08-11"
review_status: draft
confidence_summary:
  verified: 1
language: python
framework: pytest
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


def test_run_test_batch_logs_debug_on_a_resumed_skip(tmp_path, caplog):
    from mfdoc.batch import ModelResponse
    from mfdoc import testbatch

    conn = _seed_fake_test_case_db()

    def caller(prompt):
        return ModelResponse(text=_valid_test_doc_text(), input_tokens=1, output_tokens=1)

    state_path = tmp_path / "state.json"
    out_dir = tmp_path / "out"
    first = testbatch.run_test_batch(
        conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
        "rules", "template", state_path=state_path,
    )
    assert first.ok == 1

    with caplog.at_level(logging.DEBUG, logger="mfdoc.testbatch"):
        second = testbatch.run_test_batch(
            conn, ["FAKEMOD"], "python", "pytest", out_dir, caller,
            "rules", "template", state_path=state_path,
        )
    assert second.skipped == 1
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("skip FAKEMOD" in r.getMessage() and "resumed" in r.getMessage() for r in debug_records)


def test_run_test_batch_logs_warning_when_the_model_call_raises(tmp_path, caplog):
    from mfdoc import testbatch

    conn = _seed_fake_test_case_db()

    def raising_caller(prompt):
        raise RuntimeError("simulated transient network error")

    with caplog.at_level(logging.WARNING, logger="mfdoc.testbatch"):
        summary = testbatch.run_test_batch(
            conn, ["FAKEMOD"], "python", "pytest", tmp_path / "out", raising_caller,
            "rules", "template",
        )
    assert summary.failed == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("FAKEMOD" in r.getMessage() and "retrying" in r.getMessage() for r in warnings)


def test_configure_logging_writes_to_the_given_log_file(tmp_path):
    log_path = tmp_path / "mfdoc.log"
    cli._configure_logging(verbose=False, log_file=str(log_path))
    try:
        logging.getLogger("mfdoc.batch").info("hello from the log-file test")
    finally:
        # Restore a clean, handler-less state so this test doesn't leak a
        # FileHandler (and this file descriptor) into every later test in
        # the same session -- _configure_logging's own force=True already
        # replaces handlers on each call, but the very last call in the
        # suite would otherwise hold this test's temp file open.
        cli._configure_logging(verbose=False, log_file=None)
    assert log_path.exists()
    assert "hello from the log-file test" in log_path.read_text(encoding="utf-8")


def test_configure_logging_verbose_enables_debug_level():
    cli._configure_logging(verbose=True, log_file=None)
    try:
        assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
    finally:
        cli._configure_logging(verbose=False, log_file=None)
    assert logging.getLogger().getEffectiveLevel() == logging.INFO


def test_main_exits_cleanly_when_log_file_parent_dir_is_missing(cli_args, tmp_path, capsys):
    """A `--log-file` path whose parent directory doesn't exist raises
    `OSError` from `logging.FileHandler` inside `_configure_logging` --
    previously an uncaught traceback since it fired before the
    try/except ConfigError block in main() even started; should be a
    clean, exit-2 usage error like every other CLI validation failure
    instead (see the Copilot review on PR #115)."""
    bad_log_path = tmp_path / "no-such-parent-dir" / "mfdoc.log"
    argv = ["--log-file", str(bad_log_path), "coverage", "--config", cli_args.config]

    rc = cli.main(argv)

    assert rc == 2
    assert not bad_log_path.parent.exists()
    err = capsys.readouterr().err
    assert str(bad_log_path) in err


def test_main_wires_verbose_and_log_file_flags_before_the_subcommand(cli_args, tmp_path, caplog):
    """End-to-end through cli.main()'s own argument parsing -- not just
    cmd_batch called directly -- proving --verbose/--log-file actually reach
    _configure_logging when a real subcommand is dispatched."""
    log_path = tmp_path / "mfdoc.log"
    argv = ["--verbose", "--log-file", str(log_path), "coverage", "--config", cli_args.config]
    try:
        rc = cli.main(argv)
    finally:
        cli._configure_logging(verbose=False, log_file=None)
    assert rc == 0
    assert log_path.exists()
