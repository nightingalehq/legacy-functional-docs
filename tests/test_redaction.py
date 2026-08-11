"""options.redact must keep sensitive literals out of the brief -- the thing
that actually reaches a prompt -- not just out of rendered documents.

Uses its own fixture and its own isolated index, separate from the shared
session fixture, so a fake NI number and password never touch the main
coverage snapshot.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from mfdoc import cli
from mfdoc.brief import module_brief
from mfdoc.db import connect
from mfdoc.redact import Redactor

REPO_ROOT = Path(__file__).resolve().parent.parent

FAKE_NINO = "AB123456C"
FAKE_PASSWORD = "Tr0ub4dor&3"


def _ingest_sensitive_fixture(tmp_path, repo_root):
    cfg = {
        "index_db": str(tmp_path / "index.db"),
        "sources": [{
            "path": str(repo_root / "examples" / "inputs" / "redaction"),
            "glob": ["*.nsp"],
            "dialect": "natural",
        }],
        "options": {},
    }
    config_path = tmp_path / "project.yml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    args = SimpleNamespace(config=str(config_path))
    assert cli.cmd_ingest(args) == 0
    return connect(cfg["index_db"])


def test_sensitive_literals_appear_when_redaction_is_off(tmp_path):
    conn = _ingest_sensitive_fixture(tmp_path, REPO_ROOT)
    out = module_brief(conn, "SENSITIVE", redact=Redactor(enabled=False))
    assert FAKE_NINO in out
    assert FAKE_PASSWORD in out


def test_sensitive_literals_are_redacted_when_enabled(tmp_path):
    conn = _ingest_sensitive_fixture(tmp_path, REPO_ROOT)
    redactor = Redactor(patterns=[FAKE_NINO, r"Tr0ub4dor&3"], enabled=True)
    out = module_brief(conn, "SENSITIVE", redact=redactor)
    assert FAKE_NINO not in out
    assert FAKE_PASSWORD not in out
    assert "[REDACTED]" in out


def test_redactor_from_options_reads_project_config():
    redactor = Redactor.from_options({
        "redact": {"enabled": True, "patterns": [FAKE_NINO]},
    })
    assert redactor.enabled
    assert redactor("contact " + FAKE_NINO) == "contact [REDACTED]"


def test_redactor_disabled_by_default():
    redactor = Redactor.from_options({})
    assert not redactor.enabled
    assert redactor(FAKE_NINO) == FAKE_NINO
