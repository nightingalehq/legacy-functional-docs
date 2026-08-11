# `examples/outputs/` — a full run of the pipeline against `examples/inputs/`

Every file under here is real output from the pipeline described in the root
[`README.md`](../../README.md), run against the bundled sample codebase in
[`examples/inputs/`](../inputs/) — not a hand-crafted mockup. Every citation in
every document resolves against the fact store you get by following the
reproduction steps below (`mfdoc validate`/`mfdoc test-validate` both report
0 invalid citations across the whole tree).

## Layout

Output is split into two categories, each mirroring `examples/inputs/`'s own
`<dialect>/<library>` shape (library segment omitted where a dialect has none,
e.g. DDM/FDT) so a large real engagement's output tree stays navigable by
source, not one flat pile of files:

```
outputs/
  index.db                     fact-store copy (SQLite) -- open it directly, or `mfdoc export`'s json below
  index.json                   full fact-store dump (mfdoc export --json)
  coverage.json                mfdoc coverage --json
  rules-register.md            mfdoc rules-register (doc_type: register)
  test-plan-register.md        mfdoc test-plan --out (doc_type: register)
  testability-advisory.md      mfdoc test-advisory --out (doc_type: register)
  docs/
    system-overview.md         cross-cutting: whole-system narrative
    gap-register.md            cross-cutting: every unresolved gap, as SME questions
    entities/                  cross-cutting: one doc per business entity (spans multiple dialects)
    process-flows/             cross-cutting: JCL/CICS-driven orchestration
    natural/MILLPROD/*.md      module docs, one per batchable Natural member
    mantis/STEELLIB/*.md       module docs, one per batchable Mantis member
  tests/
    natural/MILLPROD/python/pytest/{MEMBER}.md + {MEMBER}.py
    natural/MILLPROD/natural/natunit/{MEMBER}.md + {MEMBER}.nsp
    natural/MILLPROD/mantis/native/{MEMBER}.md + {MEMBER}.mantis
    natural/MILLPROD/silkcentral/testcase/{MEMBER}.md
    natural/MILLPROD/uipath/testcase/{MEMBER}.md
    mantis/STEELLIB/python/pytest/{MEMBER}.md + {MEMBER}.py
    mantis/STEELLIB/natural/natunit/{MEMBER}.md + {MEMBER}.nsp
    mantis/STEELLIB/mantis/native/{MEMBER}.md + {MEMBER}.mantis
    mantis/STEELLIB/silkcentral/testcase/{MEMBER}.md
    mantis/STEELLIB/uipath/testcase/{MEMBER}.md
```

`--matrix` renders every configured `options.testgen.matrix` target for every
selected member regardless of that member's own source dialect (the target
language is the destination, not a filter on source) — so `tests/natural/`
does contain a `mantis/native` rendering (a Mantis-syntax driver testing a
Natural program) and `tests/mantis/` contains a `natural/natunit` rendering
the same way; that's the intended behaviour of a destination matrix, not a
bug in the tree above.

`docs/entities/`, `docs/process-flows/`, `docs/system-overview.md` and
`docs/gap-register.md` don't have a per-dialect home because they're
cross-cutting by nature (an entity doc spans whatever dialects define and use
it; the system overview spans everything) — they sit at the `docs/` root
instead of being forced into a `<dialect>/` shape that doesn't fit them.

## What's auto-refreshed vs. hand/session-produced

| What | How it's produced | Kept current by |
|---|---|---|
| `index.db`, `index.json`, `coverage.json`, `rules-register.md`, `test-plan-register.md`, `testability-advisory.md` | Fully deterministic — no model call anywhere in `ingest`/`derive`/`coverage`/`test-plan`/`test-advisory`/`rules-register`/`export` | CI, automatically, on every push to `main` (see `.github/workflows/ci.yml`'s `update-examples` job) — committed back with `[skip ci]` so it doesn't retrigger itself |
| `docs/natural/`, `docs/mantis/`, `tests/natural/`, `tests/mantis/` | `mfdoc batch`/`mfdoc test-batch --matrix`, run for real via `--provider claude-code` (the local Claude Code CLI, no `ANTHROPIC_API_KEY` needed); `tests/` covers every configured `options.testgen.matrix` target, not just one language | Whoever re-runs the commands below — CI does not call any model, by design (see the root README's security/compliance guide) |
| `docs/entities/`, `docs/process-flows/`, `docs/system-overview.md`, `docs/gap-register.md` | Written directly, from `mfdoc brief --entity`/`--system` output — the interactive Claude Code path these four doc types are designed for (no automated CLI path exists for them) | Same as above — hand/session-produced, CI only validates they haven't drifted |

CI's `test` job runs `mfdoc validate`/`mfdoc test-validate` against this whole
tree on every push and pull request, so a citation that stops resolving (a
fixture edit that shifts line numbers, for instance) fails the build rather
than silently going stale.

## Reproduce it yourself

```bash
pip install -e .        # or: PYTHONPATH=src python3 -m mfdoc.cli ...

mfdoc ingest   --config project.yml
mfdoc derive   --config project.yml
mfdoc coverage --config project.yml --json examples/outputs/coverage.json

mfdoc rules-register --config project.yml --out examples/outputs/rules-register.md
mfdoc export         --config project.yml --json examples/outputs/index.json
cp .mfdoc/index.db examples/outputs/index.db

mfdoc test-plan      --config project.yml --out examples/outputs/test-plan-register.md
mfdoc test-advisory  --config project.yml --out examples/outputs/testability-advisory.md

# Module docs and generated tests -- real narrative-pass output, via the local
# claude CLI instead of an API key (needs Claude Code installed and
# authenticated; drop --provider claude-code and add --model/ANTHROPIC_API_KEY
# to use the Anthropic API directly instead):
mfdoc batch      --config project.yml --out examples/outputs/docs --provider claude-code
# --matrix renders every options.testgen.matrix target (python/pytest,
# natural/natunit, mantis/native, silkcentral/testcase, uipath/testcase)
# for every batchable member in one pass:
mfdoc test-batch --config project.yml --out examples/outputs/tests \
                  --matrix --provider claude-code

mfdoc validate       --config project.yml --docs examples/outputs
mfdoc test-validate  --config project.yml --docs examples/outputs/tests
```

`docs/entities/`, `docs/process-flows/`, `docs/system-overview.md` and
`docs/gap-register.md` aren't reproduced by a single command — write them the
way `SKILL.md` describes, from `mfdoc brief --entity NAME`/`--system` output,
following `reference/writing-rules.md`. The gap register's one "Discrepancies
found" entry (`MILL-CERT` vs. the DDL's `MILL_CERT`) is a good illustration of
why: it was found by a person cross-referencing two independently-correct
facts no single automated brief puts side by side, not by any tool.
