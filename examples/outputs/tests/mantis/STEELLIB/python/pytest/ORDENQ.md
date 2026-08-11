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
  verified: 2
  inferred: 0
  unresolved: 3
sources: ["ORDENQ"]
---

# ORDENQ — generated tests (python / pytest)

Covers 5 scenarios from the ORDENQ test brief. BR-002 [[ORDENQ:16]] and
BR-004 [[ORDENQ:25]] have reconstructable consequences (the `WHILE STATUS =
0` loop guard at [[ORDENQ:21]], and the `CALL "PRICECALC"` at
[[ORDENQ:26-28]] respectively) and are asserted directly. BR-001
[[ORDENQ:11]], BR-005 [[ORDENQ:26]], and BR-006 [[ORDENQ:28]] have no
consequence reconstructable from source facts in the brief, so they are
written up to the branch decision only and marked `unresolved` via
`pytest.mark.skip` rather than inventing an expected outcome. `PRICECALC` is
stubbed per the brief's "Dependencies to mock" section using only the
parameter names the brief states (`ORDER_NO`, `ORDER_WT`); `ORDERMST` and
`ORDLINE` are named as dependencies but no field shape for either is given
in the brief, so no fixture is invented for them.

See [`ORDENQ.py`](./ORDENQ.py) for the generated test source.

## Scenarios covered

- ORDENQ:BR-001
- ORDENQ:BR-002
- ORDENQ:BR-004
- ORDENQ:BR-005
- ORDENQ:BR-006
