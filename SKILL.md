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
export PYTHONPATH=scripts
python -m mfdoc ingest   --config project.yml
python -m mfdoc derive   --config project.yml
python -m mfdoc coverage --config project.yml
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

Report these numbers to the user honestly, including when they are poor.

### 4. Generate briefs and write

For each unit of documentation:

```bash
export PYTHONPATH=scripts
python -m mfdoc brief --config project.yml --module MMP0100 --out .mfdoc/briefs/MMP0100.md
python -m mfdoc brief --config project.yml --entity MILL-ORDER
python -m mfdoc brief --config project.yml --system
```

Write from the brief. Read `reference/writing-rules.md` before the first
document — it defines the citation format, the confidence taxonomy, and the
specific prose failures to avoid. Use the templates in `templates/`.

Suggested document set, in this order (each builds vocabulary the next needs):

1. `system-overview.md` — from the system brief
2. `data/<entity>.md` — one per data store
3. `modules/<module>.md` — one per program or subprogram
4. `processes/<process>.md` — batch job or online transaction end-to-end
5. `gap-register.md` — every unresolved item, as SME questions
6. `coverage-report.md` — the numbers, unspun

### 5. Validate

```bash
export PYTHONPATH=scripts
python -m mfdoc validate --config project.yml --docs docs/functional
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

## Reference material

Read the relevant file before working in that dialect — each contains the
statement forms, listing layouts and traps that the scanners rely on:

- `reference/natural-adabas.md` — Natural statements, structured vs reporting
  mode, DDM/FDT layouts, the DDM-versus-FDT distinction
- `reference/mantis-supra.md` — Mantis constructs, Supra DML function codes,
  directory structure, and **how to calibrate** these scanners
- `reference/environment.md` — JCL, CICS CSD, copybooks, Natural batch stacks
- `reference/writing-rules.md` — citation format, confidence taxonomy, prose rules
- `reference/adding-a-dialect.md` — the extractor contract, for IDMS, IMS, ADSO,
  RPG or anything else that turns up

## Tooling map

```
scripts/mfdoc/
  db.py               schema + fact-store helpers
  normalise.py        encoding, sequence columns, member splitting, dialect sniffing
  graph.py            derivation: resolution, CRUD matrix, orphans, transaction scopes
  brief.py            fact briefs (module / entity / system) and JSON export
  validate.py         citation and front-matter validation
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
- Natural reporting mode has implicit block scope. Loop and condition nesting
  reported for reporting-mode members is unreliable and is flagged as such.
- Dynamic dispatch (`CALLNAT #VAR`, `FETCH #PGM`, Mantis `CALL` on a variable)
  cannot be resolved from source. Those call graphs are incomplete by nature.
- `UPDATE`/`DELETE` referencing a processing-loop label rather than a view need
  loop-label resolution, which is not implemented; they are flagged `unresolved`.
- The Mantis and Supra packs are a defensible starting point, not a validated
  grammar. Treat a first-run recognition rate below 85% as a calibration task.
- Copybook `PIC` to format conversion handles common cases, not every
  `REDEFINES` / `OCCURS DEPENDING ON` shape.
