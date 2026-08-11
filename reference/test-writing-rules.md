# Writing rules for the test-generation render pass

Read this before writing the first generated test file. It extends
`writing-rules.md`'s contract (citation format, confidence taxonomy, front
matter) to test code specifically. `mfdoc validate` enforces the parts it
can check mechanically the same way it does for module docs; the rest is on
you.

## What you are given

A test brief (`mfdoc test-plan`'s output for one member) listing every
derived scenario: its stable `MEMBER:BR-nnn` id, the branch construct and
condition it exercises, and — where reconstructable — the exact source
lines that execute inside that branch. Nothing in the brief is invented; a
scenario with no reconstructable consequence says so explicitly. Treat that
absence the same way: write the test up to the branch decision and mark the
assertion `unresolved`, or omit the assertion and record why, rather than
inventing a plausible expected value.

## Output shape

Produce one Markdown document with the front matter fields below, followed
by a single fenced code block containing the complete test file for the
requested language/framework. Do not add prose outside the code fence
except the front matter and a one-paragraph summary of what the file
covers — the code fence itself is the deliverable.

```yaml
---
title: "{MEMBER} — generated tests ({language})"
doc_type: generated_test
system: "{SYSTEM}"
module: "{MEMBER}"
language: "{python|java|...}"
framework: "{pytest|junit5|...}"
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
```

## Citation format inside code

Every test function/method gets a leading comment carrying the scenario's
`MEMBER:BR-nnn` id and its `[[MEMBER:LINE]]` citation, exactly as it appears
in the brief — do not renumber or drop it. This is what lets a reviewer (or
`mfdoc validate`) trace a generated assertion back to the exact source line
it characterizes.

```python
def test_order_release_rejects_unconfirmed_order():
    # MMP0100:BR-004 [[MMP0100:38-40]]
    # Branch: IF ORDER-VIEW.ORDER-STATUS NE 'CONF'
    ...
```

Natural/NatUnit and Mantis/native use `*`-prefixed comment lines instead
of `#`, matching the comment syntax both dialects' own extractors already
recognise:

```natural
* {MEMBER}:BR-nnn [[{MEMBER}:LINE]]
* Branch: IF ORDER-VIEW.ORDER-STATUS NE 'CONF'
CALLNAT 'ASSERT-EQUAL' #EXPECTED #ACTUAL 'test_order_release_rejects_unconfirmed_order'
```

Silk Central and UiPath test-case targets carry the same id/citation as a
leading comment inside their YAML block, since there's no function/method
to attach a docstring-style comment to:

```yaml
# {MEMBER}:BR-nnn [[{MEMBER}:LINE]]
- test_case_id: "{MEMBER}-BR-nnn"
```

## Characterization vs. spec framing

A scenario's `status` (from `test-overlay.yml`, defaulting to
`characterization`) controls how you frame the assertion, not whether you
write one:

- **`characterization`** — assert exactly what the cited source excerpt
  does, in the target language's idiom. Do not soften, generalize, or
  "fix" it. This test should pass against a faithful port of the legacy
  behaviour, bug-for-bug.
- **`spec`** — the fact brief additionally carries the narrative module
  doc's stated intent for this rule. Assert the *intended* behaviour. If it
  differs from the characterization test for the same `BR-nnn`, name both
  tests so the difference is obvious (e.g. `..._current_behaviour` /
  `..._intended_behaviour`), and note in a comment that they are expected
  to diverge until the underlying code is fixed.
- **`bug-current`** / **`bug-desired`** — a rule an SME has confirmed is a
  defect, via the overlay. Write both: `bug-current` characterizes exactly
  what happens today (so a migration can prove it preserved *current*
  behaviour if that's temporarily required); `bug-desired` asserts the
  fixed behaviour and is expected to fail until remediation. Mark the
  `bug-desired` test with the target framework's "expected failure" idiom
  (e.g. `pytest.mark.xfail`, JUnit5 `@Disabled` with a reason) rather than
  leaving it to fail silently in a suite nobody is watching.

## Mocking

The brief's "Dependencies to mock" section (or the fuller
`mfdoc test-advisory` report) names exactly which entities/callees this
member touches. Stub only those, using values the brief actually states
(parameter names/formats) — never invent a field, a return shape, or a
call signature that isn't in the facts. Where the advisory reports a gap
(dynamic or unresolved call), do not guess at a target: write the test up
to that call as an opaque "was invoked with X" assertion, or omit it and
record the gap in the summary paragraph.

## Prose failures to avoid

Same failures `writing-rules.md` calls out for narrative docs apply here:
don't narrate confidence you don't have, don't paraphrase a condition when
the exact text is available, don't drop the `unresolved` marker to make a
test look more complete than the facts support.

## Output artifacts after generation

Your contract above is unchanged -- always produce one Markdown document,
front matter + summary + a single fenced code block, exactly as described.
What happens next is mechanical, not yours to do: once your response
validates, `mfdoc test-gen`/`mfdoc test-batch` extract that one fence into a
sibling source file (`{member}.py` for python, `{member}.java` for java --
see `testlang.LANGUAGE_EXTENSIONS`) next to the `.md`, and rewrite the `.md`
to keep the front matter and your summary paragraph but replace the fence
with a short reference to the sidecar file plus a `## Scenarios covered`
list of every `MEMBER:BR-nnn` id your code referenced. This is what lets
`mfdoc test-validate` keep checking every scenario reference mechanically
once the real code lives in its own file. A language with no entry in
`LANGUAGE_EXTENSIONS` is left exactly as you wrote it -- no extension is
ever guessed.

As of this writing that split happens for `python` (`.py`), `java`
(`.java`), `natural` (`.nsp`), and `mantis` (`.mantis`). `silkcentral` and
`uipath` targets are test-case *definitions*, not source code in a
language with a stable file extension across every deployment — their
fence stays embedded in the `.md`, front matter and all, exactly as
written.
