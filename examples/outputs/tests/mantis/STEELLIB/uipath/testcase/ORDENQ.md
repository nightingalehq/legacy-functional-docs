---
title: "ORDENQ — generated tests (uipath)"
doc_type: generated_test
system: "OE"
module: "ORDENQ"
language: uipath
framework: testcase
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 0
  inferred: 5
  unresolved: 3
sources: ["ORDENQ"]
---

# ORDENQ — generated tests (uipath / testcase)

Five scenarios covered: BR-002 asserts the `WHILE STATUS = 0` loop entry cited at [[ORDENQ:21]] following the `STATUS <> 0` branch check, and BR-004 asserts the `CALL "PRICECALC"` invocation cited at [[ORDENQ:26-28]] as the reconstructable consequence of the `CASE ORDVIEW.STATUS` dispatch. BR-001, BR-005, and BR-006 have no reconstructable consequence in the fact brief, so each is written only up to its branch decision with the expected outcome marked `unresolved`. This is a UiPath Test Manager manual/data-driven test-case definition for import — not a UiPath Coded Test or a claim this tool can drive a 3270 screen end-to-end (see `docs/guides/testing-strategies-for-mainframes-and-4gl.md`). Real UiPath Test Manager projects customize their test-case data schema; treat the fields below as a first-draft mapping to adjust to your project's actual schema. Preconditions stub the `ORDERMST`/`ORDLINE` entities and the `PRICECALC` callee named in the brief's Dependencies-to-mock list, using only the values the brief states.

```yaml
# ORDENQ:BR-001 [[ORDENQ:11]]
# Branch: IF ORDER_NO = " "
- test_case_id: "ORDENQ-BR-001"
  title: "test_order_no_blank_check"
  preconditions:
    - "Stub ORDERMST entity per brief's Dependencies-to-mock list"
    - "Stub ORDLINE entity per brief's Dependencies-to-mock list"
  steps:
    - given: "ORDER_NO is set to a blank value (\" \")"
      when: "ORDENQ evaluates IF ORDER_NO = \" \""
      then: "unresolved -- no reconstructable consequence in the fact brief for this branch"
  status: characterization

# ORDENQ:BR-002 [[ORDENQ:16]]
# Branch: IF STATUS <> 0
- test_case_id: "ORDENQ-BR-002"
  title: "test_status_not_zero_enters_wait_loop"
  preconditions:
    - "Stub ORDERMST entity per brief's Dependencies-to-mock list"
    - "Stub ORDLINE entity per brief's Dependencies-to-mock list"
  steps:
    - given: "STATUS is set to a non-zero value"
      when: "ORDENQ evaluates IF STATUS <> 0"
      then: "control reaches WHILE STATUS = 0 [[ORDENQ:21]], per the cited source"
  status: characterization

# ORDENQ:BR-004 [[ORDENQ:25]]
# Branch: CASE ORDVIEW.STATUS
- test_case_id: "ORDENQ-BR-004"
  title: "test_case_status_conf_calls_pricecalc"
  preconditions:
    - "Stub ORDERMST entity per brief's Dependencies-to-mock list"
    - "Stub ORDLINE entity per brief's Dependencies-to-mock list"
    - "Stub PRICECALC callee per brief's Dependencies-to-mock list"
  steps:
    - given: "ORDVIEW.STATUS is set to \"CONF\""
      when: "ORDENQ evaluates CASE ORDVIEW.STATUS"
      then: "PRICECALC is invoked with (ORDER_NO, ORDER_WT), per source [[ORDENQ:26-28]]"
  status: characterization

# ORDENQ:BR-005 [[ORDENQ:26]]
# Branch: WHEN "CONF"
- test_case_id: "ORDENQ-BR-005"
  title: "test_when_conf_branch_taken"
  preconditions:
    - "Stub ORDERMST entity per brief's Dependencies-to-mock list"
    - "Stub ORDLINE entity per brief's Dependencies-to-mock list"
  steps:
    - given: "ORDVIEW.STATUS is set to \"CONF\""
      when: "ORDENQ evaluates WHEN \"CONF\""
      then: "unresolved -- no reconstructable consequence in the fact brief for this branch"
  status: characterization

# ORDENQ:BR-006 [[ORDENQ:28]]
# Branch: WHEN "HELD"
- test_case_id: "ORDENQ-BR-006"
  title: "test_when_held_branch_taken"
  preconditions:
    - "Stub ORDERMST entity per brief's Dependencies-to-mock list"
    - "Stub ORDLINE entity per brief's Dependencies-to-mock list"
  steps:
    - given: "ORDVIEW.STATUS is set to \"HELD\""
      when: "ORDENQ evaluates WHEN \"HELD\""
      then: "unresolved -- no reconstructable consequence in the fact brief for this branch"
  status: characterization
```