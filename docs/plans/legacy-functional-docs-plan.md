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
- 2026-08-05: reviewed Azure-Samples/Legacy-Modernization-Agents (a
  COBOL-reverse-engineer-then-convert tool) for applicable concepts. Its
  conversion/translation machinery and multi-provider LLM abstraction don't
  apply — we document, we don't translate, and we have one model path. Two
  new low-effort items added (4.8 stable rule IDs, 4.9 glossary support);
  cross-reference notes added to 4.2, 4.5 and the Phase 6 incremental-ingest
  item. Also opened 3.x (Vertex AI support, #12) after a client asked about
  GCP-only model routing.
- 2026-08-05: working through the open backlog in priority order. Done:
  4.5 (continuation folding, #4) — `CONTINUATION_TAIL` only continued when
  the *current* line ended in a connective; real Natural just as often
  wraps *before* the connective, silently truncating the statement while
  the citation still looked complete. Fixed with a `CONTINUATION_LEAD`
  peek at the next line. Also done: 5.1 (run the eval prompts, #7) — all
  three evals pass every listed assertion; results recorded in
  `evals/results/2026-08-05.md`, along with two new worked examples
  (`ORDERMST`, `MMB0100`). Investigated fetching real JCL/SQL-DDL from
  `openmainframeproject/cobol-programming-course` as more robust example
  material per that issue's second ask — no public corpus exists for
  Natural/Mantis/Adabas/Supra (proprietary 4GLs), but that repo's real JCL
  is a fit for hardening the `jcl`/`sql_ddl` dialects specifically; spun
  out as its own smaller follow-up (5.3, #13) rather than folded in here.
  From here on, work is landing as one branch/PR per issue rather than
  direct commits to `main`, merged as soon as each PR's own tests pass
  rather than left to accumulate and conflict with each other. Also done:
  4.2 (transitive copycode, #1) — module briefs now surface rule
  candidates from any copycode a module `INCLUDE`s, cited against the
  copycode's own lines. And 4.8 (stable rule IDs, #10) — every rule
  candidate in a brief now carries a `MEMBER:BR-nnn` ID (qualified with
  the member name so it's unique system-wide, not just per-module) for a
  human to reference later without needing the full citation. Raised in
  review: there's nowhere yet to look up a `BR-nnn` without knowing which
  module doc it's in — filed as its own follow-up (4.10, #16) rather than
  built here, since it's a new doc type/report, not a brief change. Also
  done: 4.9 (glossary support, #11) — turned out `options.narrative.lexicon`
  already existed in `project.yml` for exactly this purpose, but nothing in
  the pipeline actually read it; only a human with `project.yml` open
  during an interactive Claude Code session ever benefited from it, and
  `mfdoc batch`'s headless prompts had zero access to it. Wired it into
  `module_brief`/`entity_brief`, filtered to terms that actually appear in
  that member's own facts (not the whole glossary dumped in regardless of
  relevance). No new `reference/glossary.yml` file format was needed.
- 2026-08-05: corrected a claim from the #7/#13 work above — the user
  pointed at `SoftwareAG/adabas-natural-code-samples`, a real, official,
  public Natural/Adabas corpus, disproving "no public corpus exists for
  Natural/Mantis/Adabas/Supra" for the Natural/Adabas half of that claim
  (Mantis/Supra still has none found). #13 narrowed back to the COBOL
  course repo's JCL/SQL-DDL content it was actually about; opened #19 for
  the Natural-specific findings. Smoke-tested the scanner against all 227
  real samples from that repo (not committed — exploratory, scratch-dir
  only): no crashes, but `line_recognition_rate` dropped to 0.68 (vs. 0.99
  on our own fixtures), which is real signal our synthetic fixtures don't
  give. `mfdoc calibrate` ranked the gaps; fixed the two cheapest,
  highest-confidence ones from that ranking (4.11, #19 partial) —
  `RESET` and `IGNORE` are real Natural statements with no scanner support
  at all. `RESET #RETURN-CODE` turns out to have been the one pre-existing
  unparsed_line gap in our own MMP0100.nsp fixture since before any of
  today's other fixes, just never named. The rest of #19 (report-writer
  column-position continuations, labelled statements, sequence-number
  stripping) needs proper fixture design, not a one-line regex, and stays
  open.
- 2026-08-05: done: 4.7 (Adabas coupling, #6) — `entity_link` already
  supported `link_kind='coupled'`; nothing emitted it. No shipped fixture or
  public sample pins down a single standard listing format for coupling
  (it's free text in a DDM's Remark column), so the extractor only fires on
  an explicit `COUPL...` mention plus a nearby file/FNR number, marked
  `inferred` rather than `verified` since it's parsed from free text, not a
  structural field. An unresolvable `COUPL...` mention becomes a gap, not a
  guess. New `TEST-COUPLE.ddm`/`.fdt` fixture pair.
- 2026-08-05: done: 4.3 (loop-label resolution, #2) — `RE_READ`/`RE_FIND`/
  `RE_HISTOGRAM` now capture the conventional `R#`/`F#`/`H#` loop label they
  already matched but discarded, recording which entity each labelled loop
  opened. `UPDATE (F1.)`/`DELETE (F1.)` resolve to that entity instead of
  staying `unresolved`. Any other label naming (not the R/F/H convention,
  or a label nothing ever opened) still produces the honest gap rather than
  a guess. New `MMP9200.nsp` fixture exercises both cases.
- 2026-08-05: done: 4.4 (Natural map parser, #3), with an honesty caveat
  worth flagging explicitly. Looked for a real `.nsm` sample to verify the
  format against — checked the shipped fixtures, `openmainframeproject/
  cobol-programming-course`, and `SoftwareAG/adabas-natural-code-samples`
  (the last of which has a "Map Natural Data Area" sample, but it's a
  program that reads map metadata at runtime, not a map source export).
  None exist. Rather than not building it or fabricating unverified
  confidence, extended `natural.py` (maps are already `dialect=natural`,
  `object_type='map'` — not a separate top-level dialect, since they share
  Natural's `DEFINE DATA` syntax) to recognise the *documented* Natural
  map-source convention (level, T/F tag, content, attributes, row/column),
  gated strictly to `object_type='map'` so a wrong guess never reaches an
  ordinary program's statements, and made every map member raise a new
  `map_body_unverified` gap stating plainly that this is unverified against
  a real export. New `MMM9000.nsm` fixture; `*.nsm` added to the natural
  source glob in `project.yml`/`config/project.example.yml` (was missing).
- 2026-08-05: done: 4.11c (leading numeric sequence prefixes, #26). Some
  real-world exports put the sequence number at the *start* of each line
  (`0010DEFINE DATA LOCAL`, no guaranteed separator) rather than in the
  trailing 73-80 field `detect_seq_columns` already handled. Added
  `detect_leading_seq_prefix` (same 90%-of-candidate-lines majority
  threshold, fires only when the trailing detector found nothing) and wired
  it into `split_members`'s existing `strip_seq`. New `MMP9300.nsp` fixture
  exercising both shapes from the issue (no separator and space-padded);
  new `tests/test_sequence_columns.py` for the detection/stripping logic in
  isolation. `source_file.seq_cols` now also records `"L<width>"` for the
  leading case (distinct from the trailing `"start:end"` format) so
  `test_citation_alignment.py`'s existing skip-if-stripped logic covers it
  without changes. `reference/natural-adabas.md` updated with the new
  "Traps" entry.
- 2026-08-05: done: Phase 6 indexes sub-item (part of #9). Added expression
  indexes (`ix_member_upper_name`, `ix_entity_upper_name`,
  `ix_call_edge_upper_callee`, plus `ix_call_edge_callee_id` so SQLite's
  multi-index OR optimisation can cover `orphans()`'s
  `ce.callee_id = m.id OR UPPER(ce.callee_name) = UPPER(m.name)` clause) for
  the `UPPER(...)` correlated-subquery paths in `graph.resolve()` and
  `graph.orphans()` that the issue flagged as "quadratic-ish at scale". New
  `scripts/generate_scale_fixture.py` (gitignored output, same posture as
  the cobol-course fetch script) generates a reproducible synthetic corpus
  to measure this kind of change against; confirmed with it at 5,000
  members / 20,000 call edges: `mfdoc derive` went from ~23.6s unindexed to
  ~0.19s indexed. `EXPLAIN QUERY PLAN` before/after documented in
  `docs/guides/extending.md`'s new "Measuring scale" section.
- 2026-08-05: done: Phase 6 incremental-ingest sub-item (rest of #9, minus
  EBCDIC fixtures). `mfdoc ingest` now skips a source_file whose `sha256`
  matches the prior run's row outright; a changed file keeps its
  `source_file` row (UPDATEd in place, not delete-and-reinsert) so
  `upsert_member` can still match its members by name/library/dialect and
  reuse their existing ids across a content change, rather than every
  changed file minting new member ids. A member a changed file no longer
  produces at all (a concatenated member dropped from a multi-member
  unload) is purged outright. New `db.purge_member_facts`/`db.purge_member`
  centralise what was previously a single bare `DELETE FROM source_line`
  before re-extraction -- that alone was already stale, since every other
  dialect-extractor fact table (variable, data_access, call_edge,
  rule_candidate, ...) was never purged before a member's second
  extraction, so re-running ingest on a *changed* file would have silently
  duplicated all of those rows even before incremental skip-when-unchanged
  existed to make a second run reachable at all.

  Along the way, found and fixed a second, adjacent idempotency bug:
  `graph.run_all()` never purged its own previously-derived gap rows
  (`orphan_module`, `unresolved_call`, `no_ddl_for_entity`,
  `ambiguous_adabas_file`, `sme_question`) before re-deriving, so running
  `mfdoc derive` twice against an unchanged index doubled every one of
  them -- invisible before this issue, since `mfdoc ingest` twice always
  crashed on `source_file.path`'s UNIQUE constraint beforehand, so the
  "run derive again against the same index" path was never actually
  reachable in practice. Fixed with a `DERIVED_GAP_KINDS` purge at the top
  of `run_all()`. New `tests/test_incremental_ingest.py` covers: full skip
  on an unchanged run, `mfdoc coverage` identical between a full rebuild
  and a no-op incremental run, a changed file re-extracted without
  touching any other member, and a member dropped from a changed
  multi-member file being purged rather than orphaned.
  `docs/guides/architecture.md` updated with both behaviours. EBCDIC
  fixtures (the last #9 sub-item) still open.

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

### 3.x Multi-provider `ModelCaller` (Vertex AI support)

`batch.py` already talks to models only through the `ModelCaller` contract
(`str -> ModelResponse`), with all Anthropic-specific code isolated in
`anthropic_caller.py`. Adding Vertex AI is a new caller module, not a change
to `batch.py`/`brief.py`/`validate.py`. Two distinct asks: (1) Claude models
routed through Vertex — likely a data-residency/procurement ask, low risk,
same model family our prompts are built against; (2) Google's own Gemini
models via Vertex — a different model family that our writing-rules
citation discipline has never been exercised against, and needs eval
coverage (5.1) against it specifically before it's trusted client-facing.
Tracked as issue #12.

**Acceptance:** 9 fixtures produce 9 valid documents unattended, with a cost figure
and a retry count reported.

---

## Phase 4 — Extraction correctness debt

Ordered by documentation value per unit of effort, with my honest read of each.

| # | Item | Why it matters | Effort |
|---|---|---|---|
| 4.1 | **Extract `COMPUTE` / `MOVE` / `EXAMINE` as rule candidates** | Currently matched and discarded. Pricing, tolerance and unit-conversion arithmetic *is* business logic — arguably the most sought-after kind in a metals context, where yield and weight conversions carry real money | low |
| 4.2 | **Transitive copycode in briefs** | Rules inside copycode are attributed to the copycode. A reader of the including module never sees them, so a module document can be complete and still miss its own validation rules. *Cf. Azure-Samples/Legacy-Modernization-Agents' signature-registry pattern for cross-chunk consistency — same class of boundary problem, worth reviewing as an implementation reference* | low |
| 4.3 | **Loop-label resolution for `UPDATE (label)` / `DELETE (label)`** | Currently flagged `unresolved`. Track labelled loops and their views; converts a recurring gap into a fact | medium |
| 4.4 | **Natural map (`.nsm`) parser** | No dialect exists. Field-level validation, prompts and edit masks live in maps, and they are user-visible business rules | medium |
| 4.5 | **Better continuation folding** | `CONTINUATION_TAIL` is a heuristic on trailing tokens. Real Natural wraps without them, so long `FIND ... WITH` clauses can be truncated mid-condition — a silent partial rule, the worst failure mode. *Cf. Azure-Samples/Legacy-Modernization-Agents' signature-registry pattern for cross-chunk consistency — same class of boundary problem, worth reviewing as an implementation reference* | medium |
| 4.6 | **Reporting-mode block inference** | Currently flagged and abandoned. Indentation plus `LOOP` gives a usable guess, marked `inferred`. Reporting-mode members are the oldest and most business-critical code, so leaving them unstructured concedes the most valuable ground | high |
| 4.7 | **Adabas coupling** | `entity_link` supports `coupled` but nothing emits it. Physical relationships between Adabas files are currently invisible | low |
| 4.8 | **Stable rule IDs in generated docs** | Citations (`[[MEMBER:LINE]]`) are precise but not a stable handle for referencing a rule across doc revisions or in a gap-register conversation with an SME. Assign a stable ID (e.g. `BR-001`) alongside each citation in `templates/module.md` — a writing-rules/template change, not an extraction change. *Idea from reviewing Azure-Samples/Legacy-Modernization-Agents, which does this* | low |
| 4.9 | **Glossary support** — DONE | We cite raw field names verbatim (`WS-CUST-BAL`); a human-curated mapping to business terms, consumed at brief-generation time, raises readability without inventing facts. Turned out `options.narrative.lexicon` already existed for this in `project.yml` — the gap was that nothing read it programmatically, only a human with the config open during an interactive session. Wired into `module_brief`/`entity_brief`, filtered to terms actually present in that member's facts. *Idea from reviewing Azure-Samples/Legacy-Modernization-Agents' `Data/glossary.json`* | low |
| 4.10 | **System-wide rules register** | 4.8 gave every rule a `MEMBER:BR-nnn` ID, but there's nowhere to look one up without already knowing which module doc it's in. A generated, regeneratable index of every `rule_candidate` across the whole system, with its ID, citation and a condition excerpt — a flat table straight from the fact store, not hand-maintained | low–medium |

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
  recorded, so skipping unchanged files is straightforward. Extend the same idea to
  skip regenerating unchanged *briefs*, not only unchanged ingest rows — a cheap
  extension once the file-level check exists (cf. Azure-Samples/
  Legacy-Modernization-Agents' `--reuse-re` flag, which persists and reuses prior
  analysis rather than recomputing it).
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

| Issue | Phase | Blocks | Rough size | GitHub issue |
|---|---|---|---|---|
| Add pytest suite covering the twelve known defect classes | 1.1 | everything | 1–2 days | done, predates issue tracker |
| pyproject.toml + console script | 1.2 | CI | half day | done, predates issue tracker |
| GitHub Actions: pytest + pipeline + validate | 1.3 | — | half day | done, predates issue tracker |
| Decide licensing and repo posture | 1.4 | client work | discussion | done, predates issue tracker |
| `mfdoc gate` command | 2.1 | CI gating | half day | done, predates issue tracker |
| `mfdoc calibrate` command | 2.2 | Mantis/Supra engagements | 1 day | done, predates issue tracker |
| Implement redaction | 2.3 | any real client source | 1 day | done, predates issue tracker |
| Decide narrative orchestration (A/B/C) | 3 | Phase 3 | discussion | done, predates issue tracker |
| Batch narrative harness | 3 | scale | 2–3 days | done, predates issue tracker |
| Multi-provider `ModelCaller` (Vertex AI support) | 3.x | GCP-only client environments | 1–2 days | [#12](https://github.com/nightingalehq/legacy-functional-docs/issues/12) |
| Extract arithmetic as rule candidates | 4.1 | — | half day | done, predates issue tracker |
| Transitive copycode in briefs | 4.2 | — | half day | [#1](https://github.com/nightingalehq/legacy-functional-docs/issues/1) |
| Loop-label resolution | 4.3 | — | 1 day | [#2](https://github.com/nightingalehq/legacy-functional-docs/issues/2) |
| Natural map parser | 4.4 | — | 1–2 days | [#3](https://github.com/nightingalehq/legacy-functional-docs/issues/3) |
| Continuation folding rework | 4.5 | — | 1–2 days | [#4](https://github.com/nightingalehq/legacy-functional-docs/issues/4) |
| Reporting-mode inference | 4.6 | — | 2–3 days | [#5](https://github.com/nightingalehq/legacy-functional-docs/issues/5) |
| Adabas coupling | 4.7 | — | half day | [#6](https://github.com/nightingalehq/legacy-functional-docs/issues/6) |
| Stable rule IDs in generated docs | 4.8 | — | half day | [#10](https://github.com/nightingalehq/legacy-functional-docs/issues/10) |
| Glossary support | 4.9 | — | half day | [#11](https://github.com/nightingalehq/legacy-functional-docs/issues/11) |
| System-wide rules register | 4.10 | — | 1 day | [#16](https://github.com/nightingalehq/legacy-functional-docs/issues/16) |
| Real Natural gaps vs. SoftwareAG/adabas-natural-code-samples — RESET/IGNORE done, rest open | 4.11 | — | low (done part); medium (rest) | [#19](https://github.com/nightingalehq/legacy-functional-docs/issues/19) |
| Run eval prompts, record results | 5.1 | confidence in workflow | 1 day | [#7](https://github.com/nightingalehq/legacy-functional-docs/issues/7) |
| Citation-accuracy sampling | 5.2 | client assurance claims | 2 days | [#8](https://github.com/nightingalehq/legacy-functional-docs/issues/8) |
| Fetch-on-demand JCL/SQL-DDL fixtures | 5.3 | — | half–1 day | [#13](https://github.com/nightingalehq/legacy-functional-docs/issues/13) |
| Indexes + incremental ingest + scale fixture | 6 | large engagements | 2 days | [#9](https://github.com/nightingalehq/legacy-functional-docs/issues/9) |

Critical path to "safe to point at a client codebase": **1.1 → 1.2 → 1.3 → 2.3**,
plus the licensing and orchestration decisions. Everything else is improvement rather
than risk reduction.

All open items (issues #1–#12) are also tracked on the [legacy-functional-docs
GitHub Project board](https://github.com/orgs/nightingalehq/projects/1).

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
