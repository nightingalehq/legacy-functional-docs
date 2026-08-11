# Developer guide: extending the solution

Audience: engineers adding capability to this repo — a new dialect, a new
document type, a new CLI command, or changes to the narrative/validation
stages. Read [architecture.md](architecture.md) first if you haven't; this
guide assumes that mental model and gets into how to change things safely.

## Before changing anything

Read the caveat at the end of
[`docs/plans/legacy-functional-docs-plan.md`](../plans/legacy-functional-docs-plan.md):
several patterns in `mantis.py` and `supra.py` that look redundant or
overly specific are not — they're the fix for a real, previously-shipped
defect. If a pattern looks wrong, check the tests and the comment above it
before "simplifying" it. `tests/test_call_graph_and_entities.py` and
`tests/test_citation_alignment.py` encode the specific defect classes that
already occurred once; they exist so scepticism about a change is enforced
by the suite, not by whoever reviews the diff.

Run the suite before and after any change:

```bash
pip install -e '.[dev]'
pytest
```

CI (`.github/workflows/ci.yml`) additionally runs the full pipeline against
`examples/inputs` and validates the output — a change that breaks
citation alignment or introduces an unresolved citation goes red even if
every unit test passes, so run that locally too before pushing anything
touching extraction or narrative:

```bash
mfdoc ingest   --config project.yml
mfdoc derive   --config project.yml
mfdoc coverage --config project.yml
mfdoc validate --config project.yml --docs examples
```

## Adding a dialect

