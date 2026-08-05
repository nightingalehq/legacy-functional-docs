#!/usr/bin/env python3
"""Dev-only fetcher for supplementary JCL/SQL-DDL smoke fixtures.

Pulls a small, named set of files from the openmainframeproject
cobol-programming-course repo (CC-BY-4.0) at a pinned commit SHA into
examples/external/cobol_course/ (gitignored -- these are upstream's files,
not ours to redistribute wholesale, and upstream could change).

Not part of the installed package, not run in CI, not wired into
`mfdoc ingest` by default. See docs/guides/extending.md and issue #13 for
the full rationale.

Usage:
    python scripts/fetch_cobol_course_fixtures.py
    python scripts/fetch_cobol_course_fixtures.py --dest /tmp/cobol_course
"""
from __future__ import annotations

import argparse
import pathlib
import urllib.parse
import urllib.request

REPO = "openmainframeproject/cobol-programming-course"
# Pinned so a re-run six months from now can't silently start exercising
# different upstream content. Bump deliberately; re-run the smoke test after.
COMMIT_SHA = "61c573dd13688f25e615e7cc4f9595cee38cd6a0"
LICENSE_NOTE = (
    "Files fetched by this script are (c) Contributors to the COBOL "
    "Programming Course, CC-BY-4.0: "
    "https://github.com/openmainframeproject/cobol-programming-course"
)

# A deliberately small, varied set: different EXEC/STEP/COND shapes, a
# cataloged proc, and the DB2-related JCL that carries embedded SQL DDL
# (CREATE TABLESPACE/TABLE/INDEX) and DB2 utility control statements --
# the content this repo's own synthetic fixture (examples/fixtures/jcl/
# MMB0100.jcl) can't exercise on its own.
FILES = [
    "COBOL Programming Course #2 - Learning COBOL/Labs/jcl/HELLO.jcl",
    "COBOL Programming Course #2 - Learning COBOL/Labs/jcl/CBL0002J.jcl",
    "COBOL Programming Course #2 - Learning COBOL/Labs/jcl/PAYROL00.jcl",
    "COBOL Programming Course #2 - Learning COBOL/Labs/jclproc/IGYWCLG.jcl",
    "COBOL Programming Course #3 - Advanced Topics/Labs/jcl/DB2SETUP.jcl",
    "COBOL Programming Course #3 - Advanced Topics/Labs/jcl/CRETBL.jcl",
    "COBOL Programming Course #3 - Advanced Topics/Labs/jcl/LOADTBL.jcl",
    "COBOL Programming Course #3 - Advanced Topics/Labs/jcl/SELTBL.jcl",
    "COBOL Programming Course #3 - Advanced Topics/Labs/jclproc/DSNUPROC.jcl",
    "COBOL Programming Course #4 - Testing/Labs/jcl/DEPTPAY.JCL",
]


def fetch(dest: pathlib.Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SOURCE.txt").write_text(
        f"{LICENSE_NOTE}\nFetched at commit {COMMIT_SHA}.\n"
        f"Regenerate with scripts/fetch_cobol_course_fixtures.py.\n"
    )
    for repo_path in FILES:
        url = (
            f"https://raw.githubusercontent.com/{REPO}/{COMMIT_SHA}/"
            + urllib.parse.quote(repo_path)
        )
        out_name = pathlib.Path(repo_path).name
        out_path = dest / out_name
        print(f"fetching {repo_path} -> {out_path}")
        with urllib.request.urlopen(url, timeout=30) as resp:
            out_path.write_bytes(resp.read())
    print(f"\n{len(FILES)} files written to {dest}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=pathlib.Path,
        default=pathlib.Path("examples/external/cobol_course"),
        help="Destination directory (default: examples/external/cobol_course)",
    )
    args = parser.parse_args()
    fetch(args.dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
