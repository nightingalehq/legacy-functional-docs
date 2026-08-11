# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Claude Code skill (`SKILL.md`) that turns undocumented mainframe 4GL source
(Natural/Adabas, Mantis/Supra) plus surrounding definitions (Adabas FDT,
Natural DDM, Supra directory, DB2/SQL DDL, COBOL copybooks, JCL, CICS CSD)
into reviewable first-draft functional documentation. Every business rule in
the output carries a `[[MEMBER:LINE]]` citation back to source; unknowns
become gap-register questions rather than invented prose.

The load-bearing design decision: fact extraction (`mfdoc ingest`/`derive`,
deterministic Python, no model calls) is separated from narrative writing
(the only stage where an LLM is involved, and only from a pre-cited fact
brief — never from raw source). Read `README.md`'s "Why two stages" section
and `docs/guides/architecture.md` before changing pipeline behaviour; they
explain *why* the stages are split this way, which matters more here than in
most repos when deciding where a fix belongs.

## Commands

```bash
pip install -e '.[dev]'          # dev install (adds pytest)
pytest                           # full test suite
pytest tests/test_natural_rules.py -v          # single test file
pytest tests/test_natural_rules.py::test_x -v  # single test

# pipeline, in order, against a project config:
mfdoc ingest    --config project.yml
mfdoc derive    --config project.yml
mfdoc coverage  --config project.yml     # read before writing any docs
mfdoc gate      --config project.yml     # pass/fail vs options.quality_gates
mfdoc calibrate --config project.yml --dialect mantis   # Mantis/Supra usually need this
mfdoc brief     --config project.yml --system|--module NAME|--entity NAME
mfdoc batch     --config project.yml --out docs/functional/modules  # needs mfdoc[batch] + ANTHROPIC_API_KEY
mfdoc rules-register --config project.yml --out docs/functional/rules-register.md
mfdoc validate  --config project.yml --docs docs/functional
mfdoc export    --config project.yml --json out/index.json

# optional: draft tests from the same fact store, in the source dialect or a
# destination language -- see docs/guides/testing-strategies-for-mainframes-and-4gl.md
# --overlay/--language/--framework/--out below default to options.testgen in
# project.yml (overlay_path/default_language/default_framework/out_dir) --
# the flags shown here are overrides, not requirements, once that's set.
mfdoc test-plan     --config project.yml --overlay test-overlay.yml
mfdoc test-advisory --config project.yml
mfdoc test-overlay-draft --config project.yml --out test-overlay.yml    # needs mfdoc[batch]
mfdoc test-gen      --config project.yml --member NAME --language python --framework pytest
mfdoc test-batch    --config project.yml --language python --framework pytest --out tests_generated  # needs mfdoc[batch]
mfdoc test-validate --config project.yml --docs tests_generated
```

Before pushing any change touching extraction or narrative, also run the
pipeline against the bundled fixtures — this is what CI checks beyond unit
tests, and it catches citation-alignment regressions unit tests won't:

```bash
mfdoc ingest   --config project.yml
mfdoc derive   --config project.yml
mfdoc coverage --config project.yml
mfdoc validate --config project.yml --docs examples
```

(`mfdoc gate` against the checked-in `project.yml` is *expected* to fail —
its quality gates are set to real-engagement thresholds and the fixtures
deliberately omit some call targets to exercise the gap machinery. Gate
logic itself is covered by `tests/test_gate.py` against controlled
thresholds, not by this run.)

## Architecture

Full detail in `docs/guides/architecture.md`; summary of what you need to
orient before editing:

```
source ─▶ [0 Ingest: normalise.py] ─▶ [1 Extract: dialects/*.py] ─▶ [2 Derive: graph.py] ─▶ SQLite fact store
                                                                                                   │
                                                                                        [brief.py, + redact.py]
                                                                                                   │
                                                                          [3 Narrate: batch.py+anthropic_caller.py,
                                                                           or interactive via SKILL.md]
                                                                                                   │
                                                                                          markdown documents
                                                                                                   │
                                                                                    [4 Validate: validate.py] ─▶ pass/fail
```

- **Dialects** (`src/mfdoc/dialects/`): one module per source kind, routed by
  `DIALECT_ROUTER` in `cli.py`. `natural.py` is the reference implementation
  (most mature); `mantis.py`/`supra.py` need calibration on new codebases;
  `environment.py` covers `sql_ddl`/`cobol_copybook`/`jcl`/`cics_csd`. Each
  scanner is a heuristic regex matcher, not a grammar-based parser — it's
  built to record what it can't parse as an `unparsed_line` gap, never to
  silently skip or guess.
