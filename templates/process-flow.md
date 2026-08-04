---
title: "{PROCESS} — {business process name}"
doc_type: process
system: "{SYSTEM}"
process_kind: "{batch|online}"
entry_point: "{job name or CICS transaction}"
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

# {PROCESS}

## Business outcome

What has changed in the business when this process completes successfully.

## Trigger

Schedule, transaction code, user action, or upstream dependency. Where the trigger
lives in a scheduler that was not supplied, say so.

## Steps

| # | Step | Module | What it does | Data affected | Citation |
|---|---|---|---|---|---|

For batch, follow JCL step order and record `COND`/`IF` conditions — a skipped step
is business logic. For online, follow the screen interaction sequence.

## Flow

```mermaid
flowchart TD
```

Keep the diagram to what is evidenced. Mark unresolved or dynamic branches
explicitly rather than drawing a plausible line.

## Data flow

Which stores are read and written at each step, and which datasets pass between
steps. Match dataset creators to consumers via `DISP`.

## Failure and restart

What happens on failure at each step, what is committed by then, and what restart
requires. Where restart behaviour is not evidenced, this is a high-value SME
question — it is usually undocumented and usually known by exactly one person.

## Gaps and questions for review
