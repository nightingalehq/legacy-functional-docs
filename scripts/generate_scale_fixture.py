#!/usr/bin/env python3
"""Dev-only synthetic scale fixture generator (issue #9, Phase 6).

Synthesizes a few thousand plausible Natural programs into
examples/external/scale_fixture/ (gitignored, same as
scripts/fetch_cobol_course_fixtures.py's destination -- bulky and
regenerable, not a golden fixture) with a call graph dense enough to
exercise graph.resolve()'s and graph.orphans()'s UPPER(...) lookups at the
scale a real mill system might reach (2,000-8,000 Natural members), so a
change to those lookups (e.g. the ix_*_upper_* expression indexes added for
issue #9a) can be measured rather than guessed at.

Each generated member is a tiny but valid Natural program: DEFINE DATA /
END-DEFINE plus a handful of CALLNAT statements. Call targets are a mix of:
  - other generated members (resolved edges)
  - a name that doesn't exist (unresolved edges -- orphans()/resolve() both
    have to do real work here)
  - a variable target (dynamic edges, via CALLNAT #TARGET)
so the generated corpus isn't an unrealistic case where every call resolves
on the first index lookup.

Not part of the installed package, not run in CI, not wired into
`mfdoc ingest` by default -- run manually when measuring scale:

    python scripts/generate_scale_fixture.py --count 5000
    mfdoc ingest   --config examples/external/scale_fixture/project.yml
    mfdoc derive   --config examples/external/scale_fixture/project.yml
    mfdoc coverage --config examples/external/scale_fixture/project.yml
"""
from __future__ import annotations

import argparse
import pathlib
import random
import shutil
import tempfile
import textwrap

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

PROJECT_YML_TEMPLATE = """\
project: Synthetic scale fixture
system: SCALE
index_db: .mfdoc/index.db
sources:
  - path: natural
    glob: ["*.nsp"]
    dialect: natural
    library: SCALELIB
    system: SCALE
    sequence_columns: none
options:
  quality_gates: {}
"""


def _member_name(i: int) -> str:
    return f"SCALE{i:05d}"


def _program_text(name: str, call_targets: list[str], rng: random.Random) -> str:
    calls = []
    for target in call_targets:
        if target is None:
            # Dynamic target: exercises the dynamic_target gap path.
            calls.append("  CALLNAT #TARGET-PGM")
        else:
            calls.append(f"  CALLNAT '{target}'")
    body = "\n".join(calls) if calls else "  *"
    return textwrap.dedent(f"""\
        ** {name} -- synthetic member for scale testing (issue #9)
        DEFINE DATA LOCAL
        1 #TARGET-PGM (A8)
        END-DEFINE
        {body}
        END
        """)


def generate(dest: pathlib.Path, count: int, calls_per_member: int, seed: int) -> None:
    rng = random.Random(seed)
    names = [_member_name(i) for i in range(count)]

    # Stage into a scratch directory first, then swap into place, so a
    # partial/failed run never leaves `dest` half-old-half-new -- same
    # discipline as fetch_cobol_course_fixtures.py's fetch().
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix=".generate_scale_fixture-", dir=dest.parent))
    try:
        natural_dir = tmp_dir / "natural"
        natural_dir.mkdir(parents=True)
        for i, name in enumerate(names):
            targets: list[str | None] = []
            for _ in range(calls_per_member):
                roll = rng.random()
                if roll < 0.70:
                    # Resolved: call another generated member (never self).
                    other = rng.randrange(count)
                    if other == i:
                        other = (other + 1) % count
                    targets.append(names[other])
                elif roll < 0.95:
                    # Unresolved: a name nothing in this corpus defines.
                    targets.append(f"EXTERNAL-{i:05d}")
                else:
                    # Dynamic: target is a variable, not a literal.
                    targets.append(None)
            (natural_dir / f"{name}.nsp").write_text(
                _program_text(name, targets, rng), encoding="utf-8"
            )
        (tmp_dir / "project.yml").write_text(PROJECT_YML_TEMPLATE, encoding="utf-8")
        (tmp_dir / "SOURCE.txt").write_text(
            f"Synthetic, generated fixture -- {count} members, "
            f"{calls_per_member} CALLNAT targets each (seed={seed}).\n"
            "Regenerate with scripts/generate_scale_fixture.py; not meant to be edited by hand.\n",
            encoding="utf-8",
        )
        if dest.exists():
            shutil.rmtree(dest)
        tmp_dir.rename(dest)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    print(f"{count} members written to {dest / 'natural'}")
    print(f"project config: {dest / 'project.yml'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=3000, help="number of synthetic members (default: 3000)")
    parser.add_argument("--calls-per-member", type=int, default=3, help="CALLNAT statements per member (default: 3)")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed, for a reproducible corpus (default: 42)")
    parser.add_argument(
        "--dest", type=pathlib.Path,
        default=REPO_ROOT / "examples" / "external" / "scale_fixture",
        help="destination directory (default: examples/external/scale_fixture, "
             "relative to the repo root, not the current directory)",
    )
    args = parser.parse_args()
    generate(args.dest, args.count, args.calls_per_member, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
