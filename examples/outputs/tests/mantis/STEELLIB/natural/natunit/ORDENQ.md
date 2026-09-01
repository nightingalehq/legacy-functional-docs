---
title: "ORDENQ — generated tests (natural)"
doc_type: generated_test
system: "OE"
module: "ORDENQ"
language: natural
framework: natunit
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 2
  inferred: 0
  unresolved: 3
sources: ["ORDENQ"]
---

# ORDENQ — generated tests (natural / natunit)

Covers all five scenarios in the ORDENQ test brief. `ORDENQ:BR-003` and
`ORDENQ:BR-007` have reconstructable consequences and are characterized
directly from the cited source lines. `ORDENQ:BR-001`, `ORDENQ:BR-008`, and
`ORDENQ:BR-009` have no reconstructable consequence in the fact brief, so
each is written up to its branch decision only and marked `unresolved` —
no expected outcome is asserted for them. `PRICECALC` is stubbed per the
brief's "Dependencies to mock" list; only the `ORDER_NO`/`ORDER_WT`
parameters the brief states are used, no additional fields are invented.
`ORDERMST`/`ORDLINE` entity access is not exercised by any scenario with a
reconstructable consequence, so no fixture view setup for them is included
here. Module name `ORDENQ` is 6 characters, so the test program name
`TORDENQ` (7 characters) needs no truncation. Cites the module as a whole
where a scenario has no specific consequence line beyond its branch
citation [[ORDENQ]].

See [`ORDENQ.nsp`](./ORDENQ.nsp) for the generated test source.

## Scenarios covered

- ORDENQ:BR-001
- ORDENQ:BR-003
- ORDENQ:BR-007
- ORDENQ:BR-008
- ORDENQ:BR-009
