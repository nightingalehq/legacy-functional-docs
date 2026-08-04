# legacy-functional-docs — repo conversion and improvement brief

Status: accepted; execution in progress
Date: 2026-08-04
Target repo: `legacy-functional-docs`, public, MIT-licensed, under the nightingalehq
GitHub org.

**Decisions taken:**
- 1.4 licensing/posture: MIT, public repo. Done — see `LICENSE` and the README
  license section.
- Phase 3 orchestration: **Option C** (hybrid) — batch harness for the
  high-volume, formulaic module docs; CLI stays for system overview, process
  flows and the gap register, where judgement matters most.

**Progress (2026-08-04):**
- Done: 1.1 (pytest suite, 12 defect classes + coverage snapshot), 1.2
  (pyproject.toml + src layout + console script), 1.3 (CI), 1.4 (above), 2.1
  (`mfdoc gate`), 2.2 (`mfdoc calibrate`), 2.3 (redaction at brief time), 3
  (`mfdoc batch`, option C), 4.1 (literal-bearing arithmetic as rule
  candidates). Critical path (1.1 → 1.2 → 1.3 → 2.3) plus both decision
  points are clear — repo is at the plan's bar for "safe to point at a
  client codebase," modulo the items below.
- Deferred, not started: 4.2 (transitive copycode in briefs), 4.3
  (loop-label resolution), 4.4 (Natural map parser), 4.5 (continuation
  folding rework), 4.6 (reporting-mode block inference), 4.7 (Adabas
  coupling), 5.1 (run the eval prompts and record results), 5.2
  (citation-accuracy sampling), 6 (indexes, incremental ingest, encoding
  fixtures, synthetic scale fixture). None of these block using the tool on
  a real engagement; they're correctness/coverage/scale improvements queued
  by the priority order in Phase 4's table and the critical-path note above.
- GitHub issue creation from the "Suggested issue breakdown" table hasn't
  run — there's no GitHub remote for this repo yet. Do that once it's
  pushed to the nightingalehq org.

## Purpose of this document

Turn a working prototype into a maintainable asset. Phases below are sized to become
GitHub issues; each has acceptance criteria you can check without reading the diff.

## Where it actually stands

3,409 lines of Python across 14 modules, plus a skill definition, 5 reference packs,
7 templates, 9 fixtures and one worked example. It runs end to end and produces
correct output on the fixtures.

**But every claim of correctness so far rests on me eyeballing output in a
throwaway session.** Twelve defects were found that way. There is nothing preventing
their return, and some were subtle enough that they would not be obvious in a diff —
the masked-literal leak silently dropped `'CONF'` from a business rule while
producing output that looked entirely plausible.

So the honest read: the design is sound and the parsers work on the cases tested. The
project has no test suite, no CI, no packaging, and no measurement of whether the
*narrative* stage behaves. Treat Phase 1 as ship-blocking.

## Decision needed before Phase 3

**How does the narrative pass get orchestrated at scale?** I wrote `SKILL.md`
CLI-first, assuming it lands like your `gosmarter-core-platform` workflow. That works
for tens of modules. At a realistic engagement size — a mill system might be 2,000 to
8,000 Natural members — one-document-at-a-time in a chat session is untenable on both
time and cost.

| Option | Fits | Against |
|---|---|---|
| **A. Claude Code CLI in-repo**, one doc per invocation, phases → issues | matches your existing workflow; human checkpoints per document; cheap to start | does not scale past a few hundred modules; no batching |
| **B. Headless batch** via the Messages API, brief → doc, parallelised, validator as gate | scales; reproducible; cost is measurable up front | new harness to build and maintain; loses the per-doc human checkpoint unless designed in |
| **C. Hybrid** — batch the module docs (high volume, formulaic), CLI for system overview, process flows and gap register (low volume, high judgement) | most of the volume automated where judgement is least needed; humans stay where they add value | two code paths |

My recommendation is **C**, but it depends on your first real engagement size, so it
is your call, not mine. Phase 3 is written assuming C and is straightforward to
retarget.

---

## Phase 1 — Foundations (ship-blocking)

### 1.1 Test suite

`pytest` with the fixtures as golden tests. The specific things to lock down, because
these are the twelve defects that already occurred once:

