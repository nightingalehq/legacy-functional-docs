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

## Installing as a Claude Code skill

Clone or copy this repository's contents into a `legacy-functional-docs/`
directory under your Claude Code skills path (e.g. `~/.claude/skills/` for a
personal install, or `.claude/skills/` at the root of a project for a
project-scoped one). Claude Code discovers the skill from its `SKILL.md`
front matter; no separate registration step is required. The Python pipeline
under `src/mfdoc/` runs locally wherever Claude Code invokes shell commands;
run `pip install -e .` from the skill directory once to get the `mfdoc`
console script on `PATH` — see Requirements below.

## Quick start

```bash
pip install -e .

cp config/project.example.yml project.yml
# edit source paths, pin the dialect for each source set

mfdoc ingest   --config project.yml
mfdoc derive   --config project.yml
mfdoc coverage --config project.yml     # read this before writing anything

mfdoc brief --config project.yml --system
mfdoc brief --config project.yml --module MMP0100
mfdoc brief --config project.yml --entity MILL-ORDER

# ... write documents from the briefs, per reference/writing-rules.md ...

mfdoc validate --config project.yml --docs docs/functional

# smoke-test against the bundled fixtures and worked example:
mfdoc validate --config project.yml --docs examples
```

`mfdoc export --config project.yml --json out/index.json` dumps the whole
fact store for downstream tooling.

## Requirements

Python 3.10+ and PyYAML. No other dependencies, no network access, nothing leaves
the machine.

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
- Natural reporting mode has implicit block scope; nesting reported for
  reporting-mode members is unreliable and flagged as such.
- Dynamic dispatch cannot be resolved from source. Those call graphs are incomplete
  by nature, and that is reported rather than hidden.
- The Mantis and Supra packs need calibration against the target codebase. See
  `reference/mantis-supra.md`; below roughly 85% line recognition, calibrate before
  trusting anything built on the index.

## License

[MIT](LICENSE)
