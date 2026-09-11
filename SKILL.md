---
name: legacy-functional-docs
description: >-
  Build first-draft functional documentation from legacy mainframe 4GL codebases —
  Natural/Adabas, Mantis/Supra — and from mainframe data definitions (Adabas FDT,
  Natural DDM, Supra directory, DB2/SQL DDL, COBOL copybooks, JCL, CICS CSD).
  Produces markdown with YAML front matter where every business rule carries a
  source citation and a confidence flag, plus a gap register that becomes the SME
  interview agenda. Use this skill whenever the user mentions Natural, Adabas,
  DDM, FDT, Mantis, Supra, mainframe modernisation, legacy code archaeology,
  reverse-engineering a 4GL system, documenting an undocumented mainframe
  application, building a CRUD matrix or data lineage from mainframe source, or
  preparing a legacy system for migration, replatforming or handover — even if
  they do not use the word "documentation". Also use it when asked to work out
  what an old mainframe program does, or to extract business rules from 4GL source.
---

# Legacy functional documentation

Turn undocumented mainframe 4GL source into reviewable first-draft functional
documentation. Humans supplement and approve; this skill's job is to get them 80%
of the way there without inventing anything.

## The one rule that matters

**Never assert behaviour that cannot be traced to a specific source line.**

Legacy documentation projects fail in a specific way: the documentation reads
fluently, the business signs it off, and eighteen months later somebody discovers
that a plausible-sounding paragraph described behaviour the code never had. A
confidently wrong document is worse than no document, because it stops people
looking at the code.

So the architecture separates what is *known* from what is *reasoned*:

| Stage | What it does | Trust level of its output |
|---|---|---|
| 0 Ingest | normalise encoding, sequence columns, split members | facts |
| 1 Extract | scan source into a SQLite fact store | facts, with gaps recorded |
| 2 Derive | join facts: call graph, CRUD matrix, transaction scopes | facts |
| 3 Narrate | write prose from a fact brief, citing every claim | claims, flagged |
| 4 Validate | check every citation resolves; check front matter | pass/fail |

Stages 0–2 and 4 are deterministic Python. Stage 3 is the only place judgement
enters, and its inputs are constrained so judgement has less room to drift.

## Workflow

### 1. Establish what you actually have

Before running anything, confirm the inputs, because the commonest failure is
starting work on a partial extract. Ask about anything missing rather than
assuming:

- Natural source: SYSOBJH/Object Handler unload, or per-member listings? Which
  libraries? Are copycode, LDAs, GDAs, PDAs and maps included, or only programs?
- Adabas: DDM listings *and* FDT reports (ADAREP/ADACMP)? Both matter — see
  `reference/natural-adabas.md`.
- Mantis: how was the library exported? Mantis source normally lives inside the
  DBMS, so a filesystem copy has been through a utility whose format varies.
- Supra: directory report, or the live directory? Are linkpaths included?
- Environment: JCL, scheduler definitions, CICS CSD extract, copybooks.

Then say plainly what is missing and what it will cost in coverage. A run without
JCL cannot describe batch process flow; a run without FDTs cannot confirm physical
field semantics.

### 2. Configure and index

Copy `config/project.example.yml`, set the source paths, pin the `dialect` for
each source set rather than relying on sniffing, then:

```bash
mfdoc ingest   --config project.yml
mfdoc derive   --config project.yml
mfdoc coverage --config project.yml
```

### 3. Read the coverage report before writing anything

This is a gate, not a formality. Compare against `options.quality_gates` in the
config. If a gate fails, **stop and fix the input or calibrate the parser** rather
than writing narrative on a weak index.

- `line_recognition_rate` below the gate → the dialect scanner is mismatched to
  this codebase. For Mantis and Supra especially, expect to calibrate; see
  `reference/mantis-supra.md`.
- `call_resolution_rate` low → source is missing. Get it, or scope the
  documentation to what was supplied and say so in every affected document.
- `entity_definition_rate` low → data definitions are missing. Field-level
  meaning cannot be documented; do not guess from field names.
- Many `dynamic_target` gaps → the codebase dispatches through variables. Call
  graphs will be incomplete by nature; this is a finding to report, not a defect
  to hide.