| Test | Guards against |
|---|---|
| Citation line alignment: every `source_line` row matches the file on disk at `first_line` offset | off-by-one from banner handling — silently invalidates every citation |
| `'CONF'` present in the `IF` condition for MMP0100 | masked-literal leak losing business values |
| `WRITE-AUDIT` is `PERFORM_INTERNAL`, resolved, and generates no gap | internal subroutines reported as missing modules |
| Exactly one `ORDLINE` entity | Supra label matching inside linkpath blocks; kind-guessing across ingest order |
| `MILL-ORDER` merges `FILE-045`; `adabas_entities_merged == 1` | phantom entities in the data model |
| No `REPRO` / `OUTDATASET` call edges | IDCAMS `SYSIN` mined as a Natural stack |
| `MMP0100` reachable via `CMSYNIN` stack; not an orphan | batch Natural programs looking like dead code |
| `STEPLIB` / `DDCARD` / `CMPRINT` absent from `entity` | infrastructure DDs inflating the data model |
| Member names carry no extension chain (`MMP0100`, not `MMP0100.NSP`) | call edges failing to resolve after file transfer |
| `EXTERNAL` first token treated as library, not callee | fabricated missing modules |
| Validator rejects: out-of-range line, unknown member, missing front-matter key, bad `review_status`, uncited assertion | the traceability guarantee itself |
| Validator accepts the worked example unchanged | false positives training people to ignore it |

Add a snapshot test on `coverage` output so any metric change is visible in review
rather than discovered later.

**Acceptance:** `pytest` green; every row above has a named test; a deliberately
reintroduced masked-literal bug fails the suite.

### 1.2 Packaging

`pyproject.toml`, `mfdoc` as a console script, `pip install -e .`. Drops the
`PYTHONPATH=scripts` requirement, which is a papercut every user hits on first run.

Declare: Python ≥ 3.10 (walrus and `X | Y` unions are used in 7 modules), PyYAML the
only runtime dependency. Everything else is stdlib — worth keeping that way, since
these tools get run inside client environments with restricted egress.

**Acceptance:** `pip install -e . && mfdoc coverage --config project.yml` works from a
clean venv.

### 1.3 CI

GitHub Actions on push and PR: `pytest`, then the full pipeline against fixtures, then
`mfdoc validate --docs examples`. Fail on any invalid citation.

**Acceptance:** a PR that breaks citation alignment goes red without a human noticing.

### 1.4 Licensing and repo posture

Needs a decision, not a task: is this an internal GoSmarter asset, a client
deliverable, or open source? It shapes whether the Mantis and Supra calibration work
done on a client engagement can be folded back in. Worth settling before the first
client codebase touches it, because retrofitting that answer is awkward.

---

## Phase 2 — Make the gates and calibration real

Two things the config promises and the code does not deliver.

### 2.1 `mfdoc gate`

`options.quality_gates` is read into config and never enforced. Add a command that
evaluates coverage against the gates and exits non-zero on failure, so it can sit in
CI and in the skill workflow as an actual stop rather than an instruction to a model
that may skip it.

Output should say which gate failed, by how much, and what it blocks — the same
framing as the gap register.

### 2.2 `mfdoc calibrate --dialect mantis`

The unparsed-line shape analysis currently exists as a snippet pasted inside
`reference/mantis-supra.md`. Promote it to a command: group `unparsed_line` gaps by
leading keyword, rank by frequency, and print alongside a sample line and the file
each would be added to.

This is the single highest-leverage usability improvement for real engagements,
because Mantis and Supra calibration is *expected* work, not an edge case, and
right now it depends on someone finding a code block in a reference doc.

### 2.3 Redaction

`options.redact` is a stub. Implement it before any real client source is ingested —
literals in mainframe source routinely contain customer names, account numbers and
occasionally credentials. Apply at brief-generation time so nothing sensitive reaches
a prompt, not only at document-render time.

**Acceptance:** a fixture containing a fake NI number and a fake password is ingested,
and neither appears in any brief or document with redaction enabled.

---

## Phase 3 — Narrative stage at scale

Depends on the A/B/C decision above. Assuming C:

- Batch harness: for each module, generate brief → call model with
  `reference/writing-rules.md` + `templates/module.md` → write doc → run validator →
  retry once on validation failure with the failure text appended.
- Record per-document token cost and validation outcome, so cost per thousand members
  is a known number before quoting an engagement rather than after.
- Cap concurrency and make it resumable; a run over thousands of members will be
  interrupted.
- Keep system overview, process flows and gap register in the CLI path.

**Acceptance:** 9 fixtures produce 9 valid documents unattended, with a cost figure
and a retry count reported.

---

## Phase 4 — Extraction correctness debt

Ordered by documentation value per unit of effort, with my honest read of each.

