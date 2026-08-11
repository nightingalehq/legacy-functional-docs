---
title: "{MEMBER} — generated tests (natural)"
doc_type: generated_test
system: "{SYSTEM}"
module: "{MEMBER}"
language: natural
framework: natunit
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

# {MEMBER} — generated tests (natural / natunit)

One short paragraph: which scenarios are covered, which were skipped and
why (a gap from `mfdoc test-advisory`, an unreconstructable consequence),
and whether any `bug-desired` tests are expected to fail. Cite the module
as a whole [[{MEMBER}]] if nothing more specific applies.

NatUnit convention: one test *program* per module (`T{MEMBER}` — Natural
program names are capped at 8 characters, so truncate `{MEMBER}` if
needed and note the truncation in the summary paragraph above), one
`CALLNAT 'ASSERT-EQUAL'`/`'ASSERT-TRUE'`/`'ASSERT-FALSE'` per scenario.
Stub the dependencies named in the brief's "Dependencies to mock" section
by setting up the fixture views/parameters the brief actually states --
never invent a field this tool's fact store didn't report.

```natural
* Generated characterization/spec tests for {MEMBER}.
* Do not hand-edit -- regenerate with `mfdoc test-batch`. See the fact
* brief this file was rendered from for the scenarios covered.
*
DEFINE DATA LOCAL
1 #EXPECTED (A32)
1 #ACTUAL   (A32)
END-DEFINE
*
* {MEMBER}:BR-nnn [[{MEMBER}:LINE]]
* Branch: <construct> <condition, verbatim>
* Scenario: test_scenario_name_here
*
* ... set up fixture input per the brief, CALLNAT the unit under test,
* capture its output into #ACTUAL ...
*
CALLNAT 'ASSERT-EQUAL' #EXPECTED #ACTUAL 'test_scenario_name_here'
*
END
```
