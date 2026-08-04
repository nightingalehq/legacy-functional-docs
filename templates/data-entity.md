---
title: "{ENTITY} — {business meaning}"
doc_type: data-entity
system: "{SYSTEM}"
entity: "{ENTITY}"
entity_kind: "{adabas_file|ddm|supra_master|supra_ved|sql_table|vsam}"
physical_ref: "{DBID/FNR, dataset name, or schema.table}"
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

# {ENTITY} — {business meaning}

## What it holds

What one record represents in business terms, and what makes it unique. Cite the
key or descriptor that establishes uniqueness.

## Physical implementation

Where it lives, and where the logical and physical definitions disagree — a DDM
that omits FDT fields, a field whose long name no longer matches its use. These
discrepancies are usually the most valuable findings; do not smooth them over.

## Fields

| Field | Short name | Format | Length | Key/index | Business meaning | Confidence | Citation |
|---|---|---|---|---|---|---|---|

Business meaning is `inferred` unless a comment, a validation rule in code, or an
SME establishes it. Leave it blank and flag `unresolved` rather than guessing from
the field name.

Note multiple-value fields, periodic groups and `OCCURS` explicitly: they encode
one-to-many relationships a relational target will need to model as child tables.

## Relationships

| Related store | Relationship | Implemented by | Citation |
|---|---|---|---|

Adabas coupling, Supra linkpaths, SQL foreign keys, and joins performed in code.
Distinguish declared relationships from ones enforced only by application logic —
the second kind does not survive a naive migration.

## Which modules use it

| Module | Operations | Access path | Purpose | Citation |
|---|---|---|---|---|

## Data quality and integrity rules

Uniqueness constraints, null suppression, validation enforced in application code,
values that appear only as literals in conditions. Each cited.

## Gaps and questions for review
