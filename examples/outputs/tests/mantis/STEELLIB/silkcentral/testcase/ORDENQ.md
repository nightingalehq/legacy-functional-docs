---
title: "ORDENQ — generated tests (silkcentral)"
doc_type: generated_test
system: "OE"
module: "ORDENQ"
language: silkcentral
framework: testcase
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 0
  inferred: 2
  unresolved: 3
sources: ["ORDENQ"]
---

# ORDENQ — generated tests (silkcentral / testcase)

Covers all five scenarios in the brief: BR-003 and BR-007 have a reconstructable consequence (the `WHILE STATUS = 0` loop entry and the `CALL "PRICECALC"` dispatch on `CONF` status, respectively) and are written as `characterization` cases through that consequence; BR-001, BR-008, and BR-009 have no reconstructable consequence in the source facts, so each is written up to its branch decision only, with the `then` step marked `unresolved` rather than inventing an outcome. No `bug-desired` tests are present since the overlay carries no confirmed defects for this member. Preconditions stub `ORDERMST`, `ORDLINE`, and `PRICECALC` per the brief's Dependencies-to-mock list, using no fields or call shapes beyond what the brief states.

This is a Silk Central test-*case definition* for import into that
project's own case repository -- not an executable SilkTest/4Test
automation script, and not a claim that this tool can drive a 3270
screen (it can't -- see
`docs/guides/testing-strategies-for-mainframes-and-4gl.md`). Real Silk
Central deployments customize their import field set per project; treat
the fields below as a first-draft mapping to adjust to your project's
actual Test Case template, not a guaranteed-importable fixture. Stub the
dependencies named in the brief's "Dependencies to mock" section as
Preconditions, using only the values the brief actually states.

```yaml
# ORDENQ:BR-001 [[ORDENQ:11]]
# Branch: IF ORDER_NO = " "
- test_case_id: "ORDENQ-BR-001"
  title: "test_order_no_blank_branch_decision"
  preconditions:
    - "Stub ORDERMST entity per brief's Dependencies-to-mock list"
    - "Stub ORDLINE entity per brief's Dependencies-to-mock list"
  steps:
    - given: "ORDER_NO is blank (\" \")"
      when: "ORDENQ evaluates IF ORDER_NO = \" \" [[ORDENQ:11]]"
      then: "unresolved -- no reconstructable consequence in source facts"
  status: characterization

# ORDENQ:BR-003 [[ORDENQ:16]]
# Branch: IF STATUS <> 0
- test_case_id: "ORDENQ-BR-003"
  title: "test_status_nonzero_enters_while_loop"
  preconditions:
    - "Stub ORDERMST entity per brief's Dependencies-to-mock list"
    - "Stub ORDLINE entity per brief's Dependencies-to-mock list"
  steps:
    - given: "STATUS is not equal to 0"
      when: "ORDENQ evaluates IF STATUS <> 0 [[ORDENQ:16]]"
      then: "control reaches WHILE STATUS = 0 [[ORDENQ:21]]"
  status: characterization

# ORDENQ:BR-007 [[ORDENQ:25]]
# Branch: CASE ORDVIEW.STATUS
- test_case_id: "ORDENQ-BR-007"
  title: "test_ordview_status_conf_calls_pricecalc"
  preconditions:
    - "Stub ORDERMST entity per brief's Dependencies-to-mock list"
    - "Stub ORDLINE entity per brief's Dependencies-to-mock list"
    - "Stub PRICECALC callee per brief's Dependencies-to-mock list"
  steps:
    - given: "ORDVIEW.STATUS is \"CONF\""
      when: "ORDENQ evaluates CASE ORDVIEW.STATUS [[ORDENQ:25]] and matches WHEN \"CONF\""
      then: "CALL \"PRICECALC\" (ORDER_NO, ORDER_WT) is invoked [[ORDENQ:26-28]]"
  status: characterization

# ORDENQ:BR-008 [[ORDENQ:26]]
# Branch: WHEN "CONF"
- test_case_id: "ORDENQ-BR-008"
  title: "test_when_conf_branch_decision"
  preconditions:
    - "Stub ORDERMST entity per brief's Dependencies-to-mock list"
    - "Stub ORDLINE entity per brief's Dependencies-to-mock list"
  steps:
    - given: "ORDVIEW.STATUS matches WHEN \"CONF\""
      when: "ORDENQ evaluates WHEN \"CONF\" [[ORDENQ:26]]"
      then: "unresolved -- no reconstructable consequence in source facts"
  status: characterization

# ORDENQ:BR-009 [[ORDENQ:28]]
# Branch: WHEN "HELD"
- test_case_id: "ORDENQ-BR-009"
  title: "test_when_held_branch_decision"
  preconditions:
    - "Stub ORDERMST entity per brief's Dependencies-to-mock list"
    - "Stub ORDLINE entity per brief's Dependencies-to-mock list"
  steps:
    - given: "ORDVIEW.STATUS matches WHEN \"HELD\""
      when: "ORDENQ evaluates WHEN \"HELD\" [[ORDENQ:28]]"
      then: "unresolved -- no reconstructable consequence in source facts"
  status: characterization
```