| # | Item | Why it matters | Effort |
|---|---|---|---|
| 4.1 | **Extract `COMPUTE` / `MOVE` / `EXAMINE` as rule candidates** | Currently matched and discarded. Pricing, tolerance and unit-conversion arithmetic *is* business logic — arguably the most sought-after kind in a metals context, where yield and weight conversions carry real money | low |
| 4.2 | **Transitive copycode in briefs** | Rules inside copycode are attributed to the copycode. A reader of the including module never sees them, so a module document can be complete and still miss its own validation rules | low |
| 4.3 | **Loop-label resolution for `UPDATE (label)` / `DELETE (label)`** | Currently flagged `unresolved`. Track labelled loops and their views; converts a recurring gap into a fact | medium |
| 4.4 | **Natural map (`.nsm`) parser** | No dialect exists. Field-level validation, prompts and edit masks live in maps, and they are user-visible business rules | medium |
| 4.5 | **Better continuation folding** | `CONTINUATION_TAIL` is a heuristic on trailing tokens. Real Natural wraps without them, so long `FIND ... WITH` clauses can be truncated mid-condition — a silent partial rule, the worst failure mode | medium |
| 4.6 | **Reporting-mode block inference** | Currently flagged and abandoned. Indentation plus `LOOP` gives a usable guess, marked `inferred`. Reporting-mode members are the oldest and most business-critical code, so leaving them unstructured concedes the most valuable ground | high |
| 4.7 | **Adabas coupling** | `entity_link` supports `coupled` but nothing emits it. Physical relationships between Adabas files are currently invisible | low |

Deliberately *not* on this list: IDMS, IMS, ADSO, RPG. `reference/adding-a-dialect.md`
documents the contract; build them when a client actually has one, because speculative
dialect packs age badly and cannot be tested.

---

## Phase 5 — Quality measurement

The validator proves citations *resolve*. It does not prove they are *right* — a
citation pointing at the wrong line passes, and that is the failure mode most likely
to survive review and reach a business sign-off.

Two things worth building:

1. **Run the eval prompts.** `evals/evals.json` has three realistic prompts with
   assertions, all unexecuted. Until they run, "does Claude-with-this-skill follow the
   workflow" is untested, and the workflow is most of the value.
2. **Citation-accuracy sampling.** Sample N claims per document, present the claim
   alongside the cited source line, and judge whether the line supports it. Human
   spot-check first to calibrate; then an LLM judge if the human pass agrees with it.
   Report an accuracy figure per run, in the coverage report, next to the recognition
   rates.

That second one is what lets you say something defensible to a client about
reliability, rather than "every claim has a citation" — which is true and, on its own,
not the assurance they think it is.

---

## Phase 6 — Scale and operational hardening

Not urgent, and cheap to do wrong later, so worth noting now.

- **Indexes.** `resolve()` and `orphans()` use correlated subqueries on
  `UPPER(callee_name)`. Fine at 9 members, quadratic-ish at 5,000. Add expression
  indexes and re-measure on a synthetic large codebase.
- **Incremental ingest.** Currently a full rebuild. `source_file.sha256` is already
  recorded, so skipping unchanged files is straightforward.
- **Encoding sniffing.** Heuristic and untested against real EBCDIC with mixed
  content. Worth a fixture set in cp037 and cp500 once you have real samples; until
  then, tell users to pin `encoding:`.
- **Synthetic scale fixture.** Generate a few thousand members to get real timings
  before a client asks how long it takes.

---

## Security and compliance to settle before a real engagement

Flagging these because they need answering once, up front, not per project:

- **Where does client source live?** Mainframe source is usually the client's crown
  jewels and often contractually restricted. If ingestion runs on your infrastructure,
  that is a data-processing arrangement with all that follows.
- **What reaches a model.** Briefs are sent to an API. Even with redaction, literal
  values, field names and dataset names are disclosive — dataset naming conventions
  alone reveal infrastructure layout. Decide whether a given engagement can use a
  hosted model at all, and be able to state what is transmitted.
- **Credentials in source.** Legacy source frequently contains hard-coded passwords,
  userids and connection strings. You will find them. Decide the disclosure path
  before you do, and consider a scanner that raises them as high-severity gaps —
  arguably a selling point rather than a problem.
- **The index is a security artefact.** `.mfdoc/index.db` contains every source line.
  It is gitignored, which is necessary and not sufficient; it needs a retention and
  disposal answer.
- **Audit trail.** `project.yml` plus tool version plus source SHAs already make a run
  reproducible. Worth stating that explicitly to clients — it is a genuine
  differentiator against a consultant reading code and writing Word documents.

---

## Suggested issue breakdown

