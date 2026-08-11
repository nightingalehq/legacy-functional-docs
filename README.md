# legacy-functional-docs

A Claude Code skill that builds first-draft functional documentation from legacy
mainframe 4GL codebases — Natural/Adabas and Mantis/Supra — plus the surrounding
data definitions and orchestration (Adabas FDT, Natural DDM, Supra directory,
DB2/SQL DDL, COBOL copybooks, JCL, CICS CSD).

Output is markdown with YAML front matter. Every business rule carries a
`[[MEMBER:LINE]]` citation and a confidence flag, and every unknown becomes an item
in a gap register phrased as a question for a domain expert. Humans supplement and
approve; the skill's job is to get them most of the way there without inventing
anything.

## Capabilities

### What it reads

- **Natural/Adabas** — Natural source (programs, subprograms, subroutines,
  copycode, maps), Adabas DDM listings and FDT reports (ADAREP/ADACMP) for
  the same physical files.
- **Mantis/Supra** — Mantis source and Supra directory reports/linkpaths.
  Calibration against the target codebase is expected (`mfdoc calibrate`,
  `reference/mantis-supra.md`).
- **Surrounding orchestration and data definitions** — DB2/SQL DDL, COBOL
  copybooks, JCL (including embedded SQL), and CICS CSD extracts.
- Mainframe-specific input handling: EBCDIC code pages (`cp037`/`cp500` etc.),
  sequence-number columns, and splitting one exported listing into many
  logical members.

### What it derives

From the extracted facts, deterministically: a call graph with resolved and
unresolved (missing-source or dynamic-target) targets, a CRUD matrix, Adabas
coupling/Supra linkpaths as entity relationships, transaction scopes, orphan
detection, and coverage metrics (`line_recognition_rate`,
`call_resolution_rate`, `entity_definition_rate`, gap counts by severity).
`mfdoc gate` checks these against configurable thresholds before anything is
written.

### What it produces

Seven markdown document types (`templates/`): system overview, module docs,
data entity docs, process flows, a CRUD/coverage report, and a gap register
phrased as SME interview questions. Every business rule carries a
`[[MEMBER:LINE]]` citation and a confidence flag; `mfdoc validate` fails the
build on any citation that doesn't resolve or any uncited, unhedged
assertion. A flat `rules-register` indexes every `MEMBER:BR-nnn` rule ID
across the whole doc set. `mfdoc export --json` dumps the full fact store for
downstream tooling.

### How narrative gets written

Two paths, chosen per document type: `mfdoc batch` generates high-volume
module docs unattended via a pluggable model caller — direct Anthropic API
or Claude on Vertex AI (`--provider vertex`) — with a `fake-echo` caller for
network-free dry runs; or the interactive Claude Code path for documents
that benefit from a session holding the whole system in mind (system
overview, entity docs, process flows, gap register).

### Test generation (optional)

The same fact store can draft first-draft tests for a migration team — in
the legacy dialect or a destination language — with the same discipline as
the docs: `mfdoc test-plan` derives cited test scenarios (Given/When/Then)
from branch/parameter/CRUD facts, model-free; `mfdoc test-advisory` names
what each unit needs mocked and suggests refactor seams, model-free;
`mfdoc test-overlay-draft` lets a model *propose* a bug-vs-spec split,
which only takes effect once a human promotes it past `review_status:
draft`; `mfdoc test-gen`/`mfdoc test-batch` render the scenarios into
`language`/`framework`-specific test code (still Markdown + citations, so
`mfdoc test-validate` can check them the same way `mfdoc validate` checks
docs). See
[`docs/guides/testing-strategies-for-mainframes-and-4gl.md`](docs/guides/testing-strategies-for-mainframes-and-4gl.md).

### Data handling

Redaction (`mfdoc brief`, before anything is written or sent anywhere), a
gitignored local fact store, and a documented default posture of no network
access except the two model-calling paths above. See
[`docs/guides/security-and-compliance.md`](docs/guides/security-and-compliance.md).

## Why two stages

An LLM reading raw 4GL source will produce fluent documentation containing business
rules the code does not have. It happens because the model has to hold thousands of
lines in mind at once and the gaps get filled plausibly rather than accurately. A
confidently wrong document is worse than none, because it stops people reading the
code.

So extraction is deterministic Python into a SQLite fact store, and the narrative
pass writes only from a generated fact brief in which every line already carries a
citation. If a fact is not in the brief there is nothing to cite, and the writing
rules require the claim to be dropped or marked `unresolved`. A validator then
re-checks every citation against the index and fails the build on any that does not
resolve.

## Documentation

- **New to Claude Code, Python, or mainframe 4GLs?** Start with
  [`docs/guides/getting-started.md`](docs/guides/getting-started.md) — a
  no-assumed-background walkthrough of what this is and how to run it.
