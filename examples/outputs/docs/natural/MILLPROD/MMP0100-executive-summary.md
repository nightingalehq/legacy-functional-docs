---
title: "Executive summary — MMP0100"
doc_type: executive_summary
system: "MOM"
module: "MMP0100"
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-09-05"
review_status: draft
reviewers: []
confidence_summary:
  verified: 11
  inferred: 1
  unresolved: 0
sources: ["MMP0100"]
sme_questions:
  - "What do return codes 10 and 20 mean to an operator or calling program — is there a documented code list this should reference instead of the raw literals?"
---

# MMP0100 — executive summary

## Purpose

MMP0100 decides whether a mill order can be released to production, based on
whether enough stock of the required grade is available *(inferred from the
branching structure across the cited rule lines below — no explicit purpose
statement was found in source)* [[MMP0100:35]] [[MMP0100:54]] [[MMP0100:56]].
Depending on stock availability it marks the order fully released, partially
released, or leaves it unreleased and returns a rejection code
[[MMP0100:35]] [[MMP0100:39]].

## Trigger

Batch entry: MMP0100 is a Natural program stacked on the CMSYNIN input to
JCL member `MMB0100`'s step STEP010 (after a LOGON to library MILLPROD),
not a direct `EXEC PGM=` — the batch scheduler reaches it only through
that stacked command sequence [[MMB0100:10]]. No online (CICS) or other
caller was found in the ingested source.

## Top business rules

- If no matching order record is found, the order is rejected with return
  code 10 [[MMP0100:35]].
- If the order fails a status check, return code 20 is returned instead
  [[MMP0100:39]].
- When available stock is at least the order's requested weight, the order
  is marked fully released (`RLSD`) [[MMP0100:53]] [[MMP0100:54]].
- When available stock is at least the order's requested weight reduced by a
  configured tolerance, the order is marked partially released (`PART`)
  instead [[MMP0100:55]] [[MMP0100:56]].

## Inputs / outputs

- `MILL-ORDER`: read and updated [[MMP0100:33]]
- `STOCK-BALANCE`: read [[MMP0100:43]]
- `ORDER-AUDIT`: created [[MMP0100:71]]

## External dependents

- Called by `MMB0100` [[MMB0100:10]]

## Risk

Risk score 100.0 (top of this system's ranked members) — driven by its rule
count (17) combined with its call-graph centrality (1 caller, 4 outgoing
calls); nesting depth is shallow (max depth 1), so the score is not a
complexity-depth outlier, it is a volume-and-centrality one.
