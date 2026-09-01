---
title: "ORDENQ — generated tests (mantis)"
doc_type: generated_test
system: "OE"
module: "ORDENQ"
language: mantis
framework: native
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 0
  inferred: 0
  unresolved: 3
sources: ["ORDENQ"]
---

# ORDENQ — generated tests (mantis / native)

Covers ORDENQ:BR-003 and ORDENQ:BR-007 with reconstructable consequences (entry into the `WHILE STATUS = 0` loop, and dispatch to `PRICECALC` on the `"CONF"` case), plus ORDENQ:BR-001, BR-008, and BR-009 written up to their branch decisions only and marked `unresolved`, since no consequence is reconstructable from the source facts for those three. `PRICECALC` is stubbed per the brief's "Dependencies to mock" list using only the `ORDER_NO`/`ORDER_WT` parameters the brief states; `ORDERMST`/`ORDLINE` are named as dependencies but no field shape for either is given in the brief, so no fixture record is invented for them. No `bug-desired` tests are present.

See [`ORDENQ.mantis`](./ORDENQ.mantis) for the generated test source.

## Scenarios covered

- ORDENQ:BR-001
- ORDENQ:BR-003
- ORDENQ:BR-007
- ORDENQ:BR-008
- ORDENQ:BR-009