- **Fact store**: one SQLite file (`.mfdoc/index.db` by default, gitignored,
  rebuildable from source). Schema in `db.py`. It holds every ingested
  source line unredacted — a security-relevant artefact in its own right
  (see `docs/guides/security-and-compliance.md`).
- **Derive** (`graph.py`): joins raw facts into call graphs, CRUD matrices,
  transaction scopes, and the `coverage()` numbers `mfdoc gate` checks.
- **Brief generation** (`brief.py`): the only input the narrative stage
  sees — plain text, every line already cited. `redact.py` runs here, before
  anything is written to a file or sent anywhere.
- **Narrate**: two deliberately separate paths — `mfdoc batch` (`batch.py`)
  for high-volume formulaic module docs via a swappable `ModelCaller`
  (`anthropic_caller.py` for real calls, a `fake-echo` caller for
  network-free tests); the interactive Claude Code path (`SKILL.md`) for
  system overview, entity docs, process flows, and the gap register, where
  judgement about grouping benefits from a session holding the whole system
  in mind.
- **Validate** (`validate.py`): the only stage that reads generated
  documents back in. Resolves every `[[MEMBER:LINE]]` citation against the
  fact store and enforces that every assertive sentence is either cited or
  explicitly hedged (`inferred`, `unresolved`, etc.). `validate_test_doc`
  additionally checks a generated test file's `MEMBER:BR-nnn` references
  against real `test_case` rows.
- **Test generation** (optional, same pipeline discipline applied to tests
  instead of prose — see `docs/guides/testing-strategies-for-mainframes-and-4gl.md`):
  `testplan.py` derives `test_case` rows (Given/When/Then, cited, no
  invented expected value) from `rule_candidate`/`variable`/`data_access`
  facts, model-free; `testadvisor.py` classifies each unit for
  mockability/integration-only and names refactor seams, model-free;
  `testoverlay.py` is where an LLM may *propose* (never confirm) a
  scenario's bug-vs-spec status via `test-overlay.yml`, gated on a human
  moving `review_status` past `draft`; `testbatch.py` is the narrate stage
  for tests, reusing `batch.py`'s `ModelCaller`/retry/resumable-state
  machinery verbatim.

Two rules that matter most when touching a dialect scanner (full contract in
`reference/adding-a-dialect.md`):

1. A line not inserted into `source_line` can never be cited — a gap there
   is a silent hole in every downstream document.
2. Mask literals before keyword-matching, then recover the original text by
   offset (see `natural.mask_literals`/`natural.orig`). Storing the masked
   form loses exactly the values the documentation exists to report.

Before "simplifying" anything in `mantis.py`/`supra.py` that looks redundant
or overly specific, check `tests/test_call_graph_and_entities.py` and
`tests/test_citation_alignment.py` — several such patterns are fixes for
real, previously-shipped defects, not accidental cruft.

## Style and dependency discipline

- Python ≥3.10 syntax throughout (`X | Y` unions, walrus). Don't introduce
  3.11+-only syntax without checking `pyproject.toml`'s `requires-python`.
- The core pipeline's only runtime dependency is PyYAML; everything else is
  stdlib, on purpose — this needs to run in client environments with
  restricted or no egress. `anthropic` is an optional extra (`mfdoc[batch]`
  or `mfdoc[vertex]`), imported lazily, isolated in its own module
  (`anthropic_caller.py`/`vertex_caller.py`) — follow that pattern for any
  new dependency rather than adding one unconditionally.
- Don't add default redaction patterns, default dialect assumptions, or any
  other built-in guess at what a specific client's source contains. Flagging
  what's unknown rather than inventing a plausible answer is the product's
  entire value proposition, and that applies to the tool's own config
  surface too.
- Fixtures under `examples/inputs/` are golden and exercise specific known
  defect classes — prefer extending an existing fixture over adding a new
  one.

## Further reading

- `docs/guides/architecture.md` — full stage-by-stage architecture
- `docs/guides/extending.md` — adding a dialect, document type, or CLI command
- `docs/guides/security-and-compliance.md` — data handling, redaction, what leaves the machine
- `docs/guides/testing-strategies-for-mainframes-and-4gl.md` — what the generated
  tests are for, modern testing concepts explained for a 4GL/mainframe audience,
  and how to introduce this to a team with no prior automated-test culture
- `reference/adding-a-dialect.md`, `reference/writing-rules.md`, `reference/mantis-supra.md`
- `reference/test-writing-rules.md` — the writing-rules contract extended to generated tests
- `docs/plans/legacy-functional-docs-plan.md` — working backlog and design-decision record (its progress log is more current than the narrative below it)
