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

| Field | Short name | Format | Length | Descriptor | Business meaning | Confidence | Citation |
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

Unlike every other section above, `entity_brief` has no dedicated fact section
this one is copied from — there is no "Data quality" or "Integrity" heading in
the brief to read off. This section is synthesized narrative: infer it from
raw material scattered elsewhere in *`entity_brief`'s own* Fields table (not
this template's rendered one above, which drops some of the brief's columns) —
chiefly its `descriptor` column (`DE`/`SUPER`/`SUB`/`PHON`/`HYPER`/`UQ` —
uniqueness and lookup structure) and its `options` column (format/validation
notes) — plus any literal values a rule candidate tests against a field (see
the module briefs' "Candidate business rules" for modules that access this
entity). Do not go looking for a brief heading named after this section;
there isn't one. Every claim must still carry its own citation back to the
fact it was inferred from.

## Gaps and questions for review
