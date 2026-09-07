---
title: "Mill Order Management — screen-and-key interface matrix"
doc_type: interface-matrix
system: "MOM"
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-09-07"
review_status: draft
reviewers: []
confidence_summary:
  verified: 0
  inferred: 0
  unresolved: 0
sources: []
sme_questions:
  - "No PF-key (or configured dispatch field) branch was found alongside a screen-display reference in the bundled sample codebase -- was a screen-driven menu/dispatch program left out of this ingest?"
---

# Mill Order Management — screen-and-key interface matrix

From `mfdoc brief --interface-matrix` against `templates/interface-matrix.md`.

## Interface matrix

| Mode / module | Panel / map | PF-key | Label | Routine | Outcome | Citation |
|---|---|---|---|---|---|---|

No rows: the bundled sample codebase's online members either display a
screen without dispatching on a value compared against it (`MMP0200`
displays map `MMM0200` [[MMP0200:11]] but branches only on `#CERT-NO` and
`ON ERROR`, neither the configured dispatch field), or dispatch on a
data-driven value rather than a screen navigation key (`ORDENQ`'s
`CASE ORDVIEW.STATUS` [[ORDENQ:25]] branches on an order's own status, not
a PF-key/menu-option field). The brief itself reports this plainly rather
than rendering an empty table with no explanation -- see
`brief.interface_matrix_brief`'s own "No screen..." note.

## Screens with no interface facts recorded

Every screen/map this index knows about is displayed without a matching
dispatch branch (or vice versa) -- there is currently no screen in this
sample codebase with both facts on record at once:

- `MMM0200` — displayed by `MMP0200` [[MMP0200:11]]; no dispatch branch
  found in any module that displays it.
- `ORDSCR1` — displayed by `ORDENQ` [[ORDENQ:10]]; no dispatch branch found.
- `ORDSCR2` — displayed by `ORDENQ` [[ORDENQ:33]]; no dispatch branch
  found.
- `MAP` — displayed by `SCRNENT` [[SCRNENT:6]] (the literal token following
  `CONVERSE`, as the scanner records it -- `SCRNENT`'s own
  `SCREEN MAP("SCRNENT1")` declaration [[SCRNENT:5]] names the underlying
  screen, but `_match_interaction`-equivalent Mantis extraction here
  records the reference as written, not resolved back through the
  declaration); no dispatch branch found.

## Gaps and questions for review

- This sample codebase has no member whose source both displays a screen
  and dispatches on a PF-key (Natural's built-in `*PF-KEY`, the default
  dispatch field) or an equivalent configured field for that screen -- a
  real engagement fixture with a menu/PF-key-driven program would exercise
  the matrix's actual rows; this worked example instead demonstrates the
  brief's honest "nothing to report" path. See
  `tests/test_interface_matrix_brief.py` for synthetic cases that do
  exercise populated rows, both for Natural's `*PF-KEY` and a Mantis-style
  configured menu/transfer-option field.
