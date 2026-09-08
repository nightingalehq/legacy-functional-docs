# legacy-functional-docs

A Claude Code skill that builds first-draft functional documentation from legacy
mainframe 4GL codebases — Natural/Adabas and Mantis/Supra — plus the surrounding
data definitions and orchestration (Adabas FDT, Natural DDM, Supra directory,
DB2/SQL DDL, COBOL copybooks, JCL, CICS CSD).

Output is markdown with YAML front matter. Every business rule carries a
`[[MEMBER:LINE]]` citation and a confidence flag, and every unknown becomes an item
in a gap register phrased as a question for a domain expert. Humans supplement and
approve; the skill's job is to get them most of the way there without inventing
anything.

## Capabilities

### What it reads

- **Natural/Adabas** — Natural source (programs, subprograms, subroutines,
  copycode, maps), Adabas DDM listings and FDT reports (ADAREP/ADACMP) for
  the same physical files.
- **Mantis/Supra** — Mantis source, Supra directory reports/linkpaths, and
  Mantis screen/map painter exports (field name, type, position — the
  screen's complete field inventory). Calibration against the target
  codebase is expected (`mfdoc calibrate`, `reference/mantis-supra.md`).
- **Surrounding orchestration and data definitions** — DB2/SQL DDL, COBOL
  copybooks, JCL (including embedded SQL), and CICS CSD extracts.
- Mainframe-specific input handling: EBCDIC code pages (`cp037`/`cp500` etc.),
  sequence-number columns, and splitting one exported listing into many
  logical members.

### What it derives

From the extracted facts, deterministically: a call graph with resolved and
unresolved (missing-source or dynamic-target) targets, a CRUD matrix, Adabas
coupling/Supra linkpaths as entity relationships, transaction scopes, orphan
detection, fields on a referenced screen/table a module never touches
(`graph.unused_entity_fields`, a `gap_kind='unused_field'` row per finding),
and coverage metrics (`line_recognition_rate`,
`call_resolution_rate`, `entity_definition_rate`, gap counts by severity).
`mfdoc gate` checks these against configurable thresholds before anything is
written. Every `mfdoc coverage`/`mfdoc gate` run appends a timestamped
snapshot of these metrics to the fact store; `mfdoc coverage --history`
prints the resulting trend so drift or improvement across a multi-week
engagement is visible without tracking it by hand.

### What it produces

Eight markdown document types (`templates/`): system overview, module docs,
data entity docs, process flows, a screen-and-key interface matrix (mode x
panel x map x PF-label x routine x outcome), a CRUD/coverage report, and a
gap register phrased as SME interview questions. Every business rule carries a
`[[MEMBER:LINE]]` citation and a confidence flag; `mfdoc validate` fails the
build on any citation that doesn't resolve or any uncited, unhedged
assertion. A flat `rules-register` indexes every `MEMBER:BR-nnn` rule ID
across the whole doc set. `mfdoc export --json` dumps the full fact store for
downstream tooling.

### How narrative gets written

Two paths, chosen per document type: `mfdoc batch` generates high-volume
module docs unattended via a pluggable model caller — direct Anthropic API
or Claude on Vertex AI (`--provider vertex`) — with a `fake-echo` caller for
network-free dry runs; or the interactive Claude Code path for documents
that benefit from a session holding the whole system in mind (system
overview, entity docs, process flows, gap register). A member with more
than `options.narrative.max_rules_per_call` business rules (default 40)
renders as several independent chunk documents plus a deterministic index
doc at the normal path, instead of one — a single non-streaming completion
asked to narrate a large module's whole rule set in one pass risks running
out of room partway through and silently covering only some of it. See
`src/mfdoc/batch.py`'s `DEFAULT_MAX_RULES_PER_CALL`. Chunk boundaries are
routine-aware and packed by rule count within routine boundaries, which says
nothing about how content-dense a chunk's *source* actually is; when a chunk
fails, its reported problem is
annotated with a lines-per-rule/nesting-depth density estimate and flagged
as an outlier if it's well above the run's own median — so a chunk that
keeps failing for a genuine complexity reason is distinguishable from one
that was just unlucky, without waiting for the pattern to repeat across
several runs. See `src/mfdoc/brief.py`'s `chunk_density_metrics`/
`flag_density_outliers`/`format_density_note`.

### Test generation (optional)

The same fact store can draft first-draft tests for a migration team — in
the legacy dialect or a destination language — with the same discipline as
the docs: `mfdoc test-plan` derives cited test scenarios (Given/When/Then)
from branch/parameter/CRUD facts, model-free; `mfdoc test-advisory` names
what each unit needs mocked and suggests refactor seams, model-free;
`mfdoc test-overlay-draft` lets a model *propose* a bug-vs-spec split,
which only takes effect once a human promotes it past `review_status:
draft`; `mfdoc test-gen`/`mfdoc test-batch` render the scenarios into
`language`/`framework`-specific test code (still Markdown + citations, so
`mfdoc test-validate` can check them the same way `mfdoc validate` checks
docs). See
[`docs/guides/testing-strategies-for-mainframes-and-4gl.md`](docs/guides/testing-strategies-for-mainframes-and-4gl.md).

### Data handling

Redaction (`mfdoc brief`, before anything is written or sent anywhere), a
gitignored local fact store, and a documented default posture of no network
access except the two model-calling paths above. See
[`docs/guides/security-and-compliance.md`](docs/guides/security-and-compliance.md).

### SME notes (optional)

An SME reviewing generated docs can leave free-text business context,
gotchas, or corrections in a single semi-structured markdown file (`sme-
notes.md` by default, path configurable via `options.sme_notes` in
`project.yml`) that folds into later generation runs — no code or fact-store
changes needed. See [`examples/sme-notes.md`](examples/sme-notes.md) for a
worked example. Schema, parsed by `src/mfdoc/sme_notes.py`:

- Content before the first `##` heading (or under an explicit `## General`
  heading) applies to every module/entity document generated for the
  project.
- Each `## <member-or-entity-name>` heading scopes its body to just that
  member or entity, matched case-insensitively against the same
  `member_name`/entity names used elsewhere in the tool. Body text is
  freeform prose or bullets — no further structure required.
- The file, and the config key that points at it, are both entirely
  optional; a missing file is a no-op.

Notes are advisory context only: `mfdoc brief`/`mfdoc batch`/`mfdoc
test-batch` append a matching note as its own clearly-labeled, uncited
section at the end of `module_brief`/`entity_brief`/`executive_brief`/
`test_case_brief`, redacted the same as everything else in the brief. They
may inform interpretation and emphasis, but they are never a citable
source — every business-rule claim in the generated output still needs its
own `[[MEMBER:LINE]]` citation, and a note that contradicts the cited facts
loses to the facts (see `reference/writing-rules.md`'s "SME notes" rule).

## Why two stages

An LLM reading raw 4GL source will produce fluent documentation containing business
rules the code does not have. It happens because the model has to hold thousands of
lines in mind at once and the gaps get filled plausibly rather than accurately. A
confidently wrong document is worse than none, because it stops people reading the
code.

So extraction is deterministic Python into a SQLite fact store, and the narrative
pass writes only from a generated fact brief in which every line already carries a
citation. If a fact is not in the brief there is nothing to cite, and the writing
rules require the claim to be dropped or marked `unresolved`. A validator then
re-checks every citation against the index and fails the build on any that does not
resolve.

## Documentation

- **New to Claude Code, Python, or mainframe 4GLs?** Start with
  [`docs/guides/getting-started.md`](docs/guides/getting-started.md) — a
  no-assumed-background walkthrough of what this is and how to run it.
- **Architecture overview** — [`docs/guides/architecture.md`](docs/guides/architecture.md)
  covers the pipeline stage by stage, the data model, and where a model can
  and can't reach.
- **Security, data handling and compliance due diligence** —
  [`docs/guides/security-and-compliance.md`](docs/guides/security-and-compliance.md)
  covers what leaves the machine, redaction, credentials found in source,
  the fact-store's status as a security artefact, and an engagement
  checklist.
- **Extending the tool** — [`docs/guides/extending.md`](docs/guides/extending.md)
  is the developer guide for adding a dialect, a document type, or a CLI
  command.
- **Test generation** —
  [`docs/guides/testing-strategies-for-mainframes-and-4gl.md`](docs/guides/testing-strategies-for-mainframes-and-4gl.md)
  introduces modern testing concepts for a mainframe/4GL audience and
  explains what the generated tests are for.

## Installing as a Claude Code skill

**New to git, GitHub, or the command line?** See
[`docs/guides/getting-started.md`](docs/guides/getting-started.md) first —
it covers downloading this repository onto your machine (with or without
git) and setting up a Python virtual environment, step by step. The summary
below assumes you've already got the files locally.

Copy or clone this repository's contents into a `legacy-functional-docs/`
directory under your Claude Code skills path (e.g. `~/.claude/skills/` for a
personal install, or `.claude/skills/` at the root of a project for a
project-scoped one). Claude Code discovers the skill from its `SKILL.md`
front matter; no separate registration step is required. The Python pipeline
under `src/mfdoc/` runs locally wherever Claude Code invokes shell commands;
run `pip install -e .` from the skill directory once to get the `mfdoc`
console script on `PATH` — see Requirements below.

## Quick start

```bash
# optional: isolate dependencies in a virtual environment first
# python3 -m venv .venv && source .venv/bin/activate   (Windows: .venv\Scripts\Activate.ps1)

pip install -e .

cp config/project.example.yml project.yml
# edit source paths, pin the dialect for each source set

# Every mfdoc command validates the resolved config's known options.* keys
# (shape/type/range) before doing any work, and exits 2 with a readable
# message on the first bad project.yml it's pointed at -- so a typo (e.g. a
# negative options.narrative.max_rules_per_call, an out-of-range
# quality_gates threshold) is caught immediately rather than partway
# through a run.

mfdoc ingest   --config project.yml
mfdoc derive   --config project.yml
mfdoc coverage --config project.yml     # read this before writing anything
mfdoc gate     --config project.yml     # pass/fail check against options.quality_gates

# Mantis and Supra usually need this before the gate passes -- see
# reference/mantis-supra.md:
mfdoc calibrate --config project.yml --dialect mantis

mfdoc brief --config project.yml --system
mfdoc brief --config project.yml --module MMP0100
mfdoc brief --config project.yml --entity MILL-ORDER
mfdoc brief --config project.yml --executive MMP0100
mfdoc brief --config project.yml --interface-matrix
# whole-system: per screen/map, which module(s) display it, the PF-key (or
# configured dispatch field) branches those modules dispatch on, and any
# literal label text recorded on the screen -- see templates/interface-matrix.md.
# Each branch row is tagged with its mechanism: PF-key dispatch (the default),
# or, when options.overview.mode_field_pattern is also set, a second scan for
# a central mode/panel-field-keyed dispatch block some dialects/coding styles
# use instead of (or alongside) a distinct subroutine call per PF-key branch.

# ... write documents from the briefs, per reference/writing-rules.md ...
# module docs are high-volume and formulaic; batch them instead of writing
# one at a time (needs `pip install 'mfdoc[batch]'` and ANTHROPIC_API_KEY):
mfdoc batch --config project.yml --out docs/functional/modules
# output nests as <out>/<dialect>/<library>/<member>.md (library segment
# omitted when the member has none), mirroring the only two source-grouping
# facts actually on record for a member -- e.g.
# docs/functional/modules/natural/MILLPROD/MMP0100.md

# an engagement-scale batch/test-batch run is long enough that the routine
# progress (a member skipped on resume, a chunk completing, a transient
# error being retried) is worth watching or keeping: --verbose (-v) raises
# that from INFO to DEBUG, --log-file also writes it to a file (in addition
# to stderr) -- both are top-level `mfdoc` flags, given *before* the
# subcommand:
mfdoc --verbose --log-file batch.log batch --config project.yml --out docs/functional/modules
# the per-member OK/FAIL/SKIP table and cost summary `mfdoc batch` prints at
# the end are unaffected either way -- that's real, scriptable output on
# stdout, not diagnostic logging

mfdoc rules-register --config project.yml --out docs/functional/rules-register.md
# a flat, greppable index of every MEMBER:BR-nnn rule ID -- look one up here
# without already knowing which module doc it lives in; regenerate any time,
# byte-identical output against unchanged source

# optional structural overview reports -- all deterministic (no network call),
# all configured under options.overview in project.yml, see below:
mfdoc classify-rules --config project.yml
# assigns each rule_candidate a business theme: keyword taxonomy first
# (options.overview.themes.taxonomy), then an optional LLM fallback for
# anything unmatched (--llm-fallback, or options.overview.themes.llm_fallback),
# then a structural fallback (the rule's own member's library). Run this
# before rules-theme-register or executive-summary docs, or every rule
# reads as uncategorized.
mfdoc gap-summary --config project.yml --out docs/functional/gap-summary.md
# gap counts by kind and severity, for the top of system-overview.md
mfdoc data-flow --config project.yml --out docs/functional/data-flow.md
# entity read/write relationships as a diagram, from the CRUD matrix
mfdoc call-graph --config project.yml --out docs/functional/call-graph
# who-calls-whom, as a diagram; writes call-graph*.md files into the given
# directory (or prints the inline diagram to stdout if --out is omitted)
mfdoc complexity --config project.yml --out docs/functional/complexity.md
# a per-member risk/complexity heatmap (options.overview.complexity.metric)
mfdoc rules-theme-register --config project.yml --out docs/functional/rules-theme-register.md
# the rules register, rolled up by business theme instead of by module --
# needs `mfdoc classify-rules` run first, or every rule lands under "uncategorized"
mfdoc glossary --config project.yml --out docs/functional/glossary.md
# one entry per entity, with its fields nested underneath, from entity/field
# descriptions already recorded in the fact store
mfdoc dispatch-map --config project.yml --out docs/functional/dispatch-map.md
# for every branch that compares a configurable dispatch field (default:
# Natural's *PF-KEY) against a literal, the routines it calls and the fields
# it sets in that same branch -- override options.overview.dispatch_field_pattern
# for a different dialect's own dispatch idiom (e.g. a Mantis menu/transfer
# option field) or a Natural codebase that wraps *PF-KEY in its own field
mfdoc lang-guide --config project.yml --dialect mantis --out docs/functional/reference/language-guide.md
# every recognised construct in one dialect's source, grouped by keyword,
# with a cited example of each -- the basic (deterministic) tier of the
# language-guide document type; --dialect is required

# Note: unlike the other structural overview commands above (gap-summary, data-flow,
# complexity, rules-theme-register, glossary, dispatch-map, lang-guide), call-graph's
# --out must be a directory path, not a file path; it generates multiple files (one
# per cluster if needed).

mfdoc validate --config project.yml --docs docs/functional

# smoke-test against the bundled fixtures and worked examples:
mfdoc validate --config project.yml --docs examples/outputs

# sample generated claims against their cited source and record whether the
# source actually supports each one -- backs the min_citation_accuracy_rate
# quality gate (see "Coverage gates" below); mfdoc gate fails that gate until
# this has been run at least once with --judge human:
mfdoc sample-citations --config project.yml --docs docs/functional --judge human
```

`mfdoc export --config project.yml --json out/index.json` dumps the whole
fact store for downstream tooling.

The structural overview reports above are configured under `options.overview`
in `project.yml`:

```yaml
options:
  overview:
    themes:
      # keyword/regex taxonomy for `mfdoc classify-rules` -- theme name to a
      # list of regexes matched against each rule_candidate's condition/
      # literals; no built-in taxonomy, same "declare it, don't guess it"
      # policy as options.redact/options.testgen above
      taxonomy: {}
      # let classify-rules fall back to an LLM pass for anything the
      # taxonomy above didn't match, instead of leaving it to the
      # structural (member-library) fallback; also settable per-run with
      # `mfdoc classify-rules --llm-fallback`
      llm_fallback: false
    complexity:
      # only rule_depth is implemented today (rule count + max nesting
      # depth, combined with call-graph in/out-degree); cyclomatic is a
      # documented-but-unimplemented future option
      metric: rule_depth
    diagrams:
      # how `mfdoc call-graph` clusters nodes ("module" and "library" are
      # aliases for the same grouping -> member.library; "subsystem" ->
      # member.system); data-flow always renders one diagram and ignores this
      cluster_by: module
      # above this many distinct nodes, `mfdoc call-graph` renders a
      # collapsed cluster-level diagram inline plus one full diagram per
      # cluster on disk, instead of one large inline diagram
      max_nodes_inline: 40
      # mermaid layout direction for `mfdoc call-graph`'s diagrams: "LR"
      # (left-to-right, the default) or "TD" (top-down)
      direction: LR
    # the field `mfdoc dispatch-map` looks for on the left/right of an IF
    # condition; default (unset) is Natural's built-in *PF-KEY. Replaces
    # rather than merges with the built-in pattern -- same convention
    # options.validate.outcome_field_pattern uses (conditions.py) -- so a
    # Mantis project (no fixed dispatch-field name) or a Natural codebase
    # that wraps *PF-KEY in its own field supplies its own complete
    # pattern here
    dispatch_field_pattern: null
    # a second, independent dispatch field the screen-and-key interface
    # matrix (`mfdoc brief --interface-matrix`) also scans for, alongside
    # dispatch_field_pattern above (issue #129): a mode/panel/transaction-
    # code-like field some dialects/coding styles use for a central
    # dispatch block that acts inline (sets a field, branches directly)
    # rather than PERFORMing a distinct subroutine per PF-key value. No
    # built-in default -- unlike *PF-KEY there's no one well-known system
    # variable to guess at, so this mechanism is simply omitted from the
    # matrix until a project opts in with its own pattern here
    mode_field_pattern: null
  validate:
    # the field(s) `mfdoc validate` treats as an "outcome" field (return/
    # response/status codes, flags) when cross-checking that a narrative
    # sentence's claimed comparison direction matches the condition it
    # cites -- catches a model narrating the logical inverse of a real
    # comparison (e.g. describing a reversed pass/fail check on a status
    # field). Default (unset) is the built-in OUTCOME_FIELD denylist
    # (conditions.py); replaces rather than merges with it, same
    # replace-not-merge convention options.overview.dispatch_field_pattern
    # uses -- so a codebase with different outcome-field naming supplies
    # its own complete pattern here
    outcome_field_pattern: null
```

Optional: draft tests from the same fact store (see
[`docs/guides/testing-strategies-for-mainframes-and-4gl.md`](docs/guides/testing-strategies-for-mainframes-and-4gl.md)).
Set `options.testgen` in `project.yml` (`default_language`, `default_framework`,
`overlay_path`, `out_dir`, `max_scenarios_per_call`) once and the flags below
become optional overrides. A member with more than `max_scenarios_per_call`
test_case rows (default 150) renders as several independent chunk documents
plus a deterministic index doc at the normal path, instead of one call --
asking a single non-streaming completion for hundreds of scenarios risks a
silently truncated response reported as success. See
`src/mfdoc/testbatch.py`'s `DEFAULT_MAX_SCENARIOS_PER_CALL`.

```bash
mfdoc test-plan     --config project.yml
mfdoc test-advisory --config project.yml
mfdoc test-overlay-draft --config project.yml --out test-overlay.yml
# a model proposes a bug-vs-spec split per scenario (needs mfdoc[batch]);
# only takes effect once a human moves an entry's review_status past `draft`
mfdoc test-gen      --config project.yml --member MMP0100 --language python --framework pytest
# output nests as <out_dir>/<project-namespace>/<dialect>/<library>/<language>/<framework>/<member>.md
# (same <dialect>/<library>/<language>/<framework> convention as `mfdoc batch` above,
# plus a namespace segment -- project.yml's `system`, else `project`, else "default" --
# so two configs sharing a working directory don't share one output tree) -- e.g.
# tests_generated/mom/natural/MILLPROD/python/pytest/MMP0100.md
mfdoc test-batch    --config project.yml --language python --framework pytest --out tests_generated
# the direct high-volume analogue of `mfdoc batch` above, for generated tests
# instead of module docs -- needs `pip install 'mfdoc[batch]'` and
# ANTHROPIC_API_KEY; resumable the same way `mfdoc batch` is
mfdoc test-validate --config project.yml --docs tests_generated
```

If multiple `project.yml` configs share the same `out_dir` (the common case
once namespacing is in play), point `mfdoc test-validate --docs` at the
namespaced subdirectory for the config you're validating (e.g.
`tests_generated/mom`) rather than the bare `out_dir` -- pointed at the bare
`out_dir` it walks every project's namespace subdirectory it finds there and
attempts to validate their generated test docs too, not just the one config
you ran it for.

## Worked examples

[`examples/outputs/`](examples/outputs/) is a full, real run of the pipeline
against the sample codebase in [`examples/inputs/`](examples/inputs/) — module
docs, entity docs, a system overview, a gap register, generated tests, the
fact store itself, and every deterministic report, all with resolving
citations (`mfdoc validate`/`mfdoc test-validate` report 0 invalid across the
whole tree, which is also what CI checks on every push and PR). See
[`examples/outputs/README.md`](examples/outputs/README.md) for the full
layout, what CI auto-refreshes vs. what's hand/session-produced, and exact
reproduction commands — including generating the module docs and tests for
real via `--provider claude-code` (the local Claude Code CLI, no
`ANTHROPIC_API_KEY` needed).

## Requirements

Python 3.10+ and PyYAML. No other dependencies, no network access, nothing leaves
the machine by default. The exceptions are the handful of commands that build a
model caller (`--provider anthropic` by default; `--caller fake-echo` keeps any
of them network-free for a dry run) and so can call out to the Claude API (or
Vertex AI with `--provider vertex`, or the local Claude Code CLI with
`--provider claude-code`) when actually run: `mfdoc batch` (below); `mfdoc
classify-rules --llm-fallback` (or `options.overview.themes.llm_fallback`);
`mfdoc test-overlay-draft`; `mfdoc test-gen`/`mfdoc test-batch`; and `mfdoc
sample-citations --judge llm`. Each needs `pip install 'mfdoc[batch]'` (or
`mfdoc[vertex]`) and `ANTHROPIC_API_KEY` (unless using `--provider claude-code`),
and every one is entirely optional -- the deterministic stages never call out.

## Layout

```
SKILL.md              the agent definition and workflow
pyproject.toml        packaging; installs the `mfdoc` console script
config/               example project configuration
reference/            dialect packs and writing rules — read before use
templates/            the eight document types, plus templates/tests/ for generated tests
src/mfdoc/            the extraction pipeline (+ testplan/testadvisor/testoverlay/testbatch)
tests/                pytest suite (fixtures as golden tests)
examples/
  inputs/              sample multi-dialect source the worked examples run against
  outputs/             a full, real pipeline run against examples/inputs -- see its own README.md
    docs/<dialect>/<library>/*.md    module docs, mirroring mfdoc batch's own output convention
    docs/{entities,process-flows}/   cross-cutting doc types (no fixed dialect home)
    docs/{system-overview,interface-matrix,gap-register}.md
    tests/<dialect>/<library>/<language>/<framework>/*.{md,py}   generated tests + sidecars
    *.json, *.db, *-register.md, *-advisory.md   deterministic artifacts (CI-refreshed)
evals/                eval prompts (dev-time only; not installed)
docs/guides/          getting-started, architecture, security/compliance, extending,
                      testing-strategies-for-mainframes-and-4gl
docs/plans/           working backlog and design-decision record
```

## Coverage gates

`options.quality_gates` in the config sets thresholds the run should clear before
narrative is written. Against the shipped fixtures the pipeline achieves a
`line_recognition_rate` of 0.9753 with citation line alignment verified against
source for every member. `call_resolution_rate` is deliberately low on the
fixtures (0.1538) because three called modules are intentionally absent, which
exercises the gap machinery.

One gate, `min_citation_accuracy_rate`, is different from the rest: it isn't
computed from facts at all, it's sampling-derived. Run
`mfdoc sample-citations --config project.yml --docs docs/functional --judge human`
at least once to pull a random sample of generated claims, show each one next
to its cited source line, and record a human yes/no verdict on whether the
source actually supports the claim; an optional second pass,
`--judge llm`, checks an LLM judge's agreement against those human verdicts
rather than trusting it standalone (needs a model caller, see Requirements
above). Until at least one `--judge human` verdict is recorded,
`citation_accuracy_rate` doesn't exist in `coverage()`'s output, so if
`min_citation_accuracy_rate` is configured, `mfdoc gate` evaluates it against 0
and fails -- correctly, since a citation's accuracy has never actually been
checked, only that it resolves.

## Known limitations

Stated up front rather than discovered later:

- The scanners are heuristic line-and-clause matchers, not grammars. They are built
  to flag what they cannot parse, not to be complete.
- Natural reporting mode has implicit block scope. `LOOP` nesting is inferred
  from indentation when it's unambiguous (recorded with `confidence='inferred'`,
  still flagged for SME confirmation); when indentation doesn't clearly support
  it, nesting is left unresolved and flagged high-severity, same as before.
- Dynamic dispatch cannot be resolved from source. Those call graphs are incomplete
  by nature, and that is reported rather than hidden.
- The Mantis and Supra packs need calibration against the target codebase. See
  `reference/mantis-supra.md`; below roughly 85% line recognition, calibrate before
  trusting anything built on the index.

## License

[MIT](LICENSE)
