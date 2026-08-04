---
title: "{SYSTEM} — gap register"
doc_type: gap-register
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

# {SYSTEM} — gap register

Everything the deterministic pass could not resolve, as questions for domain
experts. This is the most-used document in the set, so order it by what it unblocks
rather than by severity label alone.

## How to use this

Each item names what is unknown, what it blocks, and who is likely to know. Answers
go in the Answer column; when an item is closed, the affected documents are
regenerated and their `review_status` advances.

## Blocking: documentation cannot proceed without these

| # | Question | Blocks | Evidence | Likely owner | Answer |
|---|---|---|---|---|---|

## Missing source

| # | Target | Referenced from | Impact | Answer |
|---|---|---|---|---|

Modules called but not supplied. Each one is a hole in the process flow.

## Missing data definitions

| # | Data store | Accessed from | Impact | Answer |
|---|---|---|---|---|

## Dynamic behaviour

| # | Location | What is indeterminate | Answer |
|---|---|---|---|

Variable call targets and loop-label updates. Source cannot settle these.

## Business intent

| # | Question | Evidence | Answer |
|---|---|---|---|

Magic values, status codes, thresholds and tolerances found as literals whose
business meaning is not evidenced in code.

## Possible dead code

| # | Module | Why it looks unreachable | Confirmed dead? | Answer |
|---|---|---|---|---|

Nothing here is confirmed dead. Dynamic invocation and unsupplied schedulers both
produce false positives, so each needs confirmation before anyone acts on it.

## Discrepancies found

| # | What disagrees | Where | Answer |
|---|---|---|---|

Comments contradicting code, DDMs disagreeing with FDTs, duplicated logic that has
diverged. Often the highest-value findings in the exercise.
