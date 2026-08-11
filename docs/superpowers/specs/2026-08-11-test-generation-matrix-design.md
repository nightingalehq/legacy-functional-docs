# Design: destination-language test generation matrix

Date: 2026-08-11
Status: approved (pending final spec review)

## Problem

`mfdoc test-gen`/`mfdoc test-batch` currently render generated tests into
exactly one `{language}_{framework}.md` template at a time, and only two
templates exist: `python_pytest` and `java_junit5`. Every generated test in
`examples/outputs/tests/` is therefore Python, even for Mantis-dialect
source, which defeats a chunk of the value for a team whose target isn't a
modern language at all — a mainframe team piloting this tool wants to see
tests it can run in its own environment (Natural, Mantis) before it trusts
tests in a language it hasn't migrated to yet, and some teams manage test
execution through a platform (Silk Central, UiPath) rather than a
language's own test runner.

Two gaps to close:

1. No templates target the source dialects themselves, or common
   mainframe-adjacent test-management/RPA tooling.
2. Every render is a separate manual CLI invocation naming one
   `--language`/`--framework` pair — there's no way to say "render this
   member (or this whole batch) into every target my team cares about" in
   one command.

## Non-goals

- No change to `mfdoc test-plan`/`test-advisory`/`test-overlay*` — this is
  entirely about the render stage (`test-gen`/`test-batch`) and its
  templates.
- No executable end-to-end/UI-driving automation (a SilkTest 4Test script
  or a UiPath Coded Test that actually drives a 3270 emulator). Silk
  Central and UiPath targets here produce structured, cited Given/When/Then
  test-case *definitions* for import into those tools' own case
  repositories — the same tier as every other generated-test target
  (derived from source facts, reviewed by a human before being trusted),
  not a new end-to-end-testing capability. `docs/guides/testing-strategies-
  for-mainframes-and-4gl.md`'s existing statement that this tool has no way
  to drive a 3270 screen remains true and unchanged.
- No change to `validate.py`, `testbatch.py`'s render/retry/state logic, or
  `testlang.py`'s "never fabricate an extension for an unrecognised
  language" rule — the matrix feature is purely about *how many* targets a
  single CLI invocation loops over; each target still goes through the
  existing single-target render path unchanged.

## New templates

Four new `templates/tests/{language}_{framework}.md` files, matching the
existing `python_pytest.md`/`java_junit5.md` shape exactly (front matter,
one-paragraph human-readable summary, single fenced code/content block,
`MEMBER:BR-nnn [[MEMBER:LINE]]` citation comments):

| language | framework | sidecar ext | shape |
|---|---|---|---|
| `natural` | `natunit` | `.nsp` | A runnable Natural test program, NatUnit convention (`CALLNAT 'ASSERT-EQUAL'`/`'ASSERT-TRUE'` etc.), one assertion block per `BR-nnn` |
| `mantis` | `native` | `.mantis` | A runnable Mantis driver program (no real Mantis test framework exists to target) — PERFORMs the unit under test, compares actual vs. expected, DISPLAYs PASS/FAIL per scenario |
| `silkcentral` | `testcase` | *(none)* | Structured Given/When/Then test-case steps in Silk Central's case shape, for import |
| `uipath` | `testcase` | *(none)* | Same idea, shaped for UiPath Test Manager's manual/data-driven test case format |

Both Natural and Mantis templates use `*`-prefixed comments for citations —
the comment prefix both dialects' own extractors already recognise
(`natural.py`'s `^\*\s`/`stripped == "*"` check, `mantis.py`'s
`COMMENT_PREFIXES`), so a generated test file would itself parse cleanly as
source if it were ever run back through `mfdoc ingest`.

`silkcentral`/`uipath` deliberately get **no** entry in `testlang.py`'s
`LANGUAGE_EXTENSIONS` map. That module's existing contract — "an
unrecognised language means keep the code embedded in the `.md`, don't
split it, never a fabricated extension" — is exactly right here: real Silk
Central/UiPath deployments customize their import schema per project, and
guessing a `.csv`/`.xml` extension would be exactly the kind of
client-specific assumption `CLAUDE.md` says not to bake in. Both new
templates' prose says so explicitly, so a reviewer isn't left wondering why
those two don't split into sidecar files like the other four.

`src/mfdoc/testlang.py` changes:

```python
LANGUAGE_EXTENSIONS = {
    "python": "py",
    "java": "java",
    "natural": "nsp",
    "mantis": "mantis",
}
```

No other code changes are needed for the new templates to work —
`testbatch.py`'s prompt construction, retry loop, and `validate.py`'s
front-matter/citation checks are already language-agnostic (confirmed by
reading both modules; the only language-aware code path is the
`LANGUAGE_EXTENSIONS` lookup above).

## Matrix support

