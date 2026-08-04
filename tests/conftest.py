"""Shared fixtures for the mfdoc test suite.

The suite runs the real pipeline (ingest -> derive) once per session against
the repo's own fixtures and worked example, using the checked-in project.yml
as the source of truth for how those fixtures are configured. Individual
tests then assert against the resulting SQLite fact store. This keeps the
tests honest: they exercise the same code path a real engagement would use,
not a hand-rolled shortcut that could drift from it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mfdoc import cli, graph  # noqa: E402
from mfdoc.db import connect  # noqa: E402


@pytest.fixture(scope="session")
def project_config(tmp_path_factory) -> Path:
    """A copy of the repo's project.yml with absolute source paths and an
    isolated index_db, so tests don't depend on cwd and don't touch the
    developer's own .mfdoc/ directory."""
    tmp_dir = tmp_path_factory.mktemp("mfdoc_project")
    cfg = yaml.safe_load((REPO_ROOT / "project.yml").read_text(encoding="utf-8"))
    for source in cfg["sources"]:
        source["path"] = str((REPO_ROOT / source["path"]).resolve())
    cfg["index_db"] = str(tmp_dir / "index.db")
    config_path = tmp_dir / "project.yml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return config_path


@pytest.fixture(scope="session")
def cli_args(project_config: Path) -> SimpleNamespace:
    return SimpleNamespace(config=str(project_config))


@pytest.fixture(scope="session")
def derive_result(cli_args) -> dict:
    """Run ingest + derive once for the whole session and return derive's result."""
    assert cli.cmd_ingest(cli_args) == 0
    cfg = cli.load_config(cli_args.config)
    conn = connect(cfg["index_db"])
    result = graph.run_all(conn)
    conn.commit()
    conn.close()
    return result


@pytest.fixture(scope="session")
def indexed_db(derive_result, cli_args):
    """A connection to the fully ingested + derived index, open for the session."""
    cfg = cli.load_config(cli_args.config)
    conn = connect(cfg["index_db"])
    yield conn
    conn.close()