- `min_citation_accuracy_rate`, if configured, is different from the others:
  it's sampling-derived, not computed from facts, so it fails against 0 until
  `mfdoc sample-citations --config project.yml --docs docs/functional --judge
  human` has been run at least once — it shows a random sample of generated
  claims next to their cited source and records a human yes/no verdict on
  whether the source actually supports the claim.

Report these numbers to the user honestly, including when they are poor.

Every `mfdoc coverage`/`mfdoc gate` run appends a timestamped snapshot of
these metrics to the index; on a multi-week engagement, run
`mfdoc coverage --config project.yml --history` to see the trend across
runs instead of only ever seeing the latest one.

### 4. Generate briefs and write

Module docs are high-volume and formulaic — one program or subprogram, one
document, following `templates/module.md` mechanically. System overview,
process flows and the gap register are low-volume and judgement-heavy —
grouping, narrative structure, and deciding what's worth asking an SME
depend on holding the whole system in mind at once. Route each accordingly:
batch the module docs, write the rest interactively.

**Module docs — `mfdoc batch`.** At a realistic engagement size (thousands
of Natural members), writing one module document per chat turn does not
scale on time or cost. `mfdoc batch` runs brief → model call → write →
validate → retry once per module, unattended:

```bash
mfdoc batch --config project.yml --out docs/functional/modules
```

It only ever touches `natural`/`mantis` members with `object_type` in
`program`/`subprogram`/`subroutine`/`copycode` — never system overview,
data entities, process flows or the gap register. It's resumable (a run
over thousands of members will be interrupted; unchanged members are
skipped on re-run) and reports token counts, a cost figure (once
`options.narrative.pricing` is set in project config), and a retry count.
Needs `pip install 'mfdoc[batch]'` and `ANTHROPIC_API_KEY` set. See
`--help` for concurrency and resume-state options.

**Everything else — brief + write here, interactively:**

```bash
mfdoc brief --config project.yml --entity MILL-ORDER
mfdoc brief --config project.yml --system
mfdoc brief --config project.yml --interface-matrix
```

Write from the brief. Read `reference/writing-rules.md` before the first
document — it defines the citation format, the confidence taxonomy, and the
specific prose failures to avoid. Use the templates in `templates/`.

