# Destination-language test-generation matrix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `mfdoc test-gen`/`mfdoc test-batch` render generated tests into Natural (NatUnit), Mantis (a native driver-program convention), Silk Central, and UiPath test-case shapes — in addition to today's Python/Java — and let a project render every configured target in one `--matrix` invocation instead of one CLI call per target.

**Architecture:** Everything upstream of the render stage (`ingest`/`derive`/`test-plan`/`test-advisory`) is untouched. Four new `templates/tests/{language}_{framework}.md` files plug into the existing template-driven render path (`testbatch.py`'s `build_test_prompt`/`run_test_batch`/`generate_member_test_doc`, all already language-agnostic). `testlang.py`'s `LANGUAGE_EXTENSIONS` gets two new entries (`natural`→`nsp`, `mantis`→`mantis`); Silk Central/UiPath deliberately get none, so their generated fence stays embedded in the `.md` per that module's existing "never fabricate an extension" contract. `cli.py` gains a `--matrix` flag on `test-gen`/`test-batch` that loops the existing single-target render body over a list read from `options.testgen.matrix` in project config, instead of one hardcoded `--language`/`--framework` pair.

**Tech Stack:** Python 3.10+, stdlib only for the pipeline itself (no new runtime dependency — this task touches config/CLI wiring and Markdown templates, not extraction).

## Global Constraints

- Python ≥ 3.10 syntax only (`X | Y` unions, walrus) — check `pyproject.toml`'s `requires-python` before using anything newer.
- No new runtime dependency. This work doesn't need one; don't add one.
- Don't invent a default redaction pattern, dialect assumption, or config default not explicitly specified here — in particular, Silk Central/UiPath get **no** `LANGUAGE_EXTENSIONS` entry (spec: `docs/superpowers/specs/2026-08-11-test-generation-matrix-design.md`, "New templates" section).
- Every generated-test template must follow `reference/test-writing-rules.md`'s shape exactly: front matter (`language`/`framework`/etc.), one summary paragraph, a single fenced block, `MEMBER:BR-nnn [[MEMBER:LINE]]` citation comments.
- Natural and Mantis templates use `*`-prefixed comment lines for citations (matches both dialects' own extractors — `natural.py`'s `^\*\s`/`stripped == "*"` check, `mantis.py`'s `COMMENT_PREFIXES`).
- `mfdoc validate`/`mfdoc test-validate` must report 0 invalid citations across `examples/outputs/` after regeneration — this is what CI checks (`.github/workflows/ci.yml`), not just the unit suite.
- Follow the existing `run_test_batch` state-key convention (`f"{subdir}::{member}::{language}::{framework}"`) — do not introduce a second convention for matrix mode; it must reuse it unchanged, since that's what already makes one shared `--state` file safe across multiple targets.

---

### Task 1: Extend `LANGUAGE_EXTENSIONS` for Natural and Mantis

**Files:**
- Modify: `src/mfdoc/testlang.py:14-17`
- Test: `tests/test_test_batch.py` (add to existing file, near `test_unknown_language_keeps_code_embedded`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `testlang.LANGUAGE_EXTENSIONS["natural"] == "nsp"`, `testlang.LANGUAGE_EXTENSIONS["mantis"] == "mantis"` — Task 2's templates and Task 6's example regeneration both depend on these two exact values.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_test_batch.py`:

```python
def test_natural_and_mantis_have_sidecar_extensions():
    from mfdoc.testlang import LANGUAGE_EXTENSIONS, sidecar_path_for

    assert LANGUAGE_EXTENSIONS["natural"] == "nsp"
    assert LANGUAGE_EXTENSIONS["mantis"] == "mantis"
    assert sidecar_path_for(Path("FAKEMOD.md"), "natural") == Path("FAKEMOD.nsp")
    assert sidecar_path_for(Path("FAKEMOD.md"), "mantis") == Path("FAKEMOD.mantis")


def test_silkcentral_and_uipath_have_no_sidecar_extension():
    """Test-case-definition targets stay embedded in the .md -- no
    invented extension for an import format that varies per deployment."""
    from mfdoc.testlang import sidecar_path_for

    assert sidecar_path_for(Path("FAKEMOD.md"), "silkcentral") is None
    assert sidecar_path_for(Path("FAKEMOD.md"), "uipath") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_test_batch.py -k "natural_and_mantis_have_sidecar or silkcentral_and_uipath_have_no_sidecar" -v`
Expected: FAIL — `KeyError: 'natural'` (the first test); the second test passes already (no code needed for languages that were never going to be in the map), which is fine, it's there to lock in current behavior alongside the new one.

- [ ] **Step 3: Write minimal implementation**

In `src/mfdoc/testlang.py`, change:

```python
LANGUAGE_EXTENSIONS = {
    "python": "py",
    "java": "java",
}
```

to:

```python
LANGUAGE_EXTENSIONS = {
    "python": "py",
    "java": "java",
    "natural": "nsp",
    "mantis": "mantis",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_test_batch.py -k "natural_and_mantis_have_sidecar or silkcentral_and_uipath_have_no_sidecar" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mfdoc/testlang.py tests/test_test_batch.py
git commit -m "Add natural/mantis sidecar extensions to testlang.LANGUAGE_EXTENSIONS"
```

---

### Task 2: Natural (NatUnit) and Mantis (native driver) templates

**Files:**
- Create: `templates/tests/natural_natunit.md`
- Create: `templates/tests/mantis_native.md`
- Test: `tests/test_test_batch.py` (add new tests)

**Interfaces:**
- Consumes: `testlang.LANGUAGE_EXTENSIONS` from Task 1 (`natural`→`nsp`, `mantis`→`mantis`) — the round-trip test below writes a fake doc using these two languages and expects those two sidecar extensions.
- Produces: `templates/tests/natural_natunit.md`, `templates/tests/mantis_native.md`, loaded by `cli._test_template_path(base, "natural", "natunit", None)` / `cli._test_template_path(base, "mantis", "native", None)` — Task 6's example regeneration depends on these two files existing at those exact paths.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_test_batch.py`:

```python
def test_natural_and_mantis_templates_exist_and_load():
    """Both new templates must exist at the path cli._test_template_path
    computes, and must contain the front-matter/citation shape
    test-writing-rules.md requires -- a template that doesn't parse as
    valid front matter would make every render using it fail validation
    silently confusingly (looks like a model problem, is actually a
    template problem)."""
    natural_path = REPO_ROOT / "templates" / "tests" / "natural_natunit.md"
    mantis_path = REPO_ROOT / "templates" / "tests" / "mantis_native.md"
    assert natural_path.exists()
    assert mantis_path.exists()

    natural_text = natural_path.read_text(encoding="utf-8")
    mantis_text = mantis_path.read_text(encoding="utf-8")

    for text, language, framework in (
        (natural_text, "natural", "natunit"),
        (mantis_text, "mantis", "native"),
    ):
        assert f"language: {language}" in text
        assert f"framework: {framework}" in text
        assert "{MEMBER}" in text
        assert "{MEMBER}:BR-nnn" in text
        assert f"```{language}" in text
        assert "* " in text or "*\n" in text  # a `*`-prefixed comment line is present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_test_batch.py -k test_natural_and_mantis_templates_exist_and_load -v`
Expected: FAIL — `assert False` on `natural_path.exists()`.

- [ ] **Step 3: Write minimal implementation**

Create `templates/tests/natural_natunit.md`:

```markdown
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
```

Create `templates/tests/mantis_native.md`:

```markdown
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_test_batch.py -k test_natural_and_mantis_templates_exist_and_load -v`
Expected: PASS

- [ ] **Step 5: Write and run a full render round-trip test using the fake-echo caller**

Add to `tests/test_test_batch.py`:

```python
def test_generate_member_test_doc_round_trips_natural_and_mantis(tmp_path):
    """A response shaped exactly like the natural/mantis templates must
    validate and split into the right sidecar extension -- proves the
    templates and testlang.py's new entries actually work together, not
    just that each exists in isolation."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    for language, framework, ext, fence_body in (
        ("natural", "natunit", "nsp",
         "* FAKEMOD:BR-001 [[FAKEMOD:1]]\nCALLNAT 'ASSERT-EQUAL' #EXPECTED #ACTUAL 'test_x'\n"),
        ("mantis", "native", "mantis",
         "* FAKEMOD:BR-001 [[FAKEMOD:1]]\nPERFORM FAKEMOD-UNDER-TEST\n"),
    ):
        doc_text = _valid_test_doc_text(language, framework).replace(
            "```python\ndef test_x():\n    # FAKEMOD:BR-001 [[FAKEMOD:1]]\n    pass\n```",
            f"```{language}\n{fence_body}```",
        )

        def caller(prompt, _doc_text=doc_text):
            return ModelResponse(text=_doc_text, input_tokens=1, output_tokens=2)

        out_path = tmp_path / language / "FAKEMOD.md"
        result = testbatch.generate_member_test_doc(
            conn, "FAKEMOD", language, framework, out_path, caller,
            "writing rules text", "template text",
        )
        assert result.ok is True, result.problems
        sidecar_path = out_path.with_suffix(f".{ext}")
        assert sidecar_path.exists()
        assert sidecar_path.read_text(encoding="utf-8") == fence_body
```

Run: `pytest tests/test_test_batch.py -k test_generate_member_test_doc_round_trips_natural_and_mantis -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add templates/tests/natural_natunit.md templates/tests/mantis_native.md tests/test_test_batch.py
git commit -m "Add Natural (NatUnit) and Mantis (native driver) test templates"
```

---

### Task 3: Silk Central and UiPath test-case templates

**Files:**
- Create: `templates/tests/silkcentral_testcase.md`
- Create: `templates/tests/uipath_testcase.md`
- Test: `tests/test_test_batch.py` (add new tests)

**Interfaces:**
- Consumes: nothing new (these two languages deliberately have no `LANGUAGE_EXTENSIONS` entry per Task 1/spec).
- Produces: `templates/tests/silkcentral_testcase.md`, `templates/tests/uipath_testcase.md`, loaded the same way as Task 2's templates — Task 6's example regeneration depends on both existing.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_test_batch.py`:

```python
def test_silkcentral_and_uipath_templates_exist_and_load():
    silkcentral_path = REPO_ROOT / "templates" / "tests" / "silkcentral_testcase.md"
    uipath_path = REPO_ROOT / "templates" / "tests" / "uipath_testcase.md"
    assert silkcentral_path.exists()
    assert uipath_path.exists()

    for path, language in ((silkcentral_path, "silkcentral"), (uipath_path, "uipath")):
        text = path.read_text(encoding="utf-8")
        assert f"language: {language}" in text
        assert "framework: testcase" in text
        assert "{MEMBER}" in text
        assert "{MEMBER}:BR-nnn" in text
        # No LANGUAGE_EXTENSIONS entry for these two -- the fence must not
        # claim a language tag testlang.py would try to split on its own
        # extension; it's still fenced, just under a neutral content tag.
        assert f"```{language}" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_test_batch.py -k test_silkcentral_and_uipath_templates_exist_and_load -v`
Expected: FAIL — `assert False` on `silkcentral_path.exists()`.

- [ ] **Step 3: Write minimal implementation**

Create `templates/tests/silkcentral_testcase.md`:

```markdown
---
title: "{MEMBER} — generated tests (silkcentral)"
doc_type: generated_test
system: "{SYSTEM}"
module: "{MEMBER}"
language: silkcentral
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

# {MEMBER} — generated tests (silkcentral / testcase)

One short paragraph: which scenarios are covered, which were skipped and
why (a gap from `mfdoc test-advisory`, an unreconstructable consequence),
and whether any `bug-desired` tests are expected to fail. Cite the module
as a whole [[{MEMBER}]] if nothing more specific applies.

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
```

Create `templates/tests/uipath_testcase.md`:

```markdown
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_test_batch.py -k test_silkcentral_and_uipath_templates_exist_and_load -v`
Expected: PASS

- [ ] **Step 5: Write and run a round-trip test confirming no sidecar split happens**

Add to `tests/test_test_batch.py`:

```python
def test_generate_member_test_doc_keeps_silkcentral_and_uipath_embedded(tmp_path):
    """No LANGUAGE_EXTENSIONS entry for these two -- the fence must stay
    embedded in the .md rather than being split to a fabricated sidecar
    extension. write_test_doc_with_sidecar returning None must not be
    mistaken for a validation failure -- generate_member_test_doc's
    result.ok must still be True."""
    from mfdoc import testbatch
    import sqlite3
    from mfdoc.db import SCHEMA, insert

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()

    for language in ("silkcentral", "uipath"):
        doc_text = _valid_test_doc_text(language, "testcase").replace(
            f"```{language}\ndef test_x():\n    # FAKEMOD:BR-001 [[FAKEMOD:1]]\n    pass\n```",
            f"```{language}\n# FAKEMOD:BR-001 [[FAKEMOD:1]]\n- test_case_id: FAKEMOD-BR-001\n```",
        )

        def caller(prompt, _doc_text=doc_text):
            return ModelResponse(text=_doc_text, input_tokens=1, output_tokens=2)

        out_path = tmp_path / language / "FAKEMOD.md"
        result = testbatch.generate_member_test_doc(
            conn, "FAKEMOD", language, "testcase", out_path, caller,
            "writing rules text", "template text",
        )
        assert result.ok is True, result.problems
        assert "test_case_id: FAKEMOD-BR-001" in out_path.read_text(encoding="utf-8")
        assert not any(out_path.parent.glob("FAKEMOD.*testcase*"))
```

Run: `pytest tests/test_test_batch.py -k test_generate_member_test_doc_keeps_silkcentral_and_uipath_embedded -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add templates/tests/silkcentral_testcase.md templates/tests/uipath_testcase.md tests/test_test_batch.py
git commit -m "Add Silk Central and UiPath test-case-definition templates"
```

---

### Task 4: `reference/test-writing-rules.md` — per-framework citation/assertion idiom

**Files:**
- Modify: `reference/test-writing-rules.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: prose only — no function/type interface. Verified by re-reading, not a unit test (this is a documentation contract, same tier as the existing pytest/JUnit5 callouts in the same file).

- [ ] **Step 1: Add a subsection after the existing "Citation format inside code" section**

Insert after the existing `python`/pytest example in `reference/test-writing-rules.md` (right after the fenced `python` example under "## Citation format inside code"):

```markdown
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
```

- [ ] **Step 2: Add a subsection after "Output artifacts after generation" clarifying which languages split to a sidecar**

Insert at the end of the existing "## Output artifacts after generation" section (after the sentence ending "... no extension is ever guessed."):

```markdown
As of this writing that split happens for `python` (`.py`), `java`
(`.java`), `natural` (`.nsp`), and `mantis` (`.mantis`). `silkcentral` and
`uipath` targets are test-case *definitions*, not source code in a
language with a stable file extension across every deployment — their
fence stays embedded in the `.md`, front matter and all, exactly as
written.
```

- [ ] **Step 3: Re-read the whole file once, confirm no contradiction**

Confirm the new subsections don't repeat or contradict the file's opening
generic front-matter example (`language: "{python|java|...}"` stays
correct as-is — it's already a generic placeholder, no edit needed there).

- [ ] **Step 4: Commit**

```bash
git add reference/test-writing-rules.md
git commit -m "Document natural/mantis/silkcentral/uipath conventions in test-writing-rules"
```

---

### Task 5: `_testgen_matrix` helper + `--matrix` support in `cli.py`

**Files:**
- Modify: `src/mfdoc/cli.py:275-278` (near `_testgen_config`), `:387-436` (`cmd_test_gen`), `:439-492` (`cmd_test_batch`), `:895-919` (argparse wiring)
- Test: `tests/test_test_batch.py` (add new tests)

**Interfaces:**
- Consumes: `options.testgen.matrix` (a list of `{"language": str, "framework": str, "template": str | None}` dicts) from project config, read the same way `_testgen_config(cfg)` already reads `default_language`/`out_dir`/etc.
- Produces: `cli._testgen_matrix(testgen_cfg: dict) -> list[dict]` — a plain list of target dicts (each guaranteed to have `"language"`/`"framework"` keys; `"template"` present only if the config entry set it). Task 6 does not depend on this directly (examples are regenerated via the CLI, not by importing this function), but Task 5's own tests do.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_test_batch.py`:

```python
def test_testgen_matrix_reads_config_list():
    from mfdoc.cli import _testgen_matrix

    assert _testgen_matrix({}) == []
    assert _testgen_matrix({"matrix": []}) == []
    targets = _testgen_matrix({
        "matrix": [
            {"language": "python", "framework": "pytest"},
            {"language": "natural", "framework": "natunit", "template": "custom.md"},
        ]
    })
    assert targets == [
        {"language": "python", "framework": "pytest"},
        {"language": "natural", "framework": "natunit", "template": "custom.md"},
    ]


def test_test_batch_matrix_and_language_are_mutually_exclusive(cli_args, indexed_db, tmp_path):
    from types import SimpleNamespace

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"), members=None,
        language="python", framework="pytest", template=None, model=None,
        caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
        concurrency=1, state="", matrix=True,
    )
    rc = cli.cmd_test_batch(args)
    assert rc == 2


def test_test_batch_matrix_requires_config_matrix_entries(tmp_path):
    """--matrix with no options.testgen.matrix in config must fail cleanly,
    the same way missing --language/--framework already does, not crash
    on an empty target list."""
    import shutil
    from types import SimpleNamespace

    project_dir = tmp_path / "proj"
    shutil.copytree(REPO_ROOT / "examples", project_dir / "examples")
    shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
    shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")
    cfg_text = (REPO_ROOT / "project.yml").read_text(encoding="utf-8")
    (project_dir / "project.yml").write_text(cfg_text, encoding="utf-8")

    args = SimpleNamespace(
        config=str(project_dir / "project.yml"), out=str(tmp_path / "out"), members=None,
        language=None, framework=None, template=None, model=None,
        caller="fake-echo", provider="anthropic", gcp_project=None, gcp_region=None,
        concurrency=1, state="", matrix=True,
    )
    # This project's checked-in options.testgen has no `matrix` key (until
    # Task 6 adds one) -- but even after Task 6 adds it, this test's
    # point is the *shape* of the error path, not this specific config's
    # absence of the key, so it stays valid either way as long as the
    # fixture project used here doesn't define one. Assert on the error
    # path directly instead of relying on that absence:
    from mfdoc import cli as cli_mod
    cfg = cli_mod.load_config(args.config)
    testgen_cfg = dict(cli_mod._testgen_config(cfg))
    testgen_cfg.pop("matrix", None)
    import unittest.mock as mock
    with mock.patch.object(cli_mod, "_testgen_config", return_value=testgen_cfg):
        rc = cli.cmd_test_batch(args)
    assert rc == 2


def test_test_gen_matrix_renders_every_configured_target(cli_args, indexed_db, tmp_path):
    from types import SimpleNamespace
    import shutil

    project_dir = Path(cli_args.config).parent
    if not (project_dir / "reference").exists():
        shutil.copytree(REPO_ROOT / "reference", project_dir / "reference")
        shutil.copytree(REPO_ROOT / "templates", project_dir / "templates")
    testplan.run_all(indexed_db, member_name="MMP0100")

    cfg = cli.load_config(cli_args.config)
    testgen_cfg = dict(cli._testgen_config(cfg))
    testgen_cfg["matrix"] = [
        {"language": "python", "framework": "pytest"},
        {"language": "natural", "framework": "natunit"},
    ]
    import unittest.mock as mock
    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "out"),
        member="MMP0100", language=None, framework=None, template=None,
        model=None, caller="fake-echo", provider="anthropic",
        gcp_project=None, gcp_region=None, matrix=True,
    )
    with mock.patch.object(cli, "_testgen_config", return_value=testgen_cfg):
        rc = cli.cmd_test_gen(args)
    assert rc == 0
    python_out = (tmp_path / "out" / "natural" / "MILLPROD" / "python" / "pytest" / "MMP0100.md")
    natural_out = (tmp_path / "out" / "natural" / "MILLPROD" / "natural" / "natunit" / "MMP0100.md")
    assert python_out.exists()
    assert natural_out.exists()
    assert "python/pytest" in python_out.read_text(encoding="utf-8")
    assert "natural/natunit" in natural_out.read_text(encoding="utf-8")


def test_test_gen_out_and_matrix_are_mutually_exclusive(cli_args, tmp_path):
    from types import SimpleNamespace

    args = SimpleNamespace(
        config=cli_args.config, out=str(tmp_path / "X.md"),
        member="MMP0100", language=None, framework=None, template=None,
        model=None, caller="fake-echo", provider="anthropic",
        gcp_project=None, gcp_region=None, matrix=True,
    )
    rc = cli.cmd_test_gen(args)
    assert rc == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_test_batch.py -k "testgen_matrix or matrix" -v`
Expected: FAIL — `AttributeError: module 'mfdoc.cli' has no attribute '_testgen_matrix'` and `AttributeError: 'SimpleNamespace' object has no attribute 'matrix'` (the CLI functions don't read `args.matrix` yet either).

- [ ] **Step 3: Implement `_testgen_matrix` and wire `--matrix` into both commands**

In `src/mfdoc/cli.py`, add right after `_testgen_config` (around line 278):

```python
def _testgen_matrix(testgen_cfg: dict) -> list[dict]:
    """options.testgen.matrix entries, or [] if absent -- each a
    {"language": ..., "framework": ..., "template": optional} dict, read
    verbatim from config. No built-in default matrix -- the set of
    destination targets a team wants is theirs to declare, not ours to
    guess (same posture CLAUDE.md already takes for redaction patterns
    and dialect assumptions)."""
    return list(testgen_cfg.get("matrix") or [])
```

Replace `cmd_test_gen` (the whole function, lines 393-436) with:

```python
def cmd_test_gen(args) -> int:
    from . import testbatch as testbatch_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    testgen_cfg = _testgen_config(cfg)

    if args.matrix and (args.language or args.framework):
        print("--matrix and --language/--framework are mutually exclusive -- "
              "pass one or the other", file=sys.stderr)
        return 2
    if args.matrix and args.out:
        print("--matrix renders multiple targets -- --out (a single path) doesn't "
              "apply; omit --out to use each target's default path", file=sys.stderr)
        return 2

    if args.matrix:
        targets = _testgen_matrix(testgen_cfg)
        if not targets:
            print("--matrix given, but no options.testgen.matrix entries in --config",
                  file=sys.stderr)
            return 2
    else:
        language = args.language or testgen_cfg.get("default_language")
        framework = args.framework or testgen_cfg.get("default_framework")
        if not language or not framework:
            print("no --language/--framework given, and no options.testgen.default_language/"
                  "default_framework in --config", file=sys.stderr)
            return 2
        targets = [{"language": language, "framework": framework}]

    writing_rules = (base / "reference" / "test-writing-rules.md").read_text(encoding="utf-8")
    caller = _build_model_caller(args)
    if caller is None:
        return 1

    from .batch import _output_subdir

    member = args.member.strip().upper()
    out_dir = testgen_cfg.get("out_dir") or "tests_generated"
    any_failed = False
    for target in targets:
        language, framework = target["language"], target["framework"]
        template_override = target.get("template") or args.template
        template_path = _test_template_path(base, language, framework, template_override)
        if not template_path.exists():
            print(f"no template at {template_path} -- pass --template, or add one for "
                  f"--language {language} --framework {framework}", file=sys.stderr)
            any_failed = True
            continue
        template = template_path.read_text(encoding="utf-8")

        out_path = (base / args.out if args.out
                    else base / out_dir / _output_subdir(conn, member) / language / framework / f"{member}.md")
        result = testbatch_mod.generate_member_test_doc(
            conn, member, language, framework, out_path, caller,
            writing_rules, template, redact=redact,
        )
        status = "OK" if result.ok else "FAIL"
        print(f"{status} {result.member} [{language}/{framework}] -> {result.path} "
              f"attempts={result.attempts} in={result.input_tokens} out={result.output_tokens}")
        for p in result.problems:
            print(f"  - {p}")
        any_failed = any_failed or not result.ok
    return 1 if any_failed else 0
```

Replace `cmd_test_batch` (the whole function, lines 439-492) with:

```python
def cmd_test_batch(args) -> int:
    """Batch harness for generated tests -- the same option-C treatment
    `mfdoc batch` gives module docs, applied to test_case rows instead of
    module facts. Run `mfdoc test-plan` first; this never derives facts."""
    from . import testbatch as testbatch_mod

    cfg = load_config(args.config)
    base = Path(args.config).parent
    conn = connect(base / cfg["index_db"])
    redact = Redactor.from_options(cfg["options"])
    testgen_cfg = _testgen_config(cfg)

    if args.matrix and (args.language or args.framework):
        print("--matrix and --language/--framework are mutually exclusive -- "
              "pass one or the other", file=sys.stderr)
        return 2

    if args.matrix:
        targets = _testgen_matrix(testgen_cfg)
        if not targets:
            print("--matrix given, but no options.testgen.matrix entries in --config",
                  file=sys.stderr)
            return 2
    else:
        language = args.language or testgen_cfg.get("default_language")
        framework = args.framework or testgen_cfg.get("default_framework")
        if not language or not framework:
            print("no --language/--framework given, and no options.testgen.default_language/"
                  "default_framework in --config", file=sys.stderr)
            return 2
        targets = [{"language": language, "framework": framework}]

    out_dir = args.out or testgen_cfg.get("out_dir") or "tests_generated"

    members = ([m.strip().upper() for m in args.members.split(",")] if args.members
               else testbatch_mod.select_test_batch_members(conn))
    if not members:
        print("no test_case rows in the index -- run `mfdoc test-plan` first")
        return 0

    writing_rules = (base / "reference" / "test-writing-rules.md").read_text(encoding="utf-8")
    caller = _build_model_caller(args)
    if caller is None:
        return 1

    grand_ok = grand_failed = grand_skipped = 0
    any_target_failed = False
    for target in targets:
        language, framework = target["language"], target["framework"]
        template_override = target.get("template") or args.template
        template_path = _test_template_path(base, language, framework, template_override)
        if not template_path.exists():
            print(f"no template at {template_path} -- pass --template, or add one for "
                  f"--language {language} --framework {framework}; skipping this target",
                  file=sys.stderr)
            any_target_failed = True
            continue
        template = template_path.read_text(encoding="utf-8")

        if len(targets) > 1:
            print(f"\n=== {language}/{framework} ===")
        summary = testbatch_mod.run_test_batch(
            conn, members, language, framework, base / out_dir, caller,
            writing_rules, template, redact=redact, concurrency=args.concurrency,
            state_path=(base / args.state) if args.state else None,
        )
        for r in summary.results:
            status = "SKIP" if r.skipped else ("OK  " if r.ok else "FAIL")
            print(f"{status} {r.member:<20} attempts={r.attempts} in={r.input_tokens} out={r.output_tokens}")
            for p in r.problems:
                print(f"       - {p}")
        print(f"\n{summary.ok}/{len(summary.results)} ok, {summary.failed} failed, "
              f"{summary.skipped} skipped (unchanged)")
        print(f"tokens: {summary.total_input_tokens} in, {summary.total_output_tokens} out")
        grand_ok += summary.ok
        grand_failed += summary.failed
        grand_skipped += summary.skipped
        any_target_failed = any_target_failed or summary.failed > 0

    if len(targets) > 1:
        print(f"\n=== grand total across {len(targets)} targets ===")
        print(f"{grand_ok} ok, {grand_failed} failed, {grand_skipped} skipped (unchanged)")

    return 1 if any_target_failed else 0
```

In the argparse section (around line 895-919), add `--matrix` to the shared loop:

```python
    for name, fn in (("test-gen", cmd_test_gen), ("test-batch", cmd_test_batch)):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        p.add_argument("--language", default=None,
                        help="e.g. python, java; default: options.testgen.default_language "
                             "from --config -- no built-in default either way")
        p.add_argument("--framework", default=None,
                        help="e.g. pytest, junit5; default: options.testgen.default_framework "
                             "from --config -- no built-in default either way")
        p.add_argument("--matrix", action="store_true",
                        help="render every {language, framework} pair in "
                             "options.testgen.matrix from --config, instead of one "
                             "--language/--framework target; mutually exclusive with "
                             "--language/--framework")
        p.add_argument("--template", help="override the default templates/tests/{language}_{framework}.md")
        p.add_argument("--model", default=None)
        p.add_argument("--caller", choices=["anthropic", "fake-echo"], default="anthropic",
                        help="fake-echo makes no network call -- for CI/dry-run smoke tests")
        p.add_argument("--provider", choices=["anthropic", "vertex", "claude-code"], default="anthropic")
        p.add_argument("--gcp-project")
        p.add_argument("--gcp-region")
        p.set_defaults(func=fn)
```

(Only the new `--matrix` line is added; everything else in that loop is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_test_batch.py -k "testgen_matrix or matrix" -v`
Expected: PASS (5 new tests)

- [ ] **Step 5: Run the full existing test-batch/test-gen suite to confirm no regression**

Run: `pytest tests/test_test_batch.py -v`
Expected: all PASS (existing single-target tests must still pass unchanged — `args.matrix` defaults to falsy on any `SimpleNamespace` that predates this change would raise `AttributeError`, so also grep for other test files constructing `cmd_test_gen`/`cmd_test_batch` args)

Run: `grep -rn "cmd_test_gen\|cmd_test_batch" tests/ --include="*.py" -l`
For every file found besides `tests/test_test_batch.py`, add `matrix=False` to any `SimpleNamespace(...)` call that doesn't already have it (existing calls in `test_test_batch.py` itself were updated in Steps 1-3 above; check for others, e.g. `tests/test_cli_batch.py` if it happens to also construct these args — most likely it doesn't, since it's module-doc batch, not test-batch, but confirm).

- [ ] **Step 6: Commit**

```bash
git add src/mfdoc/cli.py tests/test_test_batch.py
git commit -m "Add --matrix support to mfdoc test-gen/test-batch"
```

---

### Task 6: Config, docs, and example regeneration

**Files:**
- Modify: `project.yml`
- Modify: `CLAUDE.md`
- Modify: `docs/guides/testing-strategies-for-mainframes-and-4gl.md`
- Modify: `examples/outputs/README.md`
- Regenerate: `examples/outputs/tests/**`

**Interfaces:**
- Consumes: `--matrix` from Task 5, all six templates from Tasks 2-3.
- Produces: real example output under `examples/outputs/tests/` for every configured target — the deliverable a reviewer actually checks (real generated Natural/Mantis/Silk Central/UiPath test docs, not just passing unit tests).

- [ ] **Step 1: Add `options.testgen.matrix` to `project.yml`**

In `project.yml`, find the `testgen:` block (around line 128) and add a `matrix` key after `overlay_path`/`out_dir` (keep whatever those two currently are; only add the new key):

```yaml
  testgen:
    # ... existing default_language/default_framework/overlay_path/out_dir
    # comments and values, unchanged ...
    # The full set of destination targets this project wants rendered by
    # `mfdoc test-batch --matrix` / `mfdoc test-gen --matrix` in one pass.
    # No built-in default matrix -- this is the same "declare it, don't
    # guess it" posture options.testgen already takes for
    # default_language/default_framework.
    matrix:
      - {language: python, framework: pytest}
      - {language: natural, framework: natunit}
      - {language: mantis, framework: native}
      - {language: silkcentral, framework: testcase}
      - {language: uipath, framework: testcase}
```

- [ ] **Step 2: Run the existing full suite once to confirm the config edit alone doesn't break anything**

Run: `pytest -x`
Expected: all PASS (adding a config key nothing reads yet except the new `--matrix` path shouldn't affect any other test; `project_config` fixture copies this file verbatim, so this also exercises Task 5's `_testgen_matrix` against the real config shape for the first time)

- [ ] **Step 3: Update `CLAUDE.md`'s command list**

In `CLAUDE.md`, after the existing line:

```
mfdoc test-gen      --config project.yml --member NAME --language python --framework pytest
```

add:

```
mfdoc test-gen      --config project.yml --member NAME --matrix   # every options.testgen.matrix target for one member
mfdoc test-batch    --config project.yml --matrix                # every options.testgen.matrix target, every batchable member
```

- [ ] **Step 4: Update `docs/guides/testing-strategies-for-mainframes-and-4gl.md`**

Change the line:

```
scenarios," expressed in a form (`pytest`, `JUnit`, or the source dialect
itself) that a migration team already knows how to run, read, and extend.
```

to:

```
scenarios," expressed in a form (`pytest`, `JUnit`, the source dialect
itself via `natural`/`natunit` or `mantis`/`native`, or a test-case
definition for `silkcentral`/`uipath` to import) that a migration team
already knows how to run, read, or track.
```

In the "What each command operationalizes" table, change the `test-gen`/`test-batch` row's description to end with:

```
... a characterization test, a spec test, or an `xfail`-marked
bug-desired test, depending on that scenario's (human-confirmed) status.
`silkcentral`/`uipath` targets render a test-case *definition* for import
instead of executable code -- this tool still has no way to drive a 3270
screen end-to-end (see the test pyramid section above); those two targets
operationalize test-case tracking, not UI automation.
```

- [ ] **Step 5: Regenerate `examples/outputs/tests/` for real**

```bash
pip install -e '.[dev]'
mfdoc ingest         --config project.yml
mfdoc derive         --config project.yml
mfdoc test-plan      --config project.yml --out examples/outputs/test-plan-register.md
mfdoc test-advisory  --config project.yml --out examples/outputs/testability-advisory.md
mfdoc test-batch     --config project.yml --out examples/outputs/tests --matrix --provider claude-code
```

- [ ] **Step 6: Validate the regenerated output**

```bash
mfdoc test-validate --config project.yml --docs examples/outputs/tests
```

Expected: 0 invalid citations. If any target's output fails validation,
re-run just that target (`mfdoc test-batch --config project.yml --out
examples/outputs/tests --language <lang> --framework <fw> --provider
claude-code`) and inspect the printed `problems` list — do not hand-edit
a generated file to force it to pass.

- [ ] **Step 7: Update `examples/outputs/README.md`'s layout tree and reproduction script**

Replace the `tests/` block in the "Layout" fenced tree:

```
  tests/
    natural/MILLPROD/python/pytest/{MEMBER}.md + {MEMBER}.py
    mantis/STEELLIB/python/pytest/{MEMBER}.md + {MEMBER}.py
```

with:

```
  tests/
    natural/MILLPROD/python/pytest/{MEMBER}.md + {MEMBER}.py
    natural/MILLPROD/natural/natunit/{MEMBER}.md + {MEMBER}.nsp
    natural/MILLPROD/mantis/native/{MEMBER}.md + {MEMBER}.mantis   # only where a natural member's brief happens to be rendered under a mantis target -- see note below
    natural/MILLPROD/silkcentral/testcase/{MEMBER}.md
    natural/MILLPROD/uipath/testcase/{MEMBER}.md
    mantis/STEELLIB/python/pytest/{MEMBER}.md + {MEMBER}.py
    mantis/STEELLIB/natural/natunit/{MEMBER}.md + {MEMBER}.nsp
    mantis/STEELLIB/mantis/native/{MEMBER}.md + {MEMBER}.mantis
    mantis/STEELLIB/silkcentral/testcase/{MEMBER}.md
    mantis/STEELLIB/uipath/testcase/{MEMBER}.md
```

Correction before committing this step: `--matrix` renders *every*
configured target for *every* selected member regardless of that
member's own source dialect (the target language is the destination, not
a filter on source) — so a Natural member does get a `mantis/native`
rendering (a Mantis-syntax driver testing a Natural program, which reads
oddly but is what was configured) unless the config narrows it. Look at
what `mfdoc test-batch --matrix` in Step 5 actually produced under
`examples/outputs/tests/natural/MILLPROD/` and `.../mantis/STEELLIB/` and
write the tree to match reality exactly, rather than the illustrative
sketch above — this step's job is to describe the real output, not
prescribe it. Delete the "only where..." comment; it was a placeholder
for you to resolve by looking at the actual directory listing (`find
examples/outputs/tests -type f | sort`), not something to leave in the
committed file.

Then replace the reproduction script's test-generation block:

```bash
mfdoc test-plan      --config project.yml --out examples/outputs/test-plan-register.md
mfdoc test-advisory  --config project.yml --out examples/outputs/testability-advisory.md

# Module docs and generated tests -- real narrative-pass output, via the local
# claude CLI instead of an API key (needs Claude Code installed and
# authenticated; drop --provider claude-code and add --model/ANTHROPIC_API_KEY
# to use the Anthropic API directly instead):
mfdoc batch      --config project.yml --out examples/outputs/docs --provider claude-code
mfdoc test-batch --config project.yml --out examples/outputs/tests \
                  --language python --framework pytest --provider claude-code
```

with:

```bash
mfdoc test-plan      --config project.yml --out examples/outputs/test-plan-register.md
mfdoc test-advisory  --config project.yml --out examples/outputs/testability-advisory.md

# Module docs and generated tests -- real narrative-pass output, via the local
# claude CLI instead of an API key (needs Claude Code installed and
# authenticated; drop --provider claude-code and add --model/ANTHROPIC_API_KEY
# to use the Anthropic API directly instead):
mfdoc batch      --config project.yml --out examples/outputs/docs --provider claude-code
# --matrix renders every options.testgen.matrix target (python/pytest,
# natural/natunit, mantis/native, silkcentral/testcase, uipath/testcase)
# for every batchable member in one pass:
mfdoc test-batch --config project.yml --out examples/outputs/tests \
                  --matrix --provider claude-code
```

Update the "What's auto-refreshed" table's `tests/natural/`, `tests/mantis/`
row description if it names `python`/`pytest` specifically — check the
current wording and generalize it to "every configured `options.testgen.matrix`
target" if it does.

- [ ] **Step 8: Run the full suite plus the CI-equivalent commands one more time**

```bash
pytest
mfdoc validate      --config project.yml --docs examples
mfdoc test-validate --config project.yml --docs examples/outputs/tests
```

Expected: unit suite all PASS; both validate commands report 0 invalid
citations across the whole tree.

- [ ] **Step 9: Commit**

```bash
git add project.yml CLAUDE.md docs/guides/testing-strategies-for-mainframes-and-4gl.md \
        examples/outputs/README.md examples/outputs/tests examples/outputs/test-plan-register.md \
        examples/outputs/testability-advisory.md
git commit -m "Add testgen matrix config, regenerate examples for all five destination targets"
```

---

## Self-Review Notes (already applied above)

- **Spec coverage:** all four new templates (Task 2, 3), `LANGUAGE_EXTENSIONS`
  entries (Task 1), `--matrix` on both commands with the exact mutual-exclusion
  and empty-matrix error behavior from the spec (Task 5), `test-writing-rules.md`
  updates (Task 4), all doc/config updates and example regeneration (Task 6) —
  every spec section maps to a task.
- **Placeholder scan:** Task 6 Step 7's layout-tree edit intentionally tells the
  implementer to look at real output rather than hand-guess it — that's a
  deliberate "verify against reality" instruction, not a TBD; it comes with the
  exact command (`find examples/outputs/tests -type f | sort`) to resolve it,
  and explicit instruction to delete the placeholder comment before committing.
- **Type consistency:** `_testgen_matrix(testgen_cfg: dict) -> list[dict]` is
  defined once in Task 5 and used exactly that way in Task 5's own tests; no
  other task calls it directly. `target.get("template") or args.template`
  precedence (per-entry override wins, then the global `--template` flag) is
  stated identically in both `cmd_test_gen` and `cmd_test_batch`.