| Issue | Phase | Blocks | Rough size |
|---|---|---|---|
| Add pytest suite covering the twelve known defect classes | 1.1 | everything | 1–2 days |
| pyproject.toml + console script | 1.2 | CI | half day |
| GitHub Actions: pytest + pipeline + validate | 1.3 | — | half day |
| Decide licensing and repo posture | 1.4 | client work | discussion |
| `mfdoc gate` command | 2.1 | CI gating | half day |
| `mfdoc calibrate` command | 2.2 | Mantis/Supra engagements | 1 day |
| Implement redaction | 2.3 | any real client source | 1 day |
| Decide narrative orchestration (A/B/C) | 3 | Phase 3 | discussion |
| Batch narrative harness | 3 | scale | 2–3 days |
| Extract arithmetic as rule candidates | 4.1 | — | half day |
| Transitive copycode in briefs | 4.2 | — | half day |
| Loop-label resolution | 4.3 | — | 1 day |
| Natural map parser | 4.4 | — | 1–2 days |
| Continuation folding rework | 4.5 | — | 1–2 days |
| Reporting-mode inference | 4.6 | — | 2–3 days |
| Run eval prompts, record results | 5.1 | confidence in workflow | 1 day |
| Citation-accuracy sampling | 5.2 | client assurance claims | 2 days |
| Indexes + incremental ingest + scale fixture | 6 | large engagements | 2 days |

Critical path to "safe to point at a client codebase": **1.1 → 1.2 → 1.3 → 2.3**,
plus the licensing and orchestration decisions. Everything else is improvement rather
than risk reduction.

---

## Claude Code CLI prompts

Commit this document to `docs/plans/legacy-functional-docs-plan.md` first, then drive
from it. These are written to be pasted more or less as-is.

### Repo initialisation

```
Read @docs/plans/legacy-functional-docs-plan.md.

Set up this repo per Phase 1.2 and 1.3 only — do not start Phase 1.1 yet:
- pyproject.toml, Python >=3.10, PyYAML the only runtime dep, pytest as a dev dep
- console script `mfdoc` pointing at mfdoc.cli:main
- move scripts/mfdoc to src/mfdoc, update SKILL.md and README.md paths, and
  confirm the pipeline still runs against examples/fixtures before you finish
- .github/workflows/ci.yml running pytest, then the four pipeline commands, then
  `mfdoc validate --config project.yml --docs examples`

Verify by running the commands yourself. Report anything in SKILL.md or README.md
that the move made stale.
```

### The test suite — do this before any feature work

```
Read @docs/plans/legacy-functional-docs-plan.md, section 1.1.

Write the pytest suite. One named test per row of that table. Use the existing
fixtures in examples/fixtures — do not add new ones unless a row cannot be tested
without one, and say so if that happens.

The citation alignment test is the important one: for every member, assert each
source_line row matches the file on disk at the member's first_line offset. That
class of bug invalidates every citation in the output and is invisible in a diff.

Then prove the suite works: reintroduce the masked-literal bug in
src/mfdoc/dialects/natural.py by storing m.group("cond") instead of
orig(stmt, m, "cond"), confirm a test fails, revert it, confirm green.
```

### Issue creation

```
Read @docs/plans/legacy-functional-docs-plan.md.

Create GitHub issues from the "Suggested issue breakdown" table. One issue per row.
Each issue body: the relevant section text as context, explicit acceptance criteria,
and the `Blocks` column as a note. Label by phase. Milestone the critical-path items
(1.1, 1.2, 1.3, 2.3) as "safe for client use".

Open the two decision items as discussion issues, not task issues — they need my
answer, not an implementation.
```

### Calibration command

```
Read @docs/plans/legacy-functional-docs-plan.md section 2.2 and
@reference/mantis-supra.md.

Implement `mfdoc calibrate --dialect <name>`. Promote the analysis snippet currently
embedded in the reference doc into a real command: group unparsed_line gaps by
leading keyword, rank by frequency, show a sample line for each and name the file
and constant a fix would go in.

Then replace that snippet in reference/mantis-supra.md with the command, so the doc
and the tool cannot drift.
```

### Arithmetic extraction

```
Read @src/mfdoc/dialects/natural.py and @reference/writing-rules.md.

Per plan section 4.1: COMPUTE, MOVE, ADD, SUBTRACT, MULTIPLY, DIVIDE and EXAMINE
are currently matched and thrown away. Capture them as rule_candidate rows with the
unmasked expression, using orig() the same way the conditional scanner does.

Only where they carry business meaning — assignment of a literal to a status field
matters; incrementing a loop counter does not. Use your judgement on the filter and
explain what you excluded and why.

Add tests. Confirm the MMP0100 brief now surfaces the tolerance arithmetic at
line 55 as a rule candidate.
```

## One caveat on using the CLI here

The parsers are dense regex over formats with little public documentation. When Claude
Code proposes a "simplification" to a pattern in `mantis.py` or `supra.py`, be
sceptical — several of those patterns look redundant and are not. The anchor on the
Supra dataset label is one character and prevents a whole class of phantom entities;
`UTILITY_BANNER_DIALECTS` looks like an odd special case and is the difference between
correct and universally-off-by-one citations.

The comments explain the reasoning for exactly this situation. Phase 1.1 exists so
that scepticism is enforced by the test suite rather than by whoever happens to be
reviewing.
