---
title: "{MODULE} — {one-line business purpose}"
doc_type: module
system: "{SYSTEM}"
module: "{MODULE}"
dialect: "{natural|mantis}"
library: "{LIBRARY}"
object_type: "{program|subprogram|subroutine|copycode}"
generated_by: legacy-functional-docs 0.1.0
generated_at: "{YYYY-MM-DD}"
review_status: draft
reviewers: []
confidence_summary:
  verified: 0
  inferred: 0
  unresolved: 0
sources: ["{MODULE}"]
sme_questions: []
---

# {MODULE} — {business purpose}

## Purpose

Two or three sentences on what this module does for the business. Cite the
statements that establish it. Where purpose is inferred rather than evidenced, mark
it *(inferred)*.

## How it is invoked

Who calls it, with what, and from where — batch job step, CICS transaction, another
module. Cite each. If nothing was found to invoke it, say so and cite the gap rather
than leaving the section empty.

## Inputs

| Name | Format | Source | Notes | Citation |
|---|---|---|---|---|

Parameters, and any data read to establish context before the main work begins.

## Data used

| Data store | Operations | Key / access path | Purpose | Citation |
|---|---|---|---|---|

One row per store. Say what each access is *for*, not merely that it happens.

## Business rules

Numbered, each with citation, confidence, and the rule's `{MEMBER}:BR-nnn` ID from
the brief. Group by the decision they serve rather than by source order — source
order reflects how the code was built, not how the business thinks about it. When
the brief's "Internal routines" section lists more than one routine, use those as
the grouping (a subsection per routine, or a clear heading), not a flat list —
a reader trying to find everything one routine does should not have to read the
whole document. The ID is a stable, system-wide-unique handle for referring back
to this exact rule later (in a revision, or a gap-register conversation with an
SME) — copy it from the brief verbatim, never invent or renumber one.

When a rule is an `IF` the brief marks with a paired ELSE, document what happens
on *both* branches — not just the one that reads as interesting. The brief lists
the data access found on each branch precisely so this doesn't get missed; every
one of those accesses belongs in this section, attributed to its branch.

1. **{Rule name}** ({MEMBER}:BR-nnn) — {statement in business terms}. {Citation}
2. …

## Processing sequence

Numbered steps in execution order, each cited. Keep to what a reader needs to follow
the logic; this is not a transliteration of the code.

## Transaction boundaries

Where changes are committed, and what is included in each unit of work. If no commit
was found in this module, say so explicitly and record the SME question — do not
imply a boundary that is not evidenced.

## Outputs and effects

Data written, messages shown, files produced, modules invoked with side effects.

## Error handling

Error paths and what the user or operator sees. Include `REINPUT` and message text,
which is user-visible business validation rather than plumbing.

## Gaps and questions for review

Every item from this module's gap register, phrased as a question a domain expert can
answer without reading code.
