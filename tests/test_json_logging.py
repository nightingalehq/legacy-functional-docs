"""Structured diagnostic logs must not corrupt command output."""

import json
import logging

import pytest

from mfdoc import cli
from test_logging import preserve_root_logger  # noqa: F401


def test_json_ingest_and_derive_leave_stdout_parseable(cli_args, capsys, preserve_root_logger):
    prefix = ["--log-format", "json", "--verbose"]
    assert cli.main([*prefix, "ingest", "--config", cli_args.config]) == 0
    ingested = capsys.readouterr()
    assert ingested.out == ""
    records = [json.loads(line) for line in ingested.err.splitlines()]
    assert records
    assert all({"timestamp", "level", "logger", "message"} <= row.keys() for row in records)
    assert any(row.get("stage") == "ingest" for row in records)
    assert cli.main([*prefix, "derive", "--config", cli_args.config]) == 0
    derived = capsys.readouterr()
    assert isinstance(json.loads(derived.out), dict)


def test_quiet_suppresses_info_but_keeps_warning(capsys, preserve_root_logger):
    cli._configure_logging(False, None, quiet=True, log_format="json")
    logger = logging.getLogger("mfdoc.test")
    logger.info("not emitted")
    logger.warning("warning", extra={"stage": "fixture", "member": "SYNTHETIC", "run_id": "local"})
    captured = capsys.readouterr()
    record = json.loads(captured.err)
    assert record["level"] == "WARNING"
    assert record["message"] == "warning"
    assert record["stage"] == "fixture"
    assert record["member"] == "SYNTHETIC"
    assert record["run_id"] == "local"
    assert captured.out == ""


def test_json_exception_stays_on_one_line(capsys, preserve_root_logger):
    cli._configure_logging(True, None, log_format="json")
    try:
        raise RuntimeError("first\nsecond")
    except RuntimeError:
        logging.getLogger("mfdoc.test").exception("failed")
    record = json.loads(capsys.readouterr().err)
    assert "RuntimeError" in record["exception"]
    assert "first\nsecond" in record["exception"]


def test_verbose_and_quiet_are_mutually_exclusive(cli_args, preserve_root_logger):
    with pytest.raises(SystemExit) as error:
        cli.main(["--verbose", "--quiet", "coverage", "--config", cli_args.config])
    assert error.value.code == 2


def test_json_log_file_uses_the_selected_format(tmp_path, preserve_root_logger):
    path = tmp_path / "diagnostics.jsonl"
    cli._configure_logging(False, str(path), log_format="json")
    logging.getLogger("mfdoc.test").info("stored", extra={"run_id": "local"})
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["message"] == "stored"
    assert record["run_id"] == "local"


def test_quiet_cli_suppresses_ingest_progress(cli_args, capsys, preserve_root_logger):
    assert cli.main(["--quiet", "ingest", "--config", cli_args.config]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