New `options.testgen.matrix` config key: a list of `{language, framework}`
pairs, each optionally carrying a `template` override (same meaning as
today's `--template` flag, per-entry instead of global):

```yaml
options:
  testgen:
    default_language: python        # unchanged -- single-target default
    default_framework: pytest
    matrix:                         # new -- the set of targets a team wants
      - {language: python, framework: pytest}
      - {language: natural, framework: natunit}
      - {language: mantis, framework: native}
      - {language: silkcentral, framework: testcase}
      - {language: uipath, framework: testcase}
```

`default_language`/`default_framework` and `matrix` are independent knobs —
the former is what `--language`/`--framework` fall back to when neither is
given and `--matrix` isn't passed; the latter only takes effect when
`--matrix` is passed. Neither is required for the other to work.

### CLI changes

Both `test-gen` and `test-batch` gain a `--matrix` boolean flag:

- `--matrix` and `--language`/`--framework` are mutually exclusive (error,
  exit 2, if both given — ambiguous which the user actually wants).
- `--matrix` with no `options.testgen.matrix` in config is an error (exit
  2), same message style as today's missing-language/framework error.
- **`mfdoc test-batch --matrix`**: resolves the target list from config,
  then for each `{language, framework}` entry runs the existing
  `run_test_batch` unchanged (members still render concurrently *within*
  one target; targets run one after another, not concurrently with each
  other, to keep output/log interleaving simple and avoid nested thread
  pools). Output nests exactly as it does today —
  `out_dir/<subdir>/<language>/<framework>/<member>.md` — so no path
  collisions between targets. A single `--state` file is reused across the
  whole matrix; this is already safe because `run_test_batch`'s state keys
  already include `language`/`framework` (`f"{subdir}::{member}::
  {language}::{framework}"`), so one target's skip/resume bookkeeping can't
  collide with another's. Prints each target's existing per-member
  OK/FAIL/SKIP lines under a `=== {language}/{framework} ===` header, then
  one grand-total line across all targets. Exit code is non-zero if any
  target has `failed > 0`, or if a target's template file is missing (that
  target is skipped with a printed warning, not a hard abort of the whole
  matrix — one missing template shouldn't block every other target from
  rendering).
- **`mfdoc test-gen --member X --matrix`**: same target-list resolution,
  calls the existing `generate_member_test_doc` once per target, writing
  to each target's default path (`out_dir/<subdir>/<language>/<framework>/
  {member}.md` — the same default `test-gen` already computes when `--out`
  is omitted). `--out` is rejected together with `--matrix` (exit 2) since
  a single explicit path can't hold multiple targets' output.

### Implementation shape

A new small helper in `cli.py`, used by both commands, resolving config +
flags into the list of targets to render (kept as a plain function, no new
module — this is a few lines of list-building, not new architecture):

```python
def _testgen_matrix(testgen_cfg: dict) -> list[dict]:
    """options.testgen.matrix entries, or [] if absent -- each a
    {"language":..., "framework":..., "template": optional} dict."""
    return (testgen_cfg.get("matrix") or [])
```

`cmd_test_batch`/`cmd_test_gen` branch near the top on `args.matrix`: if
set, resolve targets via `_testgen_matrix`, validate non-empty, then loop
the existing per-target body (template lookup, `_build_model_caller` once
reused across targets, render call) that today runs once for the single
`--language`/`--framework` case. This is a refactor of the existing
function bodies into "resolve target(s) → loop", not new rendering logic.

## Docs to update

- `reference/test-writing-rules.md` — extend the front-matter example
  (`language`/`framework` already generic `{python|java|...}`) with a short
  subsection per new framework covering its citation-comment/assertion
  idiom, mirroring the existing pytest/JUnit5 callouts.
- `docs/guides/testing-strategies-for-mainframes-and-4gl.md` — update the
  `test-gen`/`test-batch` table row and the "pytest, JUnit, or the source
  dialect itself" line to name the concrete new options; note the Silk
  Central/UiPath targets are test-case definitions, not automation scripts,
  next to the existing 3270/end-to-end out-of-scope statement so the
  distinction is explicit in one place.
- `CLAUDE.md` — mention `--matrix` alongside the existing `test-gen`/
  `test-batch` command examples.
- `project.yml` — extend the `testgen` options comment block with the
  `matrix` example above.
- `examples/outputs/README.md` — update the layout tree (new
  `tests/natural/MILLPROD/natural/natunit/`, `.../mantis/native/`,
  `.../silkcentral/testcase/`, `.../uipath/testcase/` subtrees) and replace
  the reproduction script's single `test-batch --language python --framework
  pytest` call with one `mfdoc test-batch --matrix` call (once
  `options.testgen.matrix` is set in `project.yml`).

## Regenerating examples

Once `project.yml`'s `testgen.matrix` lists the five targets (python
included, so the existing pytest output stays reproducible from the same
one command), run:

```bash
mfdoc test-batch --config project.yml --out examples/outputs/tests \
                  --matrix --provider claude-code
mfdoc test-validate --config project.yml --docs examples/outputs/tests
```

This regenerates Python output for all 7 members (6 `MILLPROD` Natural +
`ORDENQ` Mantis) exactly as it exists today, plus Natural/Mantis/Silk
Central/UiPath output for the same 7 members. `test-validate` must report 0
invalid citations across the new files, same bar as every other generated
artifact in `examples/outputs/`.

## Testing

- Unit tests for `_testgen_matrix` (empty config → `[]`, entries pass
  through) and the new `--matrix`/`--language` mutual-exclusion and
  empty-matrix error paths in `cmd_test_batch`/`cmd_test_gen` (exit code 2,
  stderr message), using the existing `fake-echo` caller — no network call,
  matching how `test_batch.py` already tests the single-target path.
  Reuse the existing `fake-echo`-driven test project/fixture rather than
  building a new one.
- A `run_test_batch`-level test asserting that running the same members
  through two different targets in one `--matrix` invocation produces two
  independent output subtrees and two independent state entries (proves
  the shared-state-file claim above, not just asserts it in prose).
- Extend `test_citation_alignment.py`/whichever test currently checks
  `LANGUAGE_EXTENSIONS`-driven sidecar splitting to cover `natural`/
  `mantis` producing a `.nsp`/`.mantis` sidecar, and `silkcentral`/`uipath`
  *not* splitting (fence stays embedded) — both are meaningful contracts
  worth locking down, not just asserted in the design doc.
- No new golden fixtures under `examples/inputs/` — this only touches the
  render stage, downstream of ingest/derive, so the existing Natural/Mantis
  fixtures already provide real `test_case` rows to render from.
