---
title: "{MEMBER} — generated tests (uipath)"
doc_type: generated_test
system: "{SYSTEM}"
module: "{MEMBER}"
language: uipath
framework: testcase
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

# {MEMBER} — generated tests (uipath / testcase)

One short paragraph: which scenarios are covered, which were skipped and
why (a gap from `mfdoc test-advisory`, an unreconstructable consequence),
and whether any `bug-desired` tests are expected to fail. Cite the module
as a whole [[{MEMBER}]] if nothing more specific applies.

This is a UiPath Test Manager manual/data-driven test-case definition for
import -- not a UiPath Coded Test or a claim this tool can drive a 3270
screen end-to-end (it can't -- see
`docs/guides/testing-strategies-for-mainframes-and-4gl.md`). Real UiPath
Test Manager projects customize their test-case data schema; treat the
fields below as a first-draft mapping to adjust to your project's actual
schema, not a guaranteed-importable fixture. Stub the dependencies named
in the brief's "Dependencies to mock" section as Preconditions, using
only the values the brief actually states.

```yaml
# {MEMBER}:BR-nnn [[{MEMBER}:LINE]]
# Branch: <construct> <condition, verbatim>
- test_case_id: "{MEMBER}-BR-nnn"
  title: "test_scenario_name_here"
  preconditions:
    - "Stub dependency per brief's Dependencies-to-mock list"
  steps:
    - given: "<fixture state from the brief's Given>"
      when: "<action from the brief's When -- the cited branch condition>"
      then: "<expected outcome from the brief's Then, or 'unresolved' if the brief has no reconstructable consequence>"
  status: characterization  # or spec / bug-current / bug-desired, per the brief's overlay status
```
