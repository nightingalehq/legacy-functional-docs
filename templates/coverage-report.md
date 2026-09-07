---
title: "{SYSTEM} — extraction coverage"
doc_type: coverage-report
system: "{SYSTEM}"
generated_by: legacy-functional-docs 0.1.0
generated_at: "{YYYY-MM-DD}"
review_status: draft
reviewers: []
confidence_summary:
  verified: 0
  inferred: 0
  unresolved: 0
sources: []
sme_questions: []
---

# {SYSTEM} — extraction coverage

How much of the codebase the tooling actually understood. Report the numbers as
they are; a poor figure here is information the project needs, and presenting a
weak index as a strong one guarantees the problem surfaces later and costs more.

## Inputs ingested

| Source set | Dialect | Files | Members | Lines |
|---|---|---|---|---|

## Recognition

| Metric | Value | Gate | Pass |
|---|---|---|---|
| line_recognition_rate | | | |
| call_resolution_rate | | | |
| entity_definition_rate | | | |
| high-severity gaps | | | |

Where a gate failed, state what was done about it: input obtained, scanner
calibrated, or scope reduced with the limitation recorded in affected documents.

## What the numbers mean

- **line_recognition_rate** — proportion of code lines matched to a known
  construct. A low figure means the scanner is mismatched to this codebase, not
  that the code is unusual.
- **call_resolution_rate** — proportion of invocations whose target source was
  supplied. Low means missing source, so process flows have holes.
- **entity_definition_rate** — proportion of data stores with a supplied
  definition. Low means field-level meaning cannot be documented.
- **dynamic_call_edges** — invocations through variables. Irreducible; a property
  of the codebase, not a defect in the extraction.

## Confidence distribution across the document set

| Document | verified | inferred | unresolved |
|---|---|---|---|

No `mfdoc` command aggregates this across documents -- `mfdoc coverage` only
reports index-wide extraction rates (line_recognition_rate, call_resolution_rate,
entity_definition_rate, dynamic_call_edges, gap counts), not per-document
confidence. Assemble this table by hand: open each generated document under
`docs_root` and copy its own front matter's `confidence_summary.verified` /
`.inferred` / `.unresolved` counts into one row per document.

## Reproducing this run

The exact command sequence that rebuilds the index and re-derives these
numbers, in order:

```
mfdoc ingest   --config project.yml
mfdoc derive   --config project.yml
mfdoc coverage --config project.yml --json <path>
```

Also record the tool version (`generated_by` above) and the config file used.
The index rebuilds from source, so any figure here can be re-derived and
challenged by running the same three commands again against unchanged source.
