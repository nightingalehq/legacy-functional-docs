---
title: "{SYSTEM} — screen-and-key interface matrix"
doc_type: interface-matrix
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

# {SYSTEM} — screen-and-key interface matrix

Whole-system document, from `mfdoc brief --interface-matrix`'s fact brief.
One row per screen/map x PF-key (or configured dispatch field) combination:
which mode(s)/module(s) the screen is reachable from, the routine each key
dispatches to, and the outcome. Every factual claim must trace to a
`[[MEMBER:line]]` citation from the brief — do not add a fact the brief did
not provide, and do not invent a PF-key's label or outcome the brief's cited
facts don't evidence; mark it `unresolved` instead.

The brief's "PF-key / mode/panel dispatch branches" table can carry rows
from two independent mechanisms, each tagged in its own `mechanism` column:
`PF-key dispatch` (a branch that PERFORMs/CALLs a distinct subroutine per
PF-key value, or sets a field inline, inside a module that displays the
screen itself) and, only when the project configures
`options.overview.mode_field_pattern`, `mode/panel dispatch` (a branch keyed
on a separate mode/panel/transaction-code-like field, often a central block
that acts inline with no subroutine call of its own). Treat these as two
independent fact sources, not duplicates or alternates of each other — a
screen can have rows from both, and they are not guaranteed to agree on the
same set of actions.

## Interface matrix

| Mode / module | Panel / map | PF-key | Mechanism | Label | Routine | Outcome | Citation |
|---|---|---|---|---|---|---|---|

- **Mode / module**: from the brief's "Reachable from" list for this
  screen — every module whose own source displays it. More than one module
  reaching the same screen is not necessarily more than one distinct
  business mode; say what the brief's facts actually show (e.g. "reachable
  from both `MMP0100` and `MMP0200`") rather than inventing mode names
  (ADD/CHANGE/INQUIRE/...) the source doesn't name.
- **Panel / map**: the screen/map name from the brief's `## Screen/map`
  heading.
- **PF-key**: the trigger value from the brief's dispatch branches table —
  a PF-key literal for a `PF-key dispatch` row, or the mode/panel field's
  own literal value for a `mode/panel dispatch` row.
- **Mechanism**: copy the brief's `mechanism` column verbatim
  (`PF-key dispatch` or `mode/panel dispatch`) — never merge or relabel the
  two into one generic "dispatch" without saying which fact source it came
  from.
- **Label**: match a "Candidate PF-key labels" entry to this PF-key only
  when the label text itself names the key (e.g. a `PF3=Exit` caption
  matched to the `PF3` trigger row) — cite both the label and the branch.
  Leave blank and note it as an SME question when no label evidently
  corresponds, rather than guessing which caption belongs to which key.
- **Routine**: the routine(s) called in that branch, from the brief's
  "calls" column.
- **Outcome**: characterise what the branch actually does (exit the
  screen, navigate to another screen/mode, raise an error, no visible
  effect, ...) from the cited routine name, call kind, and fields set —
  never from the PF-key's number or the label's wording alone. Mark
  `unresolved` when the branch's effect can't be determined from what was
  supplied (e.g. the call target's own source is missing).

## Screens with no interface facts recorded

Any screen the brief's "No screen..." note or omission implies has a
display reference but no dispatch branch (or vice versa) — worth naming so
a reader doesn't assume the matrix above is exhaustive over every screen in
the system, only the ones with both facts on record.

## Gaps and questions for review

Unresolved outcomes, unmatched labels, and any screen reachable from more
than one module where it isn't otherwise evident whether that reflects
distinct business modes or one mode invoked from more than one place.
