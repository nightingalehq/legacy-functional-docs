---
title: "Executive summary — {MEMBER}"
doc_type: executive_summary
system: "{SYSTEM}"
module: "{MEMBER}"
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

# {MEMBER} — executive summary

One page. Write for a reviewer who will not read the per-module docs.
Every factual claim must trace to a `[[MEMBER:line]]` citation from the
brief this template was generated against — do not add facts the brief
did not provide.

## Purpose

_1-2 sentences: what this program does and when it runs._

## Trigger

_How this program starts (batch job step, online transaction, called
by another program) — cite the brief's "Entry point" section's facts._

## Top business rules

_3-5 bullets from the brief's "Top rules" section, in plain language._

## Inputs / outputs

_Which entities this program reads/writes, from the brief's "I/O" section._

## External dependents

_Which other programs call this one, from the brief's "External
dependents" section. The brief always includes this heading, with a "no
known callers recorded for this member" line when there are none -- do
not treat the brief's presence of the heading as a reason to keep it in
the generated document. In the document you write, condense an empty
list to a single one-line statement that no callers were found, rather
than reproducing an empty table or omitting the heading outright._

## Risk

_One line summarizing the brief's "Risk" section's score, plus what
drives it (rule count, nesting depth, or call-graph centrality)._