- **Architecture overview** — [`docs/guides/architecture.md`](docs/guides/architecture.md)
  covers the pipeline stage by stage, the data model, and where a model can
  and can't reach.
- **Security, data handling and compliance due diligence** —
  [`docs/guides/security-and-compliance.md`](docs/guides/security-and-compliance.md)
  covers what leaves the machine, redaction, credentials found in source,
  the fact-store's status as a security artefact, and an engagement
  checklist.
- **Extending the tool** — [`docs/guides/extending.md`](docs/guides/extending.md)
  is the developer guide for adding a dialect, a document type, or a CLI
  command.
- **Test generation** —
  [`docs/guides/testing-strategies-for-mainframes-and-4gl.md`](docs/guides/testing-strategies-for-mainframes-and-4gl.md)
  introduces modern testing concepts for a mainframe/4GL audience and
  explains what the generated tests are for.

## Installing as a Claude Code skill

**New to git, GitHub, or the command line?** See
[`docs/guides/getting-started.md`](docs/guides/getting-started.md) first —
it covers downloading this repository onto your machine (with or without
git) and setting up a Python virtual environment, step by step. The summary
below assumes you've already got the files locally.

Copy or clone this repository's contents into a `legacy-functional-docs/`
directory under your Claude Code skills path (e.g. `~/.claude/skills/` for a
personal install, or `.claude/skills/` at the root of a project for a
project-scoped one). Claude Code discovers the skill from its `SKILL.md`
front matter; no separate registration step is required. The Python pipeline
under `src/mfdoc/` runs locally wherever Claude Code invokes shell commands;
run `pip install -e .` from the skill directory once to get the `mfdoc`
console script on `PATH` — see Requirements below.

## Quick start

```bash
# optional: isolate dependencies in a virtual environment first
# python3 -m venv .venv && source .venv/bin/activate   (Windows: .venv\Scripts\Activate.ps1)

pip install -e .

cp config/project.example.yml project.yml
# edit source paths, pin the dialect for each source set

mfdoc ingest   --config project.yml
mfdoc derive   --config project.yml
mfdoc coverage --config project.yml     # read this before writing anything
mfdoc gate     --config project.yml     # pass/fail check against options.quality_gates

# Mantis and Supra usually need this before the gate passes -- see
# reference/mantis-supra.md:
mfdoc calibrate --config project.yml --dialect mantis

mfdoc brief --config project.yml --system
mfdoc brief --config project.yml --module MMP0100
mfdoc brief --config project.yml --entity MILL-ORDER

# ... write documents from the briefs, per reference/writing-rules.md ...
# module docs are high-volume and formulaic; batch them instead of writing
# one at a time (needs `pip install 'mfdoc[batch]'` and ANTHROPIC_API_KEY):
mfdoc batch --config project.yml --out docs/functional/modules
# output nests as <out>/<dialect>/<library>/<member>.md (library segment
# omitted when the member has none), mirroring the only two source-grouping
# facts actually on record for a member -- e.g.
# docs/functional/modules/natural/MILLPROD/MMP0100.md

mfdoc rules-register --config project.yml --out docs/functional/rules-register.md
# a flat, greppable index of every MEMBER:BR-nnn rule ID -- look one up here
# without already knowing which module doc it lives in; regenerate any time,
# byte-identical output against unchanged source

mfdoc validate --config project.yml --docs docs/functional

# smoke-test against the bundled fixtures and worked example:
mfdoc validate --config project.yml --docs examples
```

`mfdoc export --config project.yml --json out/index.json` dumps the whole
fact store for downstream tooling.

Optional: draft tests from the same fact store (see
[`docs/guides/testing-strategies-for-mainframes-and-4gl.md`](docs/guides/testing-strategies-for-mainframes-and-4gl.md)).
Set `options.testgen` in `project.yml` (`default_language`, `default_framework`,
`overlay_path`, `out_dir`) once and the flags below become optional overrides:

```bash
mfdoc test-plan     --config project.yml
mfdoc test-advisory --config project.yml
mfdoc test-gen      --config project.yml --member MMP0100 --language python --framework pytest
# output nests as <out_dir>/<dialect>/<library>/<language>/<framework>/<member>.md,
# same convention as `mfdoc batch` above -- e.g.
# tests_generated/natural/MILLPROD/python/pytest/MMP0100.md
mfdoc test-validate --config project.yml --docs tests_generated
```

## Worked examples

`examples/` holds real output from running the whole pipeline against the
bundled fixtures in `examples/inputs/` — not hand-crafted mockups. Every
citation in every file below resolves against the fact store you get by
following the reproduction steps, which is also what CI checks
(`mfdoc validate --docs examples`).

