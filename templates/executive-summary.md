---
title: "Executive summary — {MEMBER}"
doc_type: executive_summary
system: "{SYSTEM}"
module: "{MEMBER}"
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
by another program) — cite the brief's entry-point facts._

## Top business rules

_3-5 bullets from the brief's "Top rules" section, in plain language._

## Inputs / outputs

_Which entities this program reads/writes, from the brief's "I/O" section._

## External dependents

_Which other programs call this one, from the brief's "External
dependents" section — omit this section if the brief lists none._

## Risk

_One line summarizing the brief's "Risk" section's score, plus what
drives it (rule count, nesting depth, or call-graph centrality)._
