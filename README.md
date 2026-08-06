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
templates/            the seven document types
src/mfdoc/            the extraction pipeline
tests/                pytest suite (fixtures as golden tests)
examples/             worked example + multi-dialect fixtures
evals/                eval prompts (dev-time only; not installed)
docs/guides/          getting-started, architecture, security/compliance, extending
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