Fully specified in
[`reference/adding-a-dialect.md`](../../reference/adding-a-dialect.md) —
that's the contract document, kept authoritative and separate from this
guide so it doesn't drift. In short: implement `extract(conn, member_id,
lines, member_name) -> dict`, insert every line (including comments and
blanks) into `source_line`, and record a `gap` for anything not understood
rather than skipping it silently. Register the dialect in
`normalise.DIALECT_SIGNATURES`, `cli.DIALECT_ROUTER` and
`cli.DIALECT_DEFAULT_TYPE`, then add a fixture.

The two rules that matter most, repeated here because they're easy to
violate accidentally while iterating:

1. **A line the extractor doesn't insert into `source_line` can never be
   cited.** The validator resolves every `[[MEMBER:LINE]]` against that
   table; a gap in it is a silent hole in every document downstream.
2. **Mask literals before keyword-matching, then recover the original text
   by offset** (`natural.mask_literals` / `natural.orig` is the reference
   implementation). Storing the masked form loses exactly the values the
   documentation exists to report — `IF STATUS NE 'CONF'` without `CONF` is
   useless. This is the single defect class most likely to reappear if a
   new dialect's pattern-matching is written without this in mind.

## Adding a new document type

The seven document types under `templates/` are load-bearing on
`reference/writing-rules.md` and on `validate.py`'s expectations of front
matter. To add an eighth:

1. Add `templates/<name>.md` describing the required front matter and
   section structure — follow the existing templates' shape exactly, since
   the validator and (for module docs) `batch.py`'s prompt construction
   both assume front matter keys are stable.
2. If it needs new derived data, add it in `graph.py` (see `crud_matrix`,
   `orphans`, `transaction_scopes` for the existing pattern: a pure function
   over the fact store, no model calls, returning a plain dict/list).
3. Add a brief function in `brief.py` alongside `module_brief` /
   `entity_brief` / `system_brief` if the new document type needs its own
   fact summary shape.
4. Decide whether it belongs in the batch path or the interactive path —
   see "Two narrative paths" in architecture.md. High-volume and formulaic
   → batch-eligible; anything needing judgement about grouping or structure
   across the whole system → interactive only, and should stay out of
   `batch.select_batch_members`.
5. Update `SKILL.md`'s "Suggested document set" if it changes the
   recommended order.

## Adding a CLI command

Follow the existing shape in `cli.py`: a `cmd_<name>(args) -> int` function
that loads config, connects to the database, does its work, prints
human-readable output, and returns a process exit code (0 = success,
non-zero = failure a caller should notice — see `cmd_gate` and
`cmd_validate` for the pattern of returning 1 on a substantive failure
rather than only on a Python exception). Register a subparser in `main()`.

If the command evaluates something against a threshold or existing
convention (like `mfdoc gate`), print *why* a check failed and what it
blocks, in the same style as `GATES` in `cli.py` — this project treats a
failure message as documentation in its own right, since the person
reading it is often not the person who wrote the check.

## Working on the narrative stage

`batch.py` and `anthropic_caller.py` are deliberately separated so
`anthropic` stays an optional dependency — anything that imports `batch`
directly (not via `cli.py`'s lazy `from . import batch as batch_mod` inside
`cmd_batch`) must not require `anthropic` to be installed to be imported
and tested. Use the `fake-echo` caller (see `cmd_batch`'s `--caller`
argument) for tests and dry runs that shouldn't need a network call or an
API key — `test_batch.py` does this.

If you change `build_prompt` or the retry logic, keep in mind the retry is
one attempt, with the validator's failure text appended to the prompt —
don't add unbounded retries; a module that fails twice should surface as a
`FAIL` in `run_batch`'s summary for a human to look at, not loop silently.

## Testing conventions

- Fixtures in `examples/inputs/` are golden — most tests run the real
  pipeline against them and assert on specific facts (see
  `tests/test_call_graph_and_entities.py` for the density of what's
  checked: exact entity counts, specific resolved/unresolved calls,
  specific literal values surviving masking). Prefer extending an existing
  fixture over adding a new one unless a change genuinely can't be tested
  against what's there — new fixtures are expensive to keep meaningful.
- `tests/test_citation_alignment.py` is the most important test in the
  suite structurally: it asserts every `source_line` row matches the file
  on disk at the member's `first_line` offset, for every member. This is
  the check that would have caught the original off-by-one defect from
  banner-line handling, which silently invalidated every citation in a
  run's output. If you touch `normalise.py`'s line-splitting or
  banner-handling logic, this test is the one to watch.
- When you add a scanner rule intended to catch a business-meaningful
  construct (an arithmetic rule, a new call kind, whatever), add a test
  that asserts on the *unmasked* value surviving into the fact store, not
  just that a row was inserted. A row existing with the wrong content is a
  worse failure than a missing row, because it looks like success.

## Supplementary smoke fixtures from public corpora

`examples/inputs/` is small and golden-tested; it was written to exercise
specific known defect classes, not to be representative of real source's
variety. Where a public, appropriately-licensed corpus exists, it's worth
using as a supplementary robustness signal without ever committing it
wholesale (not ours to redistribute, and upstream can change underneath us).

- `scripts/fetch_cobol_course_fixtures.py` pulls a small, pinned-by-commit-SHA
  set of JCL files (including DB2 JCL with embedded SQL DDL) from
  `openmainframeproject/cobol-programming-course` (CC-BY-4.0) into
  `examples/external/cobol_course/` — gitignored, opt-in, not run in CI.
- `tests/test_external_fixtures_smoke.py` skips automatically unless that
  directory has been populated. Run the fetch script first, then
  `pytest tests/test_external_fixtures_smoke.py -v`. It only asserts nothing
  crashes and the recognition rate stays above a loose floor — it does not
  extend `EXPECTED_COVERAGE` in `test_coverage_snapshot.py`, which stays keyed
  to the checked-in fixture set.
- Any real gap this surfaces becomes its own issue with a small, targeted
  fixture extracted from the specific failing line shape (the same way
  `MMP9000.nsp`/`MMC0100.nsc` were built) — never by committing the source
  corpus itself. See issue #19/#24-26 for the precedent this followed against
  `SoftwareAG/adabas-natural-code-samples`.

## Measuring scale (issue #9)

`examples/inputs/` (9 members) says nothing about how the pipeline behaves
at the size of a real engagement — a mill system might be 2,000–8,000
Natural members. `scripts/generate_scale_fixture.py` synthesizes a
parameterizable, reproducible corpus of plausible Natural programs (a mix
of resolved, unresolved and dynamic `CALLNAT` targets) into
`examples/external/scale_fixture/` — gitignored, opt-in, not run in CI, same
posture as the cobol-course fetch script above.

```bash
python scripts/generate_scale_fixture.py --count 5000
mfdoc ingest   --config examples/external/scale_fixture/project.yml
mfdoc derive   --config examples/external/scale_fixture/project.yml
mfdoc coverage --config examples/external/scale_fixture/project.yml
```

This is what caught the win from the `ix_*_upper_*` expression indexes added
in #9a: at 5,000 members / 20,000 call edges, `mfdoc derive` went from
~23.6s (unindexed `UPPER(...)` correlated subqueries in `graph.resolve()`
and `graph.orphans()`, confirmed with `EXPLAIN QUERY PLAN` showing a full
table scan per candidate row) to ~0.19s once the expression indexes existed
— use it the same way for any future change to those code paths, rather
than reasoning about complexity in the abstract.

## Style and dependency discipline

- Python ≥ 3.10 syntax is used throughout (`X | Y` unions, walrus operator).
  Keep matching that floor rather than introducing 3.11+-only syntax without
  checking `pyproject.toml`'s `requires-python`.
- The core pipeline's only runtime dependency is `PyYAML`; everything else
  is standard library, on purpose — these tools are meant to run inside
  client environments with restricted or no egress. Don't add a dependency
  to the core path without a strong reason; if something genuinely needs
  one, follow the `anthropic_caller.py` pattern (isolate it, make it an
  optional extra, import it lazily) rather than adding it unconditionally.
- Don't add default redaction patterns, default dialect assumptions, or any
  other built-in guess at what a specific client's source contains. The
  project's whole value proposition rests on flagging what it doesn't know
  rather than inventing a plausible answer — that discipline applies to
  the tool's own configuration surface, not only to generated narrative.

## Where the plan document fits

[`docs/plans/legacy-functional-docs-plan.md`](../plans/legacy-functional-docs-plan.md)
is the working backlog and design-decision record, not polished
documentation — it has a progress log at the top that's more current than
the phase-by-phase narrative below it. Check it before starting anything
that sounds like it might already be scoped there (extraction correctness
items in particular — Phase 4's table is prioritised by documentation
value per unit of effort, with an honest effort estimate per row).
