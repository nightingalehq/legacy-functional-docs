"""Opt-in smoke test against externally-fetched JCL/SQL-DDL fixtures.

Not run by default: it depends on network access having already been used
to fetch a third-party repo's files (see scripts/fetch_cobol_course_fixtures.py
and issue #13), so it's skipped whenever examples/external/cobol_course/
hasn't been populated. This is a robustness/smoke check, not a golden test --
it doesn't extend EXPECTED_COVERAGE in test_coverage_snapshot.py, which is
keyed to the checked-in fixture set only.

To run:
    python scripts/fetch_cobol_course_fixtures.py
    pytest tests/test_external_fixtures_smoke.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mfdoc import cli, graph
from mfdoc.db import connect

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTERNAL_DIR = REPO_ROOT / "examples" / "external" / "cobol_course"

pytestmark = pytest.mark.skipif(
    not EXTERNAL_DIR.is_dir() or not any(EXTERNAL_DIR.glob("*.jcl")) and not any(
        EXTERNAL_DIR.glob("*.JCL")
    ),
    reason="examples/external/cobol_course/ not populated -- run "
    "scripts/fetch_cobol_course_fixtures.py first",
)


@pytest.fixture(scope="module")
def external_coverage(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("external_smoke")
    cfg = {
        "index_db": str(tmp_dir / "index.db"),
        "sources": [
            {
                "path": str(EXTERNAL_DIR),
                "glob": ["*.jcl", "*.JCL"],
                "dialect": "jcl",
                "system": "COBOL-COURSE-SMOKE",
            }
        ],
        "options": {},
    }
    config_path = tmp_dir / "project.yml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    from types import SimpleNamespace

    args = SimpleNamespace(config=str(config_path))
    assert cli.cmd_ingest(args) == 0

    conn = connect(cfg["index_db"])
    cov = graph.coverage(conn)
    conn.close()
    return cov


def test_ingest_does_not_crash(external_coverage):
    assert external_coverage["members"] > 0


def test_recognition_rate_stays_sane(external_coverage):
    # Real-world JCL is messier than our synthetic fixture; this is a floor
    # to catch a wholesale regression, not a tight snapshot like
    # test_coverage_snapshot.py.
    assert external_coverage["line_recognition_rate"] > 0.5
