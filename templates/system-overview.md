---
title: "{SYSTEM} — functional overview"
doc_type: system-overview
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

# {SYSTEM} — functional overview

## What the system does

The business capability, in terms a business reader recognises. Cite the modules
and data stores that evidence it.

## Scope of this documentation

What was supplied, what was not, and what that means for the reader. Put this near
the top; a reader who discovers the scope limits on page 40 has already been misled.

| Input | Supplied | Coverage impact |
|---|---|---|

## Data model

The business entities and their relationships. Then the CRUD matrix.

| Module | Data store | Operations | Citation |
|---|---|---|---|

## Entry points

| Kind | Name | Starts | Notes | Citation |
|---|---|---|---|---|

Batch jobs and online transactions. For Natural under CICS, note where the
transaction reaches a driver rather than the business program.

## Module inventory

| Module | Type | Purpose | Doc | Citation |
|---|---|---|---|---|

## Cross-cutting behaviour

Shared copycode, common subprograms, global data areas, standard error handling —
things whose rules apply system-wide and would otherwise be documented
inconsistently in every module that includes them.

## Known unknowns

The high-severity gaps, summarised, with what each blocks. Link to the gap register.
