---
title: "{MEMBER} — generated tests (java)"
doc_type: generated_test
system: "{SYSTEM}"
module: "{MEMBER}"
language: java
framework: junit5
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

# {MEMBER} — generated tests (java / junit5)

One short paragraph: which scenarios are covered, which were skipped and
why (a gap from `mfdoc test-advisory`, an unreconstructable consequence),
and whether any `bug-desired` tests are expected to fail (`@Disabled` with
a reason, not left to fail unnoticed). Cite the module as a whole
[[{MEMBER}]] if nothing more specific applies.

```java
// Generated characterization/spec tests for {MEMBER}.
// Do not hand-edit -- regenerate with `mfdoc test-batch`. See the fact
// brief this file was rendered from for the scenarios covered.

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class {MEMBER}Test {

    // Stub the dependencies named in the brief's "Dependencies to mock"
    // section here, using only the parameter/entity shapes the brief
    // states.

    @Test
    void testScenarioNameHere() {
        // {MEMBER}:BR-nnn [[{MEMBER}:LINE]]
        // Branch: <construct> <condition, verbatim>
    }
}
```