After writing each document in this section, run `mfdoc clean-doc --file
PATH` on it before moving on. These documents are written directly here
(not through `mfdoc batch`/`test-batch`'s write site), so they never get
that machinery's automatic strip of a leaked lead-in sentence ("I'll
write..."/"Here is the corrected document:") or wrapping code fence ahead
of the YAML front matter (issue #150/#152) — `clean-doc` is the same fix,
run explicitly. Cheap and a no-op when the file is already clean, so it's
safe to run on every document from this section as a matter of course.

Check whether this project has an SME notes file (`options.sme_notes` in
`project.yml`, `sme-notes.md` by default — see `README.md`'s "SME notes"
section). `mfdoc brief --entity`/`--module`/`--executive` already fold a
matching note into the brief text for you, but `--system` and
`--interface-matrix` don't (that wiring is scoped to module/entity/
executive/test briefs only) — so when writing `system-overview.md`,
`processes/*.md`, or `gap-register.md` from the system brief, read
`sme-notes.md` yourself (general section plus anything scoped to a member
you're covering) and weigh it the same way: advisory context only, never a
substitute for a `[[MEMBER:LINE]]` citation, and never something that
overrides a generated fact.

Suggested document set, in this order (each builds vocabulary the next needs):

1. `system-overview.md` — from the system brief
2. `data/<entity>.md` — one per data store
3. `modules/<module>.md` — one per program or subprogram (batched, above)
4. `processes/<process>.md` — batch job or online transaction end-to-end
5. `interface-matrix.md` — the screen-and-key interface matrix (mode x
   panel x map x PF-label x routine x outcome), from `mfdoc brief
   --interface-matrix` against `templates/interface-matrix.md`. Whole-
   system, not one per module: the brief gathers each screen's display
   reference(s) and the PF-key/dispatch branches found in the same
   modules that display it, so the matrix is built from the screen's own
   display context rather than from a member-local note. The brief hands
   over which module(s) display each screen, its PF-key/dispatch
   branches (routine called, fields set), and any literal on-screen text
   as candidate PF-key labels — matching a label to a specific key, and
   characterising the outcome (exit/navigate/error/...), is the judgement
   call to make when writing this document; never invent a match or an
   outcome the cited facts don't evidence. Each branch row is tagged with
   a `mechanism` (PF-key dispatch vs. mode/panel dispatch, when a project
   also configures `options.overview.mode_field_pattern`) — carry that tag
   into the document as its own column rather than merging the two into
   one undifferentiated "dispatch" fact (issue #129).
6. `gap-register.md` — every unresolved item, as SME questions
7. `coverage-report.md` — the numbers, unspun
8. `reference/language-guide.md` — what this dialect's source actually
   looks like in this codebase, useful for Mantis/Supra especially since
   they have no public documentation. Run the basic (deterministic) tier
   first: `mfdoc lang-guide --config project.yml --dialect mantis --out
   docs/functional/reference/language-guide.md` — every keyword, count,
   and cited example, no model call. Then, optionally, write the
   narrative tier from `templates/language-guide.md`: take that basic
   output as the cited fact source and add a short paragraph of
   connective prose per section about how constructs combine into
   idioms in this codebase — same citation discipline as
   `system-overview.md`, never asserting a pattern the basic tier's
   table doesn't already cite.
9. `executive-summary.md` — one page per program, for a reviewer who
   won't read the per-module docs. From `mfdoc brief --executive NAME`
   (or `brief.executive_brief()` directly — same fact-brief-then-write
   pattern as the others) against `templates/executive-summary.md`.
   **Run `mfdoc classify-rules` first**: `executive_brief()`'s "Top
   rules" section joins against `rule_theme`, so without a prior
   `classify-rules` run every rule shows as `uncategorized`. Its "Risk"
   section reads `structural._complexity_rows()` directly against the
   live fact store (the same underlying data `mfdoc complexity` renders,
   not that command's output) — running `mfdoc call-graph`/`mfdoc
   complexity` beforehand is not required and has no effect on this
   brief's numbers; they're computed fresh either way.

Standalone files are the supported default for `mfdoc gap-summary` and
`mfdoc call-graph` (see `options.overview` in project.yml) — that's what
each of those commands produces, and what `mfdoc validate` checks for
consistency against the fact store. For a single-document handoff, the
gap-summary and call-graph content may optionally be pasted into
`system-overview.md`, above or alongside its narrative, instead of
shipping them as separate files — but that's a presentation choice made
after generation, not a change to how they're produced.

### 5. Validate

```bash
mfdoc validate --config project.yml --docs docs/functional
```

This fails the build on any citation that does not resolve to a real member and
line, on missing front-matter keys, and on assertive sentences carrying neither a
citation nor a hedge. Fix every failure. Do not relax the validator to make a
document pass.

### 6. Hand over for review

The gap register is the deliverable the humans will use most. Order it by what
blocks the most documentation, phrase each item as a question a domain expert can
answer without reading code, and never pad it with items the tooling could have
resolved itself.

### 7. Optional: draft tests from the same facts

Once module docs exist (or even before, for the deterministic stages), the
same fact store can draft first-draft tests for a migration team, in the
legacy source's own dialect or a destination language — still docs-only,
still cited, still nothing invented:

```bash
mfdoc test-plan     --config project.yml   # deterministic; branch/parameter/CRUD facts -> test_case rows
mfdoc test-advisory --config project.yml   # deterministic; names what to mock, suggests refactor seams
mfdoc test-overlay-draft --config project.yml --out test-overlay.yml   # model proposes bug-vs-spec splits, review_status: draft only
mfdoc test-gen   --config project.yml --member NAME --language python --framework pytest
mfdoc test-batch --config project.yml --language python --framework pytest --out tests_generated
mfdoc test-validate --config project.yml --docs tests_generated
```

`test-plan`/`test-advisory` are model-free, same as `derive`. `test-gen`/
`test-batch` are the narrate stage for tests (Option C: batch, formulaic,
one member's worth of judgement at a time) and need `mfdoc[batch]`, same as
`mfdoc batch`. A `test-overlay.yml` entry only changes a scenario's
characterization/spec/bug status once a human moves its `review_status`
past `draft` — an unreviewed model proposal never does. See
`reference/test-writing-rules.md` and
`docs/guides/testing-strategies-for-mainframes-and-4gl.md` for what these
tests are for and how to explain the concept to a team that has never had
an automated test suite for this code.

## Reference material

Read the relevant file before working in that dialect — each contains the
statement forms, listing layouts and traps that the scanners rely on:

- `reference/natural-adabas.md` — Natural statements, structured vs reporting
  mode, DDM/FDT layouts, the DDM-versus-FDT distinction
- `reference/mantis-supra.md` — Mantis constructs, Supra DML function codes,
  directory structure, and **how to calibrate** these scanners
- `reference/environment.md` — JCL, CICS CSD, copybooks, Natural batch stacks
- `reference/writing-rules.md` — citation format, confidence taxonomy, prose rules
- `reference/test-writing-rules.md` — the same contract, extended to generated tests
- `reference/adding-a-dialect.md` — the extractor contract, for IDMS, IMS, ADSO,
  RPG or anything else that turns up

## Tooling map

```
src/mfdoc/
  db.py               schema + fact-store helpers
  normalise.py        encoding, sequence columns, member splitting, dialect sniffing
  graph.py            derivation: resolution, CRUD matrix, orphans, transaction scopes
  brief.py            fact briefs (module / entity / system / interface-matrix) and JSON export
  sme_notes.py        parses the optional options.sme_notes file, folds it into briefs
  batch.py            mfdoc batch's harness: brief -> model call -> write -> validate -> retry, resumable
  classify.py         mfdoc classify-rules: assigns each rule_candidate a theme (keyword/llm/structural)
  structural.py       deterministic overview renderers: gap-summary, data-flow, call-graph,
                      complexity, rules-theme-register, glossary, lang-guide
  testplan.py         test-case derivation + test-plan register (model-free)
  testadvisor.py       testability classification + refactor-seam advisory (model-free)
  testoverlay.py       bug-vs-spec curation overlay (model-drafted, human-promoted)
  testbatch.py         test render stage (batch.py's harness, for tests)
  validate.py         citation and front-matter validation (validate_test_doc for tests)
  cli.py              command line
  dialects/
    natural.py        Natural scanner  (reference implementation)
    mantis.py         Mantis scanner   (calibration expected)
    adabas.py         FDT + DDM parsers
    supra.py          Supra directory parser (calibration expected)
    environment.py    SQL DDL, COBOL copybook, JCL, CICS CSD
```

Everything writes to `.mfdoc/index.db`. Commit `project.yml` and the generated
docs; the index can be rebuilt from source at any time and does not need to be in
version control.

## Honest limitations

State these to the user rather than letting them be discovered later:

- The scanners are heuristic line-and-clause matchers, not grammars. They are
  built to flag what they cannot parse; they are not built to be complete.
- Natural reporting mode has implicit block scope. `LOOP` nesting is inferred
  from indentation when every `LOOP`'s body in the member is unambiguously
  more indented than the `LOOP` line itself (recorded as a rule with
  `confidence='inferred'`, still worth SME confirmation); when it isn't,
  nesting is left unresolved and flagged high-severity, as before this
  inference existed.
- Dynamic dispatch (`CALLNAT #VAR`, `FETCH #PGM`, Mantis `CALL` on a variable)
  cannot be resolved from source. Those call graphs are incomplete by nature.
- `UPDATE`/`DELETE` referencing a processing-loop label rather than a view
  resolve to that loop's target for the conventional `R#`/`F#`/`H#` label
  naming; any other label naming is flagged `unresolved` rather than guessed.
- The Mantis and Supra packs are a defensible starting point, not a validated
  grammar. Treat a first-run recognition rate below 85% as a calibration task.
- Copybook `PIC` to format conversion handles common cases, not every
  `REDEFINES` / `OCCURS DEPENDING ON` shape.