| File | What it shows |
|---|---|
| [`MMP0100-worked-example.md`](examples/MMP0100-worked-example.md), [`MMB0100-worked-example.md`](examples/MMB0100-worked-example.md), [`ORDERMST-worked-example.md`](examples/ORDERMST-worked-example.md) | Full module docs (`mfdoc brief` + narrative pass), including how a missing callee or an ambiguous transaction boundary becomes a gap-register question instead of invented prose. |
| [`test-plan-register-example.md`](examples/test-plan-register-example.md) | `mfdoc test-plan`'s system-wide register — every derived Given/When/Then scenario, keyed by the same `MEMBER:BR-nnn` id the module docs and rules register use. |
| [`testability-advisory-example.md`](examples/testability-advisory-example.md) | `mfdoc test-advisory`'s output — every batchable member classified pure / needs-mocks / integration-only / blocked, with named seams and gaps, no model call involved. |
| [`tests_generated/MMP0100-test-example.md`](examples/tests_generated/MMP0100-test-example.md) | A rendered test file (`mfdoc test-gen` shape) for MMP0100: one test per branch scenario, an `unresolved` scenario left as a skipped test rather than a guessed assertion, and two opaque call-stubs for callees with no supplied source. |

Reproduce all of it from a clean checkout:

```bash
pip install -e .        # or: PYTHONPATH=src python3 -m mfdoc.cli ...

mfdoc ingest    --config project.yml
mfdoc derive    --config project.yml
mfdoc coverage  --config project.yml
mfdoc validate  --config project.yml --docs examples   # 0 invalid citations

mfdoc test-plan     --config project.yml
mfdoc test-advisory --config project.yml --out /tmp/testability-advisory.md
# test-validate enforces language/framework front matter on every doc it
# scans, so point it only at the generated-test subtree, not all of examples/:
mfdoc test-validate --config project.yml --docs examples/tests_generated

# system-wide test-plan register, same shape as test-plan-register-example.md
python3 -c "
import sqlite3
from mfdoc import testplan
conn = sqlite3.connect('.mfdoc/index.db')
conn.row_factory = sqlite3.Row
print(testplan.test_plan_register(conn))
"
```

`MMP0100-test-example.md`'s code was hand-assembled from `mfdoc test-plan
--member MMP0100`'s brief rather than rendered by `mfdoc test-batch`,
because rendering needs `mfdoc[batch]` and a live model call — everything
it asserts is still traceable to the same brief you get by running
`mfdoc test-plan` yourself and reading its output for `MMP0100`.

## Requirements

Python 3.10+ and PyYAML. No other dependencies, no network access, nothing leaves
the machine -- with one exception: `mfdoc batch` (below) sends briefs to the
Claude API, needs `pip install 'mfdoc[batch]'` and `ANTHROPIC_API_KEY`, and is
entirely optional.

## Layout

```
SKILL.md              the agent definition and workflow
pyproject.toml        packaging; installs the `mfdoc` console script
config/               example project configuration
reference/            dialect packs and writing rules — read before use
templates/            the seven document types, plus templates/tests/ for generated tests
src/mfdoc/            the extraction pipeline (+ testplan/testadvisor/testoverlay/testbatch)
tests/                pytest suite (fixtures as golden tests)
examples/             worked examples (docs + test generation) + multi-dialect fixtures
evals/                eval prompts (dev-time only; not installed)
docs/guides/          getting-started, architecture, security/compliance, extending,
                      testing-strategies-for-mainframes-and-4gl
docs/plans/           working backlog and design-decision record
```

## Coverage gates

`options.quality_gates` in the config sets thresholds the run should clear before
narrative is written. Against the shipped fixtures the pipeline achieves a
`line_recognition_rate` of 0.996 with citation line alignment verified against
source for every member. `call_resolution_rate` is deliberately low on the fixtures
because three called modules are intentionally absent, which exercises the gap
machinery.

## Known limitations

Stated up front rather than discovered later:

- The scanners are heuristic line-and-clause matchers, not grammars. They are built
  to flag what they cannot parse, not to be complete.
- Natural reporting mode has implicit block scope. `LOOP` nesting is inferred
  from indentation when it's unambiguous (recorded with `confidence='inferred'`,
  still flagged for SME confirmation); when indentation doesn't clearly support
  it, nesting is left unresolved and flagged high-severity, same as before.
- Dynamic dispatch cannot be resolved from source. Those call graphs are incomplete
  by nature, and that is reported rather than hidden.
- The Mantis and Supra packs need calibration against the target codebase. See
  `reference/mantis-supra.md`; below roughly 85% line recognition, calibrate before
  trusting anything built on the index.

## License

[MIT](LICENSE)
