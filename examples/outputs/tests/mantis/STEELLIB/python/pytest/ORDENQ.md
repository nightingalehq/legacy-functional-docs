---
title: "ORDENQ — generated tests (python)"
doc_type: generated_test
system: "OE"
module: "ORDENQ"
language: python
framework: pytest
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 1
  inferred: 0
  unresolved: 4
sources: ["ORDENQ"]
---

# ORDENQ — generated tests (python / pytest)

Covers all five scenarios in the ORDENQ test brief. BR-004 has a reconstructable consequence (entering the `CASE` with `ORDVIEW.STATUS = "CONF"` calls `PRICECALC` with `ORDER_NO`/`ORDER_WT`) and is asserted directly. BR-001, BR-002, BR-005, and BR-006 have no reconstructable consequence in the cited source excerpt — each is written up to its branch decision only and marked `unresolved` rather than inventing an expected outcome. `ORDERMST` and `ORDLINE` are named as dependencies to mock in the brief but no field shapes or call sites for them appear in the cited excerpts, so they are stubbed as opaque mocks and not asserted against.

See [`ORDENQ.py`](./ORDENQ.py) for the generated test source.

## Scenarios covered

- ORDENQ:BR-001
- ORDENQ:BR-002
- ORDENQ:BR-004
- ORDENQ:BR-005
- ORDENQ:BR-006
