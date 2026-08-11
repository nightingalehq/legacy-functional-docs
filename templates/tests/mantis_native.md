---
title: "{MEMBER} — generated tests (mantis)"
doc_type: generated_test
system: "{SYSTEM}"
module: "{MEMBER}"
language: mantis
framework: native
generated_by: legacy-functional-docs 0.1.0
generated_at: "{YYYY-MM-DD}"
review_status: draft
reviewers: []
confidence_summary:
  verified: 0
  inferred: 0
  unresolved: 0
sources: ["{MEMBER}"]
---

# {MEMBER} — generated tests (mantis / native)

One short paragraph: which scenarios are covered, which were skipped and
why (a gap from `mfdoc test-advisory`, an unreconstructable consequence),
and whether any `bug-desired` tests are expected to fail. Cite the module
as a whole [[{MEMBER}]] if nothing more specific applies.

No dedicated Mantis unit-test framework exists, so this is a native
driver program: one paragraph per scenario that sets up fixture input,
`PERFORM`s the paragraph/subroutine under test, and compares actual vs.
expected with an `IF`/`DISPLAY` pair -- run as a batch job in the same
Mantis/Supra environment the module itself runs in, read by eye or piped
through `grep FAIL`. Stub the dependencies named in the brief's
"Dependencies to mock" section using only the field/record shapes the
brief actually states.

```mantis
* Generated characterization/spec tests for {MEMBER}.
* Do not hand-edit -- regenerate with `mfdoc test-batch`. See the fact
* brief this file was rendered from for the scenarios covered.
*
* {MEMBER}:BR-nnn [[{MEMBER}:LINE]]
* Branch: <construct> <condition, verbatim>
* Scenario: test_scenario_name_here
*
* ... set up fixture input per the brief ...
PERFORM {MEMBER}-UNDER-TEST
IF ACTUAL-RESULT = EXPECTED-RESULT
    DISPLAY 'PASS test_scenario_name_here'
ELSE
    DISPLAY 'FAIL test_scenario_name_here: expected ' EXPECTED-RESULT ' got ' ACTUAL-RESULT
END-IF
*
STOP RUN
```
