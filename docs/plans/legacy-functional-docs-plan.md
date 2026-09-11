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

**Progress (2026-09-11):**
- Fixed issue #199: `mfdoc doc-drift`'s existing checks (issue #161) caught
  system-wide/module-scoped drift but nothing keyed on a single dialect --
  a document naming one dialect's own unparsed-line count or
  line-recognition rate (the language-guide doc type's `dialect:`
  front-matter field, per `templates/language-guide.md`) could drift out
  of sync with the fact store with no check to notice it, exactly the gap
  the issue's real-world example described (a reference doc found, by
  chance, still quoting a materially worse recognition rate than the fact
  store computed, after an earlier parser fix had moved the number with no
  narrative regen for every doc quoting the old figure). Folded into
  `docdrift.py` rather than a new subcommand, per the issue's own
  preference and the module's existing per-check-function shape: added
  `graph.dialect_coverage(conn, dialect)` (the same `unparsed_lines`/
  `line_recognition_rate` pair `coverage()` computes, scoped to one
  dialect via `member.dialect` instead of the whole store) and
  `docdrift._dialect_stats_drift`, restricted to `doc_type: language-guide`
  specifically (not any doc bearing a `dialect:` field -- `templates/
  module.md` also emits one, member-scoped, and comparing a module's own
  claim against the whole dialect's totals would misreport drift) and two
  new regexes matching "N unparsed lines" / "X.XXXX line-recognition rate"
  prose, each independently skipped when its own pattern isn't present
  (checked before `dialect_coverage` is ever queried, so a tree of module
  docs that never state these figures pays no extra query cost). The rate
  comparison rounds the fact store's current rate to the same number of
  decimal places the document actually claimed rather than a fixed
  tolerance, so a stale claim can't slip through merely by being within a
  fixed absolute delta of the true value. Also exempted `doc_type:
  language-guide` from `_sources_drift` (mirroring `validate.py`'s
  existing `sources` exclusion for the same doc type, issue #91): its
  canonical front matter states a descriptive `["{DIALECT} source
  files"]` placeholder, not a member name, which `_sources_drift` would
  otherwise always flag as unresolved. Two rounds of Copilot PR review
  caught all of the above (the language-guide `sources` false-positive,
  the fixed-tolerance precision gap, the module-doc false-scope risk, the
  avoidable query cost, and a test gap around dialect scoping) and are
  fixed here. Added `test_dialect_stats_drift_detected_when_unparsed_count_is_stale`,
  `test_dialect_stats_no_drift_when_freshly_regenerated` (both now also
  seeding a second dialect with different totals, to prove scoping),
  `test_dialect_coverage_is_scoped_to_one_dialect` (graph.py's own
  regression), `test_dialect_stats_drift_ignores_non_language_guide_doc_with_dialect_field`,
  and `test_language_guide_sources_placeholder_is_not_flagged`
  (tests/test_docdrift.py); full suite (916 passed, 2 skipped) and the
  bundled fixture pipeline (`ingest`/`derive`/`coverage`/`validate --docs
  examples`, `doc-drift --docs examples` unchanged from before this
  change: the one pre-existing system-overview drift is unrelated to this
  check) both pass.

**Progress (2026-09-11, later):**
- Fixed issue #197 (two parts):
  1. Investigated why #150/#152's `_strip_response_preamble` still let a
     preamble leak through on live `batch` runs this session: the root
     cause was `_PREAMBLE_SEARCH_WINDOW` (300 chars) being too small for a
     realistic multi-sentence stated-intent preamble under Claude Code's
     agent framing (e.g. "I'll review the fact brief and existing rule
     candidates ... then write the corrected document ..."), so the real
     leading `---` fell entirely outside the searched slice and
     `_strip_response_preamble` returned the text untouched -- confirmed
     by constructing a ~400-char preamble of this shape and reproducing
     the leak against the pre-fix code before changing anything. The other
     two candidates named in the issue (a preamble shape the pattern
     doesn't cover, and an interaction with the fenced-code-wrapping case)
     were checked and ruled out: both a fence-then-preamble and a
     preamble-then-fence response already normalize correctly once the
     real `---` is within the search window. Fixed by raising
     `_PREAMBLE_SEARCH_WINDOW` to 900 (comfortably covers a multi-sentence
     preamble while staying well short of where a quoted front-matter
     example would appear in a real prompt). Added
     `test_strip_response_preamble_removes_a_longer_stated_intent_preamble`
     (`tests/test_batch.py`) reproducing the failing shape (invented
     wording, not the real observed text) at a length past the old window
     but within the fixed one; updated
     `test_strip_response_preamble_ignores_a_frontmatter_example_beyond_the_search_window`'s
     filler to size itself off `_PREAMBLE_SEARCH_WINDOW` so it can't go
     stale against a future window change the same way.
  2. Extended the same protection to documents written directly by the
     interactive Claude Code path (`system-overview.md`, an entity/process
     doc, `interface-matrix.md`, `gap-register.md`, `executive-summary.md`,
     `reference/language-guide.md`'s narrative tier) per SKILL.md's "Write
     from the brief" step -- these follow from `mfdoc brief
     --system`/`--interface-matrix`/`--executive` but are written straight
     to disk by the session itself, never through `generate_module_doc`'s
     write site, so `_fix_generated_by_version` never ran against them at
     all. Added `mfdoc clean-doc --file PATH`, a new CLI command reusing
     `batch.py`'s `_fix_generated_by_version` unchanged against an
     arbitrary file on disk (strip + `generated_by:` version correction,
     in place, a no-op when already clean); wired into SKILL.md's "Write
     from the brief" step, to be run against each such document right
     after writing it and before `mfdoc validate`, and added to
     `README.md`'s Quick start. Added
     `test_clean_doc_strips_leaked_preamble_from_an_interactively_written_document`/
     `test_clean_doc_is_a_no_op_and_says_so_when_already_clean`
     (`tests/test_cli.py`). Copilot PR review caught that this initial
     version could falsely report "already clean" for a document
     `_fix_generated_by_version` couldn't actually rescue (a preamble
     still longer than the search window, or no front matter at all) --
     `mfdoc validate` would then still reject the same file `clean-doc`
     just reported clean. Fixed by checking `split_frontmatter` on the
     result before declaring success, and failing loudly (non-zero exit)
     instead when it still can't find valid front matter; added
     `test_clean_doc_fails_loudly_instead_of_reporting_already_clean_when_uncleanable`.
     Full suite (rebased onto main post-#199): 953 passed, 2 skipped (four
     new tests total from this fix).

**Progress (2026-09-11b):**
- Investigated issue #191 (part of the #187 epic): confirmed #183's
  per-chunk whole-member-context re-query problem does **not** apply to
  `testplan.py`'s `test_case_brief`/`test_case_brief_chunk` -- no code
  change made. #183 (PR #206) found that pre-fix `module_brief(conn,
  member_name, rule_range=...)` re-ran ~15 whole-member fact-store queries
  itself, inline, on every one of a chunked member's chunk calls, because
  it always took `conn` and always queried regardless of `rule_range`.
  `test_case_brief_chunk`, by contrast, takes no `conn` parameter at all --
  it only ever renders from `rows`/`system`/`routines` passed in by its
  caller, and its own chunk loop (in both `_generate_member_test_doc_chunked`
  and `plan_test_batch`'s dry-run preview) calls
  `fetch_test_case_rows`/`fetch_routines` once per member, before the `for
  i, (start, end) in enumerate(ranges, ...)` loop, then passes the same
  `rows`/`system`/`routines` into every `test_case_brief_chunk` call across
  it -- the loop body itself performs zero fact-store access, which is the
  property #183 was restoring for `module_brief`. (`plan_test_batch` does
  fetch both twice per member overall -- once inside its earlier
  `test_case_brief()` call, used only to fingerprint the member's full
  brief for skip/resume, and again directly before the chunk loop -- but
  that duplication is at most one extra fetch per *member*, not one per
  *chunk*, so it isn't the pathology #191 is asking about.) This is an
  architectural difference from `module_brief`'s pre-#183 shape, not an
  oversight sharing the same bug: `testplan.py`'s chunk-brief builder was
  written as a pure function over pre-fetched data from the start, so there
  was never a `MemberFacts`-shaped gap to port #183's fix into. No
  behaviour or performance change; full suite unaffected (944 passed, 2
  skipped, same as before this investigation). Closing #191 as
  confirmed-not-applicable.

**Progress (2026-09-11c):**
- Investigated issue #207 (#183 Part 2: a second prompt-cache breakpoint on
  `MemberFacts`' precomputed shared context, layered on top of the existing
  project-level breakpoint from #159/#168) and decided **not** to implement
  it yet -- closing as "not currently safely implementable without a
  section-reorder design change", per #207's own suggested next step.
  Confirmed the budget question first: the Anthropic API's documented limit
  is 4 `cache_control` breakpoints per request (`shared/prompt-caching.md`
  in the bundled Claude API skill, and `anthropic_caller.py`'s own #159
  comment); the existing project-level breakpoint uses 1, so a second,
  member-level one would use 2 -- comfortably inside budget for every code
  path that uses both (`AnthropicCaller.__call__`/`_content` builds one
  `messages[0].content` list per call, chunked or not). Budget was never
  the blocker.
  The structural blocker #207 already named -- `module_brief`'s section
  order renders "Business rules from included copycode" and "Known gaps"
  *after* the rule_range-dependent "Candidate business rules" section, so
  a chunked member's brief is `[shared A][chunk-specific][shared B]`, not
  `[shared][chunk-specific]` -- turned out to have a second layer #207
  didn't call out: even "shared A" (everything module_brief renders
  *before* "Candidate business rules" -- header comments through "Messages
  and error handling") is **not** byte-identical across a member's own
  chunk calls, for two independent reasons found by tracing `module_brief`
  end to end:
  1. The "PARTIAL BRIEF" note itself (`module_brief`'s own `rule_range`
     branch) says "chunk `{this_chunk}` of `{chunk_count}`" -- literally
     different text on every chunk of the same member -- and renders
     *before* "shared A", inside what would need to be the cached block.
  2. The "Internal routines" section (also inside "shared A") annotates a
     routine with `**[documented in chunk N]**` only when that routine's
     chunk differs from `chunk_info`'s current chunk -- so which routines
     get annotated (and the section's exact text) also changes chunk to
     chunk, via the same `chunk_map`/`chunk_info` parameters
     `_generate_module_doc_chunked` already passes per-chunk.
  A third, pre-existing wrinkle (implicit in #207's own vocabulary-section
  reasoning): the "Business vocabulary" section is spliced into the output
  *before* "shared A" (`vocab_insert_at`, right after the intro) but is
  computed by scanning the **fully rendered** brief text for
  `options.narrative.lexicon` hits, chunk-specific rules included -- so
  whenever a project configures a lexicon, its presence/content also
  varies per chunk.
  Net effect: with today's `module_brief` layout, there is no contiguous,
  chunk-invariant substring long enough to be worth a second
  `cache_control` breakpoint without first (a) moving `rule_range`/
  `chunk_info`/`chunk_map`-dependent text (the PARTIAL BRIEF note, the
  routine chunk-annotations, and the vocabulary section's scan scope) out
  of what would become the cached prefix, and (b) re-validating that this
  doesn't change any generated document's actual content -- exactly the
  "real output-shape change... needing its own design pass, tests, and a
  fresh pipeline validation run" #207 flagged as more than a caching-only
  change. Rather than force a narrower, riskier slice under time pressure,
  leaving this closed pending that design pass (or a narrower follow-up:
  render a purpose-built, `chunk_info`/`chunk_map`-free "shared prefix"
  string from `MemberFacts` directly, bypassing `module_brief` entirely for
  the cached block, and let `module_brief` keep doing what it does for the
  rendered document body -- untried here, flagged as the least invasive
  option for a future attempt). No code change; full suite unchanged at
  944 passed, 2 skipped.

**Progress (2026-09-10e):**
- Fixed issue #188: ported `batch.py`'s near-miss/targeted-patch mechanism
  (issue #131, generalized by #170) to `testbatch.py`'s test-generation
  retry loop, which previously had none of it -- every `mfdoc test-batch`
  validation failure, from a single uncited scenario sentence to a
  structural problem, triggered a full chunk regeneration. Reused
  `batch.py`'s `_is_near_miss`/`_localized_findings`/
  `_auto_cite_uncited_assertions` unchanged (they operate only on
  `result["problems"]`/`brief`/`current_text`, none of which are module-
  doc-specific) and added a test-generation-specific
  `build_localized_test_patch_prompt`, wording-adapted from `batch.py`'s
  `build_localized_patch_prompt` for a generated-test document (mentions
  the fenced code block explicitly; "Test brief" heading instead of "Fact
  brief"). One genuine difference from `batch.py`, confirmed by reading
  `validate.py`: `validate_test_doc`'s `_reversed_condition_problems` call
  is gated on `doc_type in ("module", "module_index")`, which a generated-
  test document never is -- so the reversed-condition near-miss class
  `_is_near_miss` also recognizes can never actually fire on this path;
  `_localized_findings`'s uncited-assertion class is the only one that
  applies here, and `reversed_findings` stays in every signature purely to
  keep this a drop-in match for `_localized_findings`'s return shape.
  Applied to both places a test document is actually retried:
  `_generate_test_doc_from_brief` (the direct `mfdoc test-gen`/chunked-
  member path) *and* `run_test_batch`'s own thread-pooled loop for
  non-chunked members -- the latter caught by Copilot PR review: that pool
  loop is the ordinary `mfdoc test-batch` path for every member at or
  below `max_scenarios_per_call` (the common case this issue's own cost
  evidence was measured against) and isn't routed through
  `_generate_test_doc_from_brief` at all, so the fix initially missed it
  entirely. Also fixed, same review round: a targeted-patch call itself
  raising (the real `ClaudeCLICaller` timeout/`RuntimeError` risk
  `_generate_test_doc_from_brief`'s main call already guards against) was
  logged but never appended to `problems`, silently dropping it from the
  next retry_note and the final `DocResult`'s diagnostics on an eventual
  failure -- now appended before falling through to the full retry, same
  as every other failure class this loop tracks. Added
  `test_near_miss_uncited_assertion_in_test_doc_gets_a_targeted_patch_not_a_
  full_retry`/`test_near_miss_patch_failure_in_test_doc_falls_back_to_
  full_retry` (the direct/chunked path) and
  `test_run_test_batch_near_miss_uncited_assertion_gets_a_targeted_patch_
  not_a_full_retry`/`test_run_test_batch_near_miss_patch_failure_falls_
  back_to_full_retry` (the pool-loop path) in `tests/test_test_batch.py`,
  mirroring `test_batch.py`'s corresponding pair. Full suite (rebased onto
  main post-#194): 915 passed, 2 skipped -- six new tests total from this
  fix (the four above plus this branch's pre-existing pair); bundled
  fixture pipeline (`ingest`/`derive`/`coverage`/`validate --docs
  examples`) unaffected (this change touches no dialect/derive/validate
  code, only the model-retry loops).
- Fixed issue #190 (part of the #187 epic): ported `batch.py`'s `plan_batch`/
  `--dry-run` resume preview (issue #160) to `testbatch.py`'s own,
  independently-implemented `prior_chunks`/`brief_sha256` chunked-resume
  state. Confirmed the fragility #160 found in `batch.py` is real here too:
  `testplan.py` derives a scenario's `MEMBER:BR-nnn` id via the same
  `citations.numbered_rule_candidates`/`_rule_id` ordinal-position numbering
  `batch.py`'s rule ids use (see #184), so a rule inserted or removed
  anywhere earlier in a member's rule list renumbers every later scenario
  and changes every later chunk's brief hash, even though that chunk's own
  routine facts are untouched -- exactly the "a resume is silently a full
  regeneration" risk `--dry-run` exists to surface before it costs
  anything.
  - Added `testbatch._test_chunk_reuse_ok`, mirroring `batch._chunk_reuse_ok`
    except calling `validate_test_doc` instead of `validate_doc` (a test
    doc's `language`/`framework` front matter and `MEMBER:BR-nnn`
    references aren't things `validate_doc` alone checks) -- refactored
    `_generate_member_test_doc_chunked`'s previously-inline reuse check to
    use it too, so the real render path and the dry-run preview can never
    drift onto two different reuse rules.
  - Added `testbatch.plan_test_batch`/`TestBatchPlan`/`TestMemberPlan`,
    mirroring `batch.plan_batch`/`BatchPlan`/`MemberPlan` for one
    language/framework target at a time (matching `run_test_batch`'s own
    per-target shape) -- deliberately has no `narrative_reusable` estimate,
    since a chunked test-batch member's index document
    (`_render_chunk_index`) is built deterministically, never via its own
    model call the way a chunked module doc's whole-module reconciliation
    call is.
  - Wired `mfdoc test-batch --dry-run` in `cli.py` (`_print_test_batch_plan`,
    mirroring `_print_batch_plan`), printed once per `--matrix`/
    `--language`+`--framework` target -- same "no --model/--provider/
    --caller needed" contract as `mfdoc batch --dry-run`.
  - Copilot PR review then caught two real gaps: (1) the dry-run branch
    skipped the real loop's per-target template-existence check, so a
    missing/misconfigured template silently reported every member as
    RENDER and exited 0 instead of matching the real run's exit 2
    (single target) / skip-with-warning (matrix mode) -- fixed by
    resolving and checking each target's template before planning it;
    (2) `_test_chunk_reuse_ok`'s revalidation of a cache-hit chunk calls
    `validate_test_doc`, which deletes/reinserts that path's `doc_claim`
    rows and commits -- a real database mutation from a command whose
    whole contract is "writes nothing" -- fixed by adding
    `_readonly_validate_test_doc` (snapshots and restores the affected
    `doc_claim` rows around the call) and a `readonly` flag on
    `_test_chunk_reuse_ok`, used only by `plan_test_batch`; the real
    chunked-render path's own revalidation is unchanged.
  - New tests in `tests/test_test_batch.py`: `plan_test_batch` reporting
    render/skip/chunked correctly (including a fresh-vs-stale
    `prior_chunks` comparison against a real `generate_member_test_doc`
    chunk-reuse count, the same cross-check
    `test_plan_batch_matches_generate_module_doc_chunk_reuse` does for
    `batch.py`), a CLI-level guard that `--dry-run` never reaches
    `_build_model_caller`, a missing-template exit-2 case, and a
    doc_claim-untouched assertion across a cache-hit dry-run plan. Full
    suite: 915 collected, 913 passed, 2 skipped. Bundled fixture pipeline
    (`ingest`/`derive`/`coverage`/`validate --docs examples`) still 71/71
    documents clean, 0 invalid citations of 750.

**Progress (2026-09-10d):**
- Fixed issue #194: `mfdoc ingest`'s incremental-ingest skip decision
  compared only a source file's own content hash (`sha256`) against what
  was recorded at last ingest, so a dialect parser code change with zero
  source-file edits was invisible to it and the stale, pre-fix fact store
  was silently reused -- undetected by `mfdoc gate`/`coverage`, and costly
  to recover from once found (the whole downstream pipeline has to be
  re-run from `derive` onward). Fixed by folding a hash of the dialect's
  own extractor module(s) into the cache key: `cli._dialect_parser_hash
  (dialect)` hashes the dialect name plus the source of the file(s) behind
  `DIALECT_ROUTER[dialect]` (via the new `DIALECT_PARSER_MODULES` map --
  `mantis` includes `natural.py` too, since `mantis.extract` reuses
  `natural.mask_literals`/`orig`; the dialect name itself is hashed in too,
  so two dialects sharing one module, like `adabas_fdt`/`ddm` both routing
  through `adabas.py`, still get distinct cache keys), stored as a new
  `source_file.dialect_hash` column (via `db._COLUMN_MIGRATIONS`, so an
  existing index.db picks it up and simply re-parses every file once on
  upgrade). `cli.py` now asserts at import time that every `DIALECT_ROUTER`
  key has a `DIALECT_PARSER_MODULES` entry, and
  `reference/adding-a-dialect.md`'s registration checklist covers it, so a
  future dialect can't silently ship without this guard. Also folded
  `dialect_hash` into `batch._corpus_signature` (shared by `mfdoc batch`
  and `test-batch`'s resumable-state fast path), which previously hashed
  only `(path, sha256)` and so could take a corpus-level skip past a
  parser fix that changed facts without changing `sha256` or the installed
  version. Deliberately scoped to `dialects/*.py`, not `db.py`/
  `normalise.py` -- those are shared infrastructure that changes for
  reasons unrelated to any one dialect's parsing; folding them in would
  invalidate every source file on any unrelated schema/chunking edit
  instead of just the dialect whose parser actually changed. Considered
  the issue's `--force` flag alternative and rejected it per the issue's
  own reasoning: it depends on a human remembering to pass it after a
  parser change, exactly the failure mode that caused this. Added
  `test_dialect_parser_code_change_invalidates_cache_without_source_edit`
  and `test_dialect_parser_hash_differs_between_module_contents`
  (tests/test_incremental_ingest.py), plus a real pre-migration
  `source_file` table (no `dialect_hash` column) to
  `tests/test_db_migrations.py`'s fixture so its migration assertions
  actually exercise that upgrade path rather than only checking a
  freshly-created table. Two rounds of Copilot PR review then caught: (1)
  the hash needs the dialect *name* folded in too, not just module
  content, since `adabas_fdt`/`ddm` (and the four `environment.py`
  dialects) share one module and would otherwise satisfy each other's
  cache entry; (2) `inspect.getsource()` instead of a raw `__file__` read,
  so this still works under zipimport/frozen installs; (3) deferred
  `normalise.dialect_confidence()`'s full regex scan until after the skip
  check, so an unchanged/skipped file no longer pays for it; (4) an
  import-time assertion that every `DIALECT_ROUTER` dialect has a
  `DIALECT_PARSER_MODULES` entry, documented in
  `reference/adding-a-dialect.md`'s registration checklist; (5) folded
  `dialect_hash` into `batch._corpus_signature` too (shared by `mfdoc
  batch`/`test-batch`'s resumable-state fast path, which previously hashed
  only `(path, sha256)` and could take a corpus-level skip past an
  in-place parser fix); (6) a module `inspect.getsource()` can't read at
  all now fails closed (a fresh UUID every call, never "unchanged") rather
  than falling back to a `__version__` marker that isn't guaranteed to
  bump on every real code change; (7) documented, rather than silently
  left, the residual gap that a dialect-specific entry living inside
  `normalise.py` itself (a `DIALECT_SIGNATURES` pattern, a
  `DEFAULT_SPLITTERS` entry) changing isn't covered by this cache key,
  for the same over-invalidation reason `normalise.py`/`db.py` are
  excluded generally. Added
  `test_dialect_parser_hash_fails_closed_for_a_sourceless_module`. A third
  review round then caught: (8) a hinted source (the common case) was
  still building its full joined `text` and calling `detect_dialect()`
  ahead of the skip check, even though a configured hint makes the dialect
  known for free -- restructured so a hinted file's dialect is just `hint`
  with no join or scan at all, leaving the unavoidable full-text scan (for
  an *unhinted* source, which must be scanned to auto-detect the dialect
  before the skip decision either way) as the only new per-file cost this
  fix adds; (9) added
  `test_batch_recomputes_briefs_when_a_dialect_hash_changes`
  (tests/test_batch.py) -- the existing corpus-signature regression
  coverage only mutated `sha256`, not the new `dialect_hash` dimension;
  (10) documented two further narrow, rare residual gaps in
  `_dialect_parser_hash`'s docstring rather than chasing them into more
  over-invalidation: `DIALECT_ROUTER`'s own wiring (which function it
  calls per dialect) isn't hashed, only the target module's bytes are, so
  repointing a router entry at a different function in an
  already-listed, otherwise-unchanged module would slip through; and a
  config-driven dialect (`supra_dir`'s `options.dialects.supra.labels`)
  can have its effective behaviour changed by a `project.yml` edit alone,
  which this key doesn't see at all. Full suite: 911 passed, 2 skipped;
  bundled fixture pipeline (`ingest`/`derive`/`coverage`/`validate --docs
  examples`) still 71/71 documents clean, 0 invalid citations.

**Progress (2026-09-10e):**
- Investigated issue #196 ("`validate.py`'s negation-detection regex misses
  the 'neither X nor Y' framing of a double-inequality guard"). The fix
  this issue asks for was already shipped: commit f274c3e (PR #164, closing
  issue #157) added `neither`/`nor` to `conditions._NEGATION_NEAR_LITERAL`
  and the `_LIST_NEGATION` clause-boundary exception a few hours before
  #196 was filed -- #196's evidence (4 occurrences across 2 regen runs) was
  gathered before that fix landed and is the same recurring gap #157
  already describes. Confirmed on this branch (checked out fresh from
  `main`, which already contains f274c3e) that `conditions.prose_polarity`
  reads "A is not 'FOO' and is not 'BAR'" and "A is neither 'FOO' nor
  'BAR'" identically (`ne` for both operands, both phrasings). Added
  `tests/test_conditions.py::test_prose_polarity_neither_nor_matches_explicit_double_negation`,
  the explicit equivalence test #196 asked for, to lock that in as a named
  regression case distinct from #157's own tests. No production code
  change needed -- full suite: 908 passed, 2 skipped.

**Progress (2026-09-10f):**
- Fixed issue #198: `ModelCaller` failures (the `claude -p` subprocess in
  `claude_cli_caller.py`, and the Anthropic-SDK-backed `anthropic_caller.py`/
  `vertex_caller.py`) previously surfaced usage-limit/quota exhaustion no
  differently from a genuine per-chunk content/tooling failure: `claude -p`
  as the same generic `RuntimeError`, and the Anthropic/Vertex path as
  whatever raw, unstructured SDK exception `retry.call_with_retry` gave up
  on (e.g. `anthropic.RateLimitError` propagating as-is) -- either way,
  indistinguishable without a human (or an orchestrating agent) noticing
  "everything failed at once" by eye, which materially extended a
  real regeneration run's wall-clock time across several recurrences.
  Researched what signal is actually available rather than assuming: for
  `claude -p`, neither a distinct exit code nor a `--output-format json`
  `subtype` exists for this condition, but extracting strings from the
  `claude` CLI binary itself (v2.1.268) surfaced its own internal error-
  classification regex, which matches `usage limit reached` and
  `credit balance (is )?too low` case-insensitively against output text --
  reused verbatim as the detection pattern so `mfdoc`'s classification
  stays aligned with what Claude Code itself already treats as
  authoritative. For the Anthropic SDK (installed package inspected
  directly), `APIStatusError` carries a `type` attribute from the response
  body's `error.type` (`"billing_error"` for a credit-balance/quota
  problem) and a `status_code`; a 429 (`RateLimitError`) that survives
  every one of `retry.call_with_retry`'s attempts is treated as the same
  "everything failed at once" pattern, not just an ordinary transient
  throttle recovering within the retry budget. Added
  `model_errors.py` (`QuotaExhaustedError` -- a `RuntimeError` subclass, so
  every existing `except RuntimeError`/`except Exception` still catches it
  unless a caller wants to distinguish it -- plus `is_quota_exhaustion_text`/
  `is_quota_exhaustion_error`), wired into all three callers so each raises
  the distinct type instead of a generic failure; `batch.py`'s existing
  per-chunk/per-member exception handling already logs
  `exc.__class__.__name__`, so this alone makes the condition grep-able in
  logs with no change to `batch.py` itself. Deliberately scoped to
  detection only -- no automatic pause/resume-on-quota scheduling, which
  issue #198 explicitly leaves to a future wrapping orchestrator. Added
  `test_model_errors.py` plus new cases in `test_claude_cli_caller.py`/
  `test_anthropic_caller.py`/`test_vertex_caller.py` covering both the
  quota-shaped and genuine-failure paths for every caller; full suite (923
  passed, 2 skipped) and the bundled fixture pipeline both green.

**Progress (2026-09-10g):**
- Fixed issue #183 Part 1 (PR #206): `module_brief(conn, member_name, rule_range=...)`
  ran ~15 whole-member fact-store queries (interface, data access, calls,
  inbound callers, gaps, ...) on *every* chunk of a chunked member, even
  though none of that content depends on `rule_range` -- for a member split
  into 16-30 chunks, the same fixed queries and the same large rendered
  text ran/were resent 16-30 times over. Split `module_brief` into
  `build_member_facts(conn, member_name)` (runs every whole-member
  query once, returning a `MemberFacts` of raw rows) and `module_brief`
  itself, now optionally taking a `facts=` param and skipping every query
  it covers when given. `_generate_module_doc_chunked` and `plan_batch`
  (`batch.py`) now build one `MemberFacts` per member before their chunk
  loops and pass it to every chunk's `module_brief` call; every other call
  site (`cli.py`, the single-call narrate path, tests) is unaffected --
  `facts=None` (the default) rebuilds it exactly as before. Also moved
  `_branch_data_access`'s per-rule data-access lookup (previously its own
  `conn.execute` per IF/ELSE rule with a branch) to filter the same
  `data_access` rows `module_brief` already fetches once, in Python,
  rather than adding a query per rendered rule on top of the ~15
  whole-member ones -- verified via a `_CountingConn` test wrapper that a
  chunked member's per-chunk `module_brief` calls make **zero** further
  fact-store queries once a `MemberFacts` is shared across them. Pure
  efficiency refactor, no behaviour change: new tests in `tests/test_brief.py`
  assert byte-identical `module_brief` output (with and without
  `chunk_map`) whether facts are shared across a member's chunks or
  rebuilt per chunk exactly as before this change; full suite (912 passed,
  2 skipped -- 907 baseline + 5 new) and the bundled fixture pipeline
  (`ingest`/`derive`/`coverage`/`validate --docs examples`, 71/71 documents
  clean, 0 invalid citations) both pass.
  - Part 2 (reuse `MemberFacts` as a second prompt-cache breakpoint,
    #159/#168-style) deferred to #207, not attempted here.
    Confirmed the Anthropic API's documented limit (4 cache-control
    breakpoints per request) isn't the blocker -- a second breakpoint
    alongside the existing project-level one is well within budget. The
    real blocker: `module_brief`'s section order doesn't put every
    rule_range-independent section before the rule_range-dependent
    "Candidate business rules" section -- copycode rules and gaps render
    *after* it -- so a chunked member's brief is shaped `[shared A]
    [chunk-specific][shared B]`, not `[shared][chunk-specific]`, and
    `AnthropicCaller._content` only knows how to cache one contiguous
    leading prefix per breakpoint. Fixing that needs a real design
    decision (reorder sections vs. cache only the leading "shared A" slice
    vs. something else), not just wiring a second `cache_control` block on
    top of Part 1 -- see #207 for the options considered.
  - Copilot review on PR #206 then caught: (1) `run_batch`/`plan_batch`
    each still gathered a member's whole-member facts a second time in the
    chunked path, after their own routing/hashing pass had already built
    them -- fixed by threading that same `MemberFacts` through
    `generate_module_doc`/`_generate_module_doc_chunked` instead of
    discarding it; (2) `_branch_data_access`'s Python-side filter over
    `data_access` rows was itself O(rules * data_access) -- switched to
    `bisect` over a precomputed `line_no` list, O(rules * log
    data_access); (3) `MemberFacts.guard_lines` was pre-rendered with
    whichever `redact` `build_member_facts` happened to be called with,
    so reusing a `MemberFacts` under a different `redact` could leak
    unredacted guard-chain text -- `build_member_facts` no longer takes a
    `redact` at all; it stores raw guard-chain facts
    (`_caller_guard_chain_facts`) and `module_brief` renders them via the
    new `_render_guard_chain(facts, caller_name, redact)` using its own
    caller's `redact` every time, the same as every other field. Full
    suite: 913 passed, 2 skipped.
  - A second Copilot review round, arriving after PR #206 had already
    merged, caught the following (landed as its own immediate follow-up PR
    rather than reopening #206): (4) `run_batch`'s initial per-member
    `module_brief()` call (used only to
    fingerprint the brief for resume/skip) was itself still building a
    `MemberFacts` and discarding it, rather than being the one place that
    builds it and hands it forward to the chunked path -- restructured
    so `run_batch`/`plan_batch` each build exactly one `MemberFacts` per
    member and thread it through both the initial brief and (for a
    chunked member) `generate_module_doc`; (5) `_generate_module_doc_chunked`
    itself still called `fetch_routines()` before ever looking at a
    caller-supplied `member_facts`, even though `member_facts.routines`
    already has the same rows -- reordered to resolve `member_facts` first
    and reuse `.routines` from it (`plan_batch`'s own chunk-range
    computation got the same fix); (6) a stale progress-log line and a
    docstring both still described `build_member_facts` as taking a
    `redact` argument, or `generate_module_doc` as never passing `facts`
    to the non-chunked path -- both corrected to match (5)'s actual code;
    (7) added an orchestration-boundary test
    (`test_run_batch_gathers_a_chunked_members_facts_exactly_once`,
    `tests/test_batch.py`) asserting `run_batch` itself -- not just
    `module_brief(facts=...)` directly -- gathers a chunked member's facts
    exactly once, plus a redaction-safety regression test
    (`test_module_brief_guard_chain_redacts_correctly_when_facts_are_shared_across_different_redacts`,
    `tests/test_brief.py`) proving one shared `MemberFacts` renders
    correctly under two different `redact` policies in a row, with no
    stale leakage either direction. Noted, but deliberately not changed,
    a raised memory-tradeoff finding: `to_run_chunked` now holds one
    `MemberFacts` per pending chunked member for the duration of the
    non-chunked pool's run, trading some peak memory for not re-gathering
    those facts later -- accepted as consistent with `briefs`' existing
    same-shaped tradeoff for non-chunked members, documented inline rather
    than restructured. Full suite: 946 passed, 2 skipped; bundled fixture
    pipeline still 71/71 documents clean, 0 invalid citations.

**Progress (2026-09-10c):**
- Fixed issue #184: `citations._rule_id(member_name, n)` is a pure
  formatting helper -- the ordinal `n` was independently re-derived by
  five call sites, each running its own `SELECT ... FROM rule_candidate
  ... ORDER BY line_no` plus its own `enumerate(rows, start=1)`, with
  nothing enforcing that they stayed in lockstep. Added
  `citations.numbered_rule_candidates(rows)` -- a thin, single-source-of-
  truth wrapper over that `enumerate(rows, start=1)` -- and switched every
  call site to it: `brief.module_brief`'s "Candidate business rules" and
  copycode-rules sections, `brief.rules_register`,
  `structural.thematic_rules_register` (whose per-member running counter
  became a `groupby` over its own already member_id/line_no-ordered bulk
  query, applying the shared helper per member run instead of hand-rolling
  the counter), `testplan.build_member_test_cases`'s scenario naming,
  `validate.module_completeness_problems`'s missing-citation-range
  reporting, and `batch`'s chunk-boundary routine-to-chunk mapping. Only
  the ordinal-assignment step moved -- every call site's own query
  (several already shared via `brief.fetch_rule_candidate_rows`, others
  batched across members for performance and correctly left as bulk
  queries) is unchanged, since the goal was removing the *redundant
  enumeration*, not forcing an awkward combined fetch+number helper where
  a query was already in flight for other data. Pure refactor, no
  numbering-scheme change: full test suite (907 passed) and the bundled
  fixture pipeline (`ingest`/`derive`/`coverage`/`validate --docs
  examples`, 71/71 documents clean, 0 invalid citations of 750) both
  confirm identical `BR-nnn` output before and after. Added
  `test_ids_match_module_brief_exactly` (tests/test_rules_register.py) to
  pin numbering consistency across a third consumer, alongside the
  existing `test_ids_match_rules_register_exactly` pairing
  `rules_register`/`thematic_rules_register`.
- Design spike for issue #186 (part of #182): wrote
  `docs/superpowers/specs/2026-09-10-narrative-memo-cache-design.md`,
  covering a persistent cache that would reuse an already-validated
  narrated sentence for a structurally identical `rule_candidate` elsewhere
  in a project, keyed on a canonical fact-signature (condition operator +
  literal-shape class + field identity + action shape, dialect-normalized).
  Measuring against this repo's own bundled `examples/` fixtures (the same
  59-row set #173's spike measured) found the guard-clause idiom repeats
  cleanly twice but is already fully covered by #173's algorithmic
  templating, while the one genuine same-signature collision actually
  found in the fixtures (two different Mantis members, both with a local
  `STATUS`/`MSG` field pair, same operator and literal shape, completely
  different business meaning) demonstrates the false-match risk concretely
  rather than hypothetically. Recommendation: do not build the general
  cache — the shapes that repeat safely are already served by #173, and
  the shapes that would need a cache are exactly the ones this measurement
  shows can carry different content behind an identical signature. If
  revisited, only the narrowest project-local slice (never cross-project,
  both for signature-generalization and client-data-handling reasons),
  with free-text literals excluded from eligibility entirely and a
  mandatory three-part self-check (citation resolution, token-level
  re-confirmation, `validate_doc`'s reversed-condition/completeness
  checks) before any substituted sentence is ever surfaced — reusing #171's
  own conservative splice discipline rather than trusting a signature
  match alone — and even then only after a real engagement's fact store is
  measured the same way.

**Progress (2026-09-10b):**
- Fixed issue #168: extended issue #159's prompt-caching pattern from
  `batch.py` to `testbatch.py`. Added `build_test_prompt_parts`/
  `build_test_prompt_cache_prefix`, mirroring `build_prompt_parts`/
  `build_prompt_cache_prefix` exactly -- `build_test_prompt` is now just
  `"\n\n---\n\n".join(build_test_prompt_parts(...))`, and the cache-prefix
  helper derives the stable leading substring (instructions + writing
  rules + template) from the same three parts. One difference from
  `batch.py`'s version: `build_test_prompt`'s instructions section is
  templated on `language`/`framework`, so the stable prefix -- and the
  `_apply_test_cache_prefix` call `run_test_batch` now makes once per
  project/target run -- is necessarily per-target rather than shared
  across a `--matrix` invocation's different language/framework
  combinations. Wired via the same `getattr(caller, "set_cache_prefixes",
  None)` duck-typing `run_batch` uses, so `ClaudeCLICaller`/the fake-echo
  test caller are unaffected. New tests in `tests/test_test_prompt_caching.py`
  mirror `tests/test_prompt_caching.py`'s coverage of the `batch.py`
  equivalents.
**Progress (2026-09-10, later):**
- Fixed issue #172: `classify_rules_deterministic`'s keyword taxonomy pass
  only ever ran against whatever `options.overview.themes.taxonomy` a
  project declared -- with the checked-in `project.yml`'s taxonomy left at
  its documented empty default, every one of the bundled fixtures' 59
  `rule_candidate` rows fell straight through to `source='structural'`
  (`mfdoc classify-rules` reported `keyword: 0, structural: 59`), which is
  exactly the "more work for the LLM fallback than necessary" problem #172
  described.
  - Reviewed the actual `condition`/`literals` text of all 59 fixture rows
    (via `mfdoc ingest`/`derive` + a direct fact-store query, per this
    repo's "no client data" policy -- these are the bundled, invented
    steel-mill-order-management fixtures) for genuinely generalizable
    shapes rather than fixture-specific hacks.
  - Added `classify.DEFAULT_TAXONOMY` -- a built-in fallback taxonomy used
    only when a project hasn't declared its own (`classify
    .taxonomy_from_options`, wired into `cmd_classify_rules`), the same
    replace-not-merge convention `conditions.OUTCOME_FIELD`/
    `outcome_field_from_options` and `DISPATCH_FIELD`/
    `dispatch_field_from_options` already establish. Its six themes are
    common business-rule vocabulary and structural shapes, not a guess at
    any client's field names: `validation` (required/invalid/missing
    wording, and a comparison against a blank/space literal -- a common
    required-field-check shape regardless of field name), `error-handling`
    (RETURN-CODE/RESPONSE-CODE/ERROR-CODE/RC field names), `status`
    (bare STATUS/STAT/FLAG fields), `messaging` (MSG/MESSAGE field
    assignment), `data-access` (the "no records found for preceding
    database loop" phrase mfdoc's own extraction generates for an empty
    database loop), and `calculation` (ADD/SUBTRACT/MULTIPLY/DIVIDE/
    COMPUTE). Identifier-shaped patterns use a custom
    `(?<![A-Za-z0-9])...(?![A-Za-z0-9])` boundary instead of `\b`, so a
    field name is isolated correctly whether the codebase joins words with
    a hyphen (`ORDER-STATUS`, Natural's convention) or an underscore
    (`SCHED_STATUS`, seen in this repo's own Mantis-style fixtures) --
    `\b` alone only isolates the first, since underscore is itself a word
    character.
  - Re-running `mfdoc classify-rules --config project.yml` against the
    fixtures now reports `keyword: 37, structural: 22` (62.7% keyword vs.
    0% before), with no change to the fixture pipeline's citation
    validation (`mfdoc validate --docs examples`: 71/71 documents still
    clean, 0 invalid citations).
  - **Caveat, same as #173's design spike modeled:** this repo's bundled
    fixtures are a tiny, invented sample (59 rule_candidate rows from one
    fictional system) -- the 62.7% figure says these six themes cover a
    meaningful share of *this* sample's shapes, not that they'll cover a
    comparable share of any real project's rule mix. A real project should
    still review its own post-run `rule_theme` table filtered
    `source='structural'` and declare its own
    `options.overview.themes.taxonomy` (which fully replaces
    `DEFAULT_TAXONOMY`, not merges with it) once it has a representative
    sample to work from.
  - New tests in `tests/test_classify.py`: coverage for
    `taxonomy_from_options`'s fallback/replace-not-merge behavior, one test
    per new theme against a real fixture row exhibiting that shape, a
    regression test for the hyphen-vs-underscore boundary choice, an
    aggregate check that the default taxonomy classifies at least half of
    the fixture set, and a check that an explicit project taxonomy still
    sees only its own declared themes take effect. Full suite (887 tests)
    and the fixture pipeline (`ingest`/`derive`/`coverage`/`validate --docs
    examples`) both pass with no regressions.
- Investigated and partly addressed issue #160 (batch.py's per-chunk resume
  cache key is fragile to unrelated upstream fact-store changes, with no
  up-front regeneration-scope estimate).
  - **Renumbering-cascade hypothesis: confirmed.** `citations.py`'s
    `_rule_id` numbers a rule's `BR-nnn` id by its position in
    `enumerate(rules, start=1)` over that member's whole `rule_candidate`
    row set, ordered by `line_no` -- every consumer (`brief.py`'s
    `module_brief`, `structural.py`'s `thematic_rules_register`,
    `testplan.py`, `validate.py`'s missing-range reporting) recomputes this
    same positional numbering fresh each time, none of it persisted as a
    stored id. So a single rule added or removed anywhere earlier in a
    member does renumber every later rule, which does appear in every
    later chunk's own rendered brief text (via `module_brief`'s "Each
    carries a stable `BR-nnn` ID" listing) even when that chunk's own
    routine's facts are otherwise unchanged -- exactly the false-
    invalidation mechanism the issue describes. `routine_aware_chunk_ranges`
    compounds this: it recomputes chunk boundaries from the current rule
    count too, so an upstream rule-count change can also reshuffle which
    rules land in which chunk, on top of renumbering them.
  - **Not fixed, on purpose -- documented as a legitimate non-fix.** The
    same global rule ordinal `_rule_id` numbers from is also the exact unit
    `_generate_module_doc_chunked`'s chunk ranges are expressed in (a
    `(start, end)` 1-based ordinal pair over that same rule list), and the
    chunk-boundary summary (`- BR-nnn..BR-nnn -- label`) assumes those
    ordinals are contiguous. Any per-routine or line-anchored renumbering
    scheme would have to change that contiguous-ordinal chunking model too,
    plus `rules-register`, `testplan.py`'s scenario naming, and
    `validate.py`'s missing-range citation logic -- all of which currently
    stay mutually consistent for the boring reason that they all recompute
    the same ordinal from the same query, at generation and validation
    time alike. Changing the scheme is a system-wide id-semantics change
    with a real risk of new, harder-to-spot correctness bugs (e.g. gaps in
    a "reusable in bulk" contiguous ordinal range), for a benefit
    (narrower resume invalidation) already substantially covered by the
    dry-run preview below. Left as-is; `citations.py`'s `_rule_id` docstring
    already documented this exact trade-off before this issue.
  - **Built regardless (independent of the above): a no-model-call resume
    preview.** `batch.plan_batch` mirrors `run_batch`'s own resume decision
    (corpus-signature fast path, per-member brief-hash check, and -- for an
    over-threshold member -- each chunk's own brief hash via a new shared
    `_chunk_reuse_ok` helper, factored out of
    `_generate_module_doc_chunked` so the preview can never drift from what
    a real run actually does) without ever calling the model or writing a
    file. `mfdoc batch --dry-run` (cli.py's `_print_batch_plan`) prints it:
    how many members would skip (cache hit) vs. render, and for each
    chunked member, how many of its chunks are actually reusable vs. would
    re-render -- plus a warning line when every chunk of every chunked
    member would render (a resume that's actually a full regeneration).
    Needs no `--model`/`--provider`/API key, since it returns before
    `_build_model_caller` is ever reached.
  - Tests: `tests/test_batch.py` (`plan_batch` matching a real chunked
    run's actual reuse count, corpus-signature vs. per-member-hash skip
    paths, a fresh member reported as `render`) and
    `tests/test_cli_batch.py` (the `--dry-run` CLI flag never builds a real
    model caller and writes nothing).
  - Docs: `CLAUDE.md`/`README.md`'s `mfdoc batch` command listings now show
    `--dry-run`.

**Progress (2026-09-10):**
- Fixed issue #171: `batch.py`'s near-miss retry path (issue #131,
  generalized to reversed-condition findings by #170) always spent a full
  second model call to patch a near-miss chunk, even for the common shape
  where the flagged uncited sentence is a close paraphrase of a fact the
  *same brief* already states with a citation elsewhere -- the model wrote
  correct content but dropped or misplaced the `[[MEMBER:LINE]]` tag.
  - Added a deterministic pass, scoped to uncited-assertion findings only
    (never reversed-condition ones -- that finding's fix is a polarity
    correction to existing prose, not "attach a citation that already
    exists elsewhere for this claim", so it has no equivalent shape):
    `_auto_cite_uncited_assertions` locates each flagged sentence's full
    (untruncated) text via `_logical_units`, then `_find_confident_
    citation` searches the brief's own already-cited lines for one whose
    key tokens are a *superset* of the sentence's. On a confident match it
    splices the citation into the document text in code and the caller
    (`_generate_module_doc_from_brief`) re-runs `validate_doc` -- only
    falling back to `build_localized_patch_prompt` (unchanged) for
    whichever sentences (if any) got no confident match.
  - **The conservatism tradeoff, spelled out for a future reader:** "key
    tokens" means only backtick- or quote-delimited substrings (a field name
    like `` `ORDER-STATUS` `` or a literal like `` `'CONF'` ``) -- brief.py
    already wraps every field name/literal/condition fragment it renders in
    backticks, so reusing that convention means the token set is exactly
    "the specific things this line asserts", never a bare shared word ("the
    system", "order", "before posting"). A match requires *every* one of
    the sentence's key tokens to appear on one single brief line, and
    exactly one brief line to qualify -- a partial overlap, or more than one
    equally-plausible line, declines rather than guesses. This means the
    pass reliably catches "same field name(s) and literal(s), just missing
    the tag" but will *not* catch a paraphrase that drops the literal/field
    name entirely, rewords a value without quoting it, or draws on a fact
    spread across more than one brief line -- those still cost the one
    model call this issue optimizes away for the common case, which is the
    intended tradeoff (a false negative here just falls through to the
    existing patch/retry path unchanged; a false positive would mean an
    attached citation that doesn't actually support the sentence, which is
    worse than spending the model call).
  - New tests in `tests/test_batch.py`: unit-level coverage for
    `_find_confident_citation` (clear match / no-tokens / ambiguous) and
    `_splice_citation` (declines on a non-verbatim, e.g. wrapped, sentence),
    plus integration tests proving a clear-cut case gets auto-cited with
    zero model calls, an ambiguous case falls back to the model patch
    unchanged, and the reversed-condition near-miss path is untouched
    (verified by monkeypatching `_auto_cite_uncited_assertions` to raise if
    called for a reversed-condition-only near-miss). Full suite and the
    fixture pipeline (`ingest`/`derive`/`coverage`/`validate --docs
    examples`) both pass with no regressions (71/71 documents clean).
- Fixed issue #157: `conditions.py`'s `_NEGATION_NEAR_LITERAL` denylist had
  no entry for "neither"/"nor" -- "the field is neither X nor Y" (a common
  narration of a compound `<>` condition over two literals) read as an
  unhedged equality claim on both literals, producing a false "comparison
  direction may be reversed" failure in `validate.py`. Same real branch
  shape (`IF field<>X AND field<>Y`) also hit a second, related gap: for
  "value other than X or Y", `_clip_at_clause_boundary` clipped the
  "before"-literal search window at the "or" clause boundary, which
  stripped "other than" away from the second literal's (Y's) own window --
  so Y alone still read as an unhedged equality claim even though the
  sentence correctly negates it.
  - Added "neither"/"nor" to `_NEGATION_NEAR_LITERAL`.
  - Added a new, narrower `_LIST_NEGATION` marker set ("neither", "other
    than", "except", "unless" -- the idioms that negate a whole list of
    operands, not just the clause immediately after the marker).
    `_clip_at_clause_boundary`'s "before"-literal half now skips clipping at
    an "or" boundary specifically when a `_LIST_NEGATION` marker appears
    earlier in the window -- "and" is never treated this way, since none of
    these idioms use "and" to join list operands, so a genuine "and"-joined
    independent clause still clips exactly as before (issue #90 unaffected).
  - This is dialect-neutral -- it's a prose-negation fix in `validate.py`'s
    shared comparison-direction cross-check, not a dialect scanner, so no
    per-dialect variant is needed.
  - A third shape from the same issue (a broad citation range paired with a
    generic non-outcome-asserting phrase matching a bare literal substring
    with no relational context at all) is left as a documented follow-up --
    no clean fix was obvious without risking new false negatives on
    genuinely reversed conditions, and the issue itself allowed leaving it
    open rather than forcing something risky.
  - New tests in `tests/test_conditions.py`:
    `test_prose_polarity_detects_neither_nor_negation`,
    `test_prose_polarity_other_than_x_or_y_negates_both_operands`. Full
    suite and the fixture pipeline (`ingest`/`derive`/`coverage`/`validate
    --docs examples`) both pass with no regressions (71/71 documents clean).
- Fixed issue #158: `validate.py`'s `ASSERTIVE` regex matches a unit's
  opening words ("When ", "If ", "The system", etc.) to decide whether it
  needs a citation or hedge, with no exemption for a unit that is itself a
  genuine question rather than a declarative claim. A legitimate open SME/
  gap-register question phrased in the same idiom ("When X occurs, is Y the
  correct outcome?") was flagged as an uncited assertive statement, forcing
  a fragile hand-placed hedge before the `?` as the only workaround.
  - Added `QUESTION_UNIT` (a unit ending in `?`, optionally followed only by
    closing punctuation/quotes and whitespace) and a short-circuit in
    `_uncited_assertions` right after the existing `ASSERTIVE`/`CITATION`/
    `HEDGE` check. Deliberately anchored on the *whole* unit ending in `?`
    so a declarative sentence that merely contains an embedded `?` elsewhere
    (e.g. quoting a literal screen prompt) is still checked normally --
    covered by both a positive and a negative test in `tests/test_validate.py`.
  - Dialect-neutral: this operates on `_logical_units`' plain-text output
    after markdown unwrapping, the same layer `ASSERTIVE`/`HEDGE`/`CITATION`
    already operate on, with nothing dialect-specific to vary per project.
- Fixed issue #159: `batch.py`'s `build_prompt`/`build_reconciliation_prompt`
  assembled one flat prompt string per call, and `AnthropicCaller`/
  `VertexCaller` sent it as a single plain content block with no
  `cache_control` -- so the large writing-rules/template prefix, byte-
  identical across every chunk/member/retry in one project run, was billed
  at full price on every single call instead of being cached.
  - Kept `build_prompt`/`build_reconciliation_prompt`'s return type (a flat
    string) and the `ModelCaller = Callable[[str], ModelResponse]` interface
    completely unchanged -- `ClaudeCLICaller` and the `fake-echo` test
    caller keep receiving exactly the same flat string as before, with no
    special-casing anywhere in `batch.py`'s call sites. Each prompt builder
    now has a companion `..._parts(...) -> list[str]` helper (single source
    of truth: the flat-string builder is just `"\n\n---\n\n".join(parts)`)
    and a `..._cache_prefix(...) -> str` helper that derives the exact
    stable leading substring from the same inputs -- no re-parsing of an
    already-joined string.
  - `build_reconciliation_prompt` also had its section order changed:
    `member_name` (previously woven into the very first sentence) now comes
    after the writing-rules/index-template sections, not before them --
    otherwise no cache prefix could ever be byte-identical across two
    different members, since a cache breakpoint requires an exact match of
    everything preceding it, not just the marked block's own text.
  - `AnthropicCaller`/`VertexCaller` gained `set_cache_prefixes(prefixes)`
    (a caller-side opt-in, empty by default) and split a prompt matching a
    registered prefix into two content blocks -- the stable prefix, marked
    `cache_control: {"type": "ephemeral"}`, and the variable remainder,
    unmarked. A prompt matching no registered prefix (or a caller nobody
    ever called `set_cache_prefixes` on, e.g. any pre-existing test) is
    sent exactly as before, one plain string. `run_batch` computes both
    prefixes once per project run and hands them to any caller exposing
    the method (`getattr(..., None)` duck-typing), touching nothing else.
  - Confirmed (against the installed `anthropic` 1.4.0 wheel's
    `types/usage.py`) that the SDK's `Usage` object already exposes
    `cache_creation_input_tokens`/`cache_read_input_tokens` (both
    `Optional[int]`, `None` when caching wasn't used) -- threaded both
    through `ModelResponse`/`model_response_from_message` (defaulting to 0)
    so cost reporting can eventually show real cache savings.
    `BatchSummary`/`estimate_cost` don't yet aggregate or price these
    fields -- deliberately scoped out as a follow-up, not a blocker, since
    the caching fix itself needed neither.
  - `testbatch.py`'s own `build_test_prompt` (a separate, parallel prompt
    builder for `mfdoc test-batch`) was not touched -- out of scope for
    this issue, which named only `batch.py`/`anthropic_caller.py`, but the
    same technique would apply there.
  - No live end-to-end run against the real Anthropic API was possible in
    this environment (no `ANTHROPIC_API_KEY`); verified instead with unit
    tests against a faked SDK client (`tests/test_anthropic_caller.py`,
    `tests/test_vertex_caller.py`, `tests/test_prompt_caching.py`) plus the
    bundled-fixture pipeline check (`mfdoc ingest`/`derive`/`coverage`/
    `validate --docs examples`), which is unaffected by this change (it
    doesn't call a model).
- Fixed issue #169: `ClaudeCLICaller.__call__` (`claude_cli_caller.py`) only read
  `usage.input_tokens`/`usage.output_tokens` off `claude -p --output-format
  json`'s response, silently dropping `cache_creation_input_tokens`/
  `cache_read_input_tokens` -- under the Anthropic Messages API's usage-object
  shape (which the installed `claude` CLI's own runtime uses internally, and
  which the CLI's `--output-format json` result reuses verbatim), plain
  `input_tokens` reports only a turn's uncached/cache-miss portion once
  prompt caching is in play, so every cost/usage report from a `--provider
  claude-code` run was understating real input-token consumption.
  - Verified directly: the installed `claude` CLI binary
    (`~/.local/share/claude/versions/2.1.267`) itself contains the literal
    string constants `cache_creation_input_tokens` and
    `cache_read_input_tokens` in its own usage-accumulation logic (alongside
    `input_tokens`/`output_tokens`), and a Zod-style schema fragment in the
    same binary types a `usage` object with exactly those four fields (plus
    a nested `cache_creation` object and `server_tool_use`/`service_tier`) --
    this is the CLI's own internal usage shape, strong indirect evidence for
    what `--print --output-format json`'s `usage` object carries, though no
    live `claude -p` call was made in this sandboxed environment (no active
    login/egress) to confirm the exact top-level JSON byte-for-byte.
  - **Correction to this fix's own PR body:** at the time this fix was
    written, its own investigation concluded `ModelResponse` didn't yet carry
    `cache_creation_input_tokens`/`cache_read_input_tokens` and that #159/PR
    #166 had only marked a stable prompt prefix with `cache_control`, not
    added these fields. That was a mistaken read of #159's actual shipped
    diff (confirmed on rebase, once both landed on `main` together): #159
    *did* already add both fields to `ModelResponse` (for
    `AnthropicCaller`/`VertexCaller`, via `model_response_from_message`).
    This fix's own field addition was therefore redundant and was dropped on
    rebase, keeping #159's original fields and comment (extended with a note
    that `ClaudeCLICaller` populates them too) rather than duplicating them.
  - `ClaudeCLICaller.__call__` now reads all four `usage` keys with
    `.get(..., 0)` defaults, so an older `claude` CLI that omits the cache
    fields (or a call that genuinely didn't cache anything) still defaults
    sensibly to 0 rather than raising or under-reporting silently.
  - `tests/test_claude_cli_caller.py`: added coverage for a fake `usage`
    object carrying the cache fields, and for their absence defaulting to 0.
- Fixed issue #170 (continuation of #131, part of #156): the token-cost
  ledger's root-cause analysis found repeated full-chunk regenerations
  chasing a small number of flagged sentences were the single largest
  driver of `mfdoc batch`'s token spend, and #131's cheap targeted-patch
  path only ever applied to one failure class (uncited-and-unhedged
  assertive statements). Generalized it in `batch.py`:
  - `_is_near_miss_uncited`/`build_uncited_patch_prompt` became
    `_is_near_miss`/`build_localized_patch_prompt`, backed by a new
    `_localized_findings` helper that classifies every entry in
    `validate_doc`'s `problems` list as either sentence-localized (names a
    specific citation/snippet a patch prompt can point back at) or
    structural. Two localized shapes are recognized so far: the original
    uncited-assertion summary problem, and `validate._reversed_condition_
    problems`'s reversed-comparison-direction findings (each already names
    its flagged `[[MEMBER:LINE]]` citation). The moment any problem doesn't
    fit either shape, `_localized_findings` returns `None` and the whole
    failure falls through to the existing full-chunk retry, unchanged --
    missing/malformed front matter, a broken forward-reference, an invalid
    citation, and any other structural check stay full-retry-only.
  - `NEAR_MISS_MAX_UNCITED` was renamed `NEAR_MISS_MAX_LOCALIZED` and now
    bounds the *combined* count of localized findings (uncited assertions
    plus reversed-condition flags together), not just uncited ones.
  - `build_localized_patch_prompt` keeps #131's shape (no writing rules or
    template resent, only the flagged findings and an instruction to leave
    everything else unchanged) but renders one section per finding kind
    present, so a chunk with both an uncited assertion and a reversed
    condition gets one combined patch call rather than two separate ones.
  - Dialect-neutral by construction: both recognized failure classes come
    from `validate.py` checks that already operate on `rule_candidate`/
    citation data every dialect's extractor populates the same way: no
    Natural-only or Mantis-only branching was needed.
  - Tests (`tests/test_batch.py`): renamed/extended the existing near-miss
    boundary and patch-vs-fallback tests, added a reversed-condition-only
    near-miss test using the repo's own MMP0100 fixture (`ORDER-STATUS NE
    'CONF'` at line 38) exercising the same call->validate->retry loop, and
    a negative test confirming a missing-front-matter failure still forces
    a full retry. Full suite (`pytest`) and the bundled-fixture pipeline
    check (`ingest`/`derive`/`coverage`/`validate --docs examples`) both
    pass clean (71/71 documents, 0 invalid citations), no regressions.
- Fixed issue #167: `classify_rules_llm` (`classify.py`) issued one
  `caller(prompt)` call per LLM-eligible `rule_candidate` row, each a
  tiny, cheap-in-tokens prompt asking for a single one-word theme. On a
  real project this made `classify-rules` the slowest pipeline step in
  wall-clock terms despite negligible token cost -- a call-count problem,
  not a token-cost one (per the token-cost report tracked under epic
  #156).
  - Rows are now grouped into batches of `_DEFAULT_LLM_BATCH_SIZE`
    (25, the same order of magnitude as `batch.py`'s
    `DEFAULT_MAX_RULES_PER_CALL`, for consistency rather than any other
    relationship) and sent as one prompt per batch via a new
    `_build_batch_prompt`, cutting model-call count by roughly that
    factor. `classify_rules_llm` gained an optional `batch_size`
    parameter (default `None` -> the module constant) so tests (and any
    future CLI flag) can override it without touching the constant.
  - Responses are parsed back per-row by `_parse_batch_response`, which
    matches each line to the row's own `rule_candidate.id` (not its
    position in the batch) -- so a reordered, partial, or garbled
    response can never misassign or drop another row in the same batch.
    Every existing per-row check (taxonomy case-insensitive key
    matching, `_looks_like_a_refusal_or_non_answer`, "can't confidently
    theme stays structural") still runs per-row against the parsed text,
    unchanged in behavior. A row with no usable line in its batch's
    response is left 'structural' (counted in a new `unparsed` field on
    the return dict) exactly as an individual failed/empty call was
    before batching existed; `cmd_classify_rules` in `cli.py` prints it
    when nonzero.
  - Progress reporting and commit granularity stay per-row (not
    per-batch) -- `progress_callback(i, total)` and the `_COMMIT_BATCH_SIZE`
    commit cadence both still count individual rows, so `cli.py`'s
    existing callback usage needed no changes.
  - `tests/test_classify.py` gained unit tests for
    `_build_batch_prompt`/`_parse_batch_response` (including a batch
    with one malformed/unparseable row mixed with otherwise-good rows)
    and an end-to-end test asserting call count drops below row count;
    several pre-existing tests that assumed one call per row were
    updated to either pass `batch_size=1` (where the test's point was
    commit/token/limit accounting, not batching itself) or to echo each
    row's id back from the fake caller so batched responses parse
    successfully. Verified against the bundled fixtures with a
    fake-echo caller: 59 structural rows across 3 model calls instead of
    59 (default batch size 25).
- Closed issue #161: new deterministic `mfdoc doc-drift` command
  (`docdrift.py`), following `structural.py`'s pure-extraction/comparison,
  no-model-call contract. Given a generated document (or a directory of
  them) and the current fact store, reports which of the document's own
  citable numbers no longer match what the fact store would produce
  today — a `mfdoc coverage`-style plain mismatch list, not a document
  rewrite. Complements `mfdoc validate` (citation *integrity*) rather than
  replacing it: a document can validate cleanly while still citing a stale
  number.
  - v1 checks four things, each independently additive (a document with
    none of the checkable patterns present reports no mismatches rather
    than a false failure): (1) for `doc_type: module`/`module_index`/
    `executive_summary` docs, the highest `MEMBER:BR-nnn` id cited in the
    body against `fetch_rule_candidate_rows`'s current count for that
    member — this is the one number in a narrative document that's
    contractually fixed text (`reference/writing-rules.md` requires
    copying it in verbatim), not paraphrased prose, which is what makes it
    reliable where an arbitrary prose numeric claim is not; (2)
    front-matter `sources:` entries still resolving to a real member or
    entity; (3) a `doc_type: gap-register` document's own stated "N gap(s)
    total"/"N high, N medium, N low" figures against a fresh `gap` table
    count; (4) a `doc_type: system-overview` document's stated "NN.N% line
    recognition" against a freshly recomputed `graph.coverage()`. Running
    it against this repo's own `examples/outputs/docs` found three of
    these fixture docs were themselves already stale relative to the
    checked-in fixtures' current derive output (gap-register.md's totals,
    system-overview.md's line-recognition figure, ORDENQ.md's rule count) —
    a real, if modest, proof that the check works as intended.
  - Deliberately out of scope for v1 (noted in the PR rather than left
    implicit, per this file's "Update the documentation" guidance): the
    `confidence_summary` front-matter counts the issue's own text named as
    a candidate check turned out, on inspection against this repo's real
    example docs, to require counting `*(inferred ...)*`/`*(unresolved
    ...)*` inline markers in freeform prose — a heuristic that undercounted
    against `examples/outputs/docs/natural/MILLPROD/MMP0100.md`'s own
    already-accepted body, i.e. exactly the kind of guessed pattern-match
    CLAUDE.md's architecture principles warn against relying on. Also out
    of scope: `call_resolution_rate`/`entity_definition_rate` and
    executive-summary risk-score ("risk score NN (rule count NN, ...)")
    prose checks — plausible follow-ups once/if a stable phrasing
    convention for them is established, but not reliable enough yet to
    ship without inventing an assumption about narrative wording.
- Design spike for issue #173 (part of #156): wrote
  `docs/superpowers/specs/2026-09-10-deterministic-rule-templating-design.md`,
  proposing deterministic sentence templates for the most mechanical
  `rule_candidate` shapes (single-condition-equals-literal → single MOVE/
  ASSIGN action, plus a "no records found" guard-clause idiom), spliced
  into the model's chunk brief as pre-rendered, cite-as-your-own sentences
  rather than removed from generation entirely. Measuring against this
  repo's own bundled `examples/` fixtures (the only "real" data available
  to this repo — never client data, per `CLAUDE.md`) found roughly 22% of
  59 `rule_candidate` rows meet the strict eligibility criteria, explicitly
  caveated as a small, non-representative fixture sample, not a sizing
  estimate. Recommendation: worth building, but only the narrowest slice
  first — just the single-condition/single-literal/single-action shape,
  behind an opt-in `options.narrative.template_eligible_rules` flag,
  re-measured against a real engagement's fact store before adding the
  guard-clause idiom, `ELSE`-paired variant, or any other shape. No
  contradiction with the near-miss-patch generalization proposed in #170
  or the deterministic auto-citation pass proposed in #171 — this design
  composes with both rather than modifying either.

**Progress (2026-09-09):**
- Fixed issue #151: real SME review feedback asked, across several
  independent examples, for two framing changes to the module-doc
  narrative rather than a new document type.
  - `reference/writing-rules.md` gained a new "Business-first sentence
    framing" section: a rule sentence's main clause should state the
    business trigger and resulting action, with field names/literals/
    citation as supporting detail, not the sentence's subject -- citation
    discipline is unchanged, only what leads the sentence. Includes a
    before/after example pair in the same style as the existing "How to
    turn a rule candidate into a documented rule" section. `batch.py`'s
    narrative prompts embed `writing_rules` verbatim and needed no separate
    change; `SKILL.md` only references `writing-rules.md` and duplicates
    none of its guidance.
  - `brief.py`'s `module_brief` "Inbound callers" section previously cited
    only the call line itself for every callee, even one reachable from
    exactly one call site. Added `_caller_guard_chain`/`_enclosing_condition`:
    for a single-caller callee, the caller's own preceding `call_edge` rows
    (already ordered by `line_no`) are summarized with their innermost
    enclosing `rule_candidate` condition, when one exists -- read-only
    synthesis over facts already in the store, no new extraction, condition
    text passed through `redact` like every other condition rendering in
    this module. Scoped to exactly one known call site; with more than one
    caller there is no single guard chain to point to. `templates/module.md`'s
    "How it is invoked" section now instructs the narrator to use this when
    present.
- Fixed issue #148: `natural.py`'s `FIND`/`READ`/`HISTOGRAM` deliberately
  never push onto `open_blocks` (nesting integrity -- see
  `_END_TO_OPENERS`'s comment), which left statements between the verb line
  and its own `END-FIND`/`END-READ`/`END-HISTOGRAM` with no scope signal
  tying them to "this runs once per record actually found/matched." For a
  single-record `FIND (n) ... WITH <key>` existence check with no
  `IF`/`ELSE` in sight, the narrative stage had nothing but line adjacency
  to infer the found-branch shape from, and could (and did, on a real
  engagement) invert it -- narrating a found-branch assignment block as "the
  assumed default when not found."
  - Added `data_access.end_line` (new column, migrated via
    `_COLUMN_MIGRATIONS`): the found-body extent for a FIND/READ/HISTOGRAM
    row, resolved from its own matching END- keyword via a new,
    deliberately separate `access_opens` per-verb stack in `natural.py`'s
    `extract()`/`_match_data_access()`/`_match_rules()` -- never touching
    `open_blocks`, so the nesting-integrity fix stays intact.
  - `brief.py`'s "Data access" section now renders a `found-body extent
    [[MEMBER:LINE-LINE]]` fact on any FIND/READ/HISTOGRAM row whose extent
    resolved, with an explicit "no implicit not-found branch" sentence.
    `reference/writing-rules.md` gained a matching "Inventing a not-found
    branch for FIND/READ/HISTOGRAM" rule.
  - Checked `mantis.py` for the equivalent idiom (issue's own suggestion):
    Mantis's `GET`/`OBTAIN` are single-line statements with no `END-GET`/
    body block of their own (unlike Natural's `FIND`/`READ`/`HISTOGRAM`), so
    there's no analogous verb-owned line range to record. A Mantis loop
    shape (`WHILE`/`FOR` wrapping repeated `GET`s) already gets `end_line`
    under mantis.py's existing block-extent tracking (issue #132) --
    out of scope here, left as-is.
- Fixed issue #150: `--provider claude-code` (`ClaudeCLICaller`) runs Claude
  Code itself in headless mode, not a bare completion like `AnthropicCaller`/
  `VertexCaller` -- on real `mfdoc batch`/`test-batch` volume this produced
  responses that don't literally start with `---` (a wrapping code fence or
  a line of lead-in commentary before the document), failing
  `split_frontmatter`'s strict leading-`---` check, plus 600s subprocess
  timeouts with nothing analogous to `AnthropicCaller.DEFAULT_MAX_TOKENS`
  bounding a single call's output.
  - Added `_strip_response_preamble` (`batch.py`), folded into the existing
    `_fix_generated_by_version` post-processing hook every response.text
    write site (both `batch.py` and `testbatch.py`) already calls right
    before `write_text` -- caller-agnostic on purpose, a no-op for a
    response that already starts with `---`. Strips a wrapping code fence
    (only the delimiter, never the document's own trailing newline) and,
    failing that, a pre-`---` preamble within a 300-char search window. A
    candidate `---` also has to actually open a real YAML mapping
    (`_looks_like_real_frontmatter`) to be accepted -- added after
    Copilot's PR review flagged that a naive "any `---` line" match would
    misidentify `build_prompt`'s own bare `"\n\n---\n\n"` section
    separators. The window stays too: on its own the YAML check isn't
    enough, since a worked front-matter *example* quoted verbatim inside a
    prompt's writing-rules/template text does parse as a real mapping --
    the fake-echo test caller (which echoes its whole prompt back as the
    "response") exercises both false-positive shapes.
  - `ClaudeCLICaller` gained an opt-in `max_budget_usd` (`--claude-code-
    max-budget-usd` on every subcommand that already exposes
    `--claude-code-timeout`), passed through as `claude -p`'s own
    `--max-budget-usd` -- the closest available lever to `claude -p --help`
    exposing no output-token cap at all; unset by default, since this repo
    doesn't guess at a business-tuned dollar figure nobody asked for.
    Rejects a non-positive value immediately (`ValueError`), rather than
    letting a nonsensical cap reach `claude -p` for a confusing failure.
- Fixed issue #149: `natural.py`'s `_match_arithmetic` deliberately skips a
  COMPUTE/ADD/SUBTRACT/MULTIPLY/DIVIDE/MOVE/EXAMINE/bare-`:=` assignment
  whose RHS has no literal (a pure variable/array-element move, e.g. `#REF
  := #ARRAY(#INDEX)`) — reasonable on its own, but such a statement then
  fell through to `extract()`'s own last-resort matcher, which set
  `matched=True` purely on regex match without ever calling `insert()`.
  Net effect: the statement left *no* trace anywhere -- no `rule_candidate`,
  and (since "matched" suppresses the `unparsed_line` path too) no gap
  either. A real SME review had flagged exactly this shape's complete
  absence from a generated document as a significant comprehension gap.
  - Added `_match_arithmetic_low_confidence` (`natural.py`), tried in that
    same last-resort fallback (both the main loop and the generic-label
    rematch path) right before the truly-structural no-op checks
    (RESET/IGNORE/SET CONTROL/etc.): same COMPUTE/bare-`:=` shape as
    `_match_arithmetic`, same loop-counter exclusion (`ADD 1 TO`/`SUBTRACT
    1 FROM` still stays silent, on purpose), but with the literal gate
    dropped and every row tagged `confidence='low'` — a new value alongside
    `rule_candidate.confidence`'s existing `verified`/`inferred` (schema
    comment in `db.py` updated to document it). The statement now leaves a
    real trace, distinguishable downstream from a real literal-bearing
    decision.
  - Against the bundled fixtures: 5 previously-silent statements now
    surface as low-confidence `rule_candidate`s — MMP0100:48's `ADD
    STOCK-VIEW.AVAIL-WEIGHT TO #AVAIL-TOTAL` accumulator and MMP0400:42-45's
    four variable-to-variable MOVEs populating `HOLD-VIEW` before its
    `STORE`. `rule_candidates` 54 -> 59 in `tests/test_coverage_snapshot.py`'s
    snapshot; `MMP0100`'s already-narrated module doc renumbered its
    `BR-010`..`BR-017` to `BR-011`..`BR-018` and gained a new `BR-010` for
    the newly-surfaced accumulator (per `reference/writing-rules.md`'s
    "Stable rule IDs" section: inserting a rule earlier in the source shifts
    every later ID in that module, by design) — propagated to every
    generated-test artifact under `examples/outputs/tests/.../MMP0100.*`
    that referenced the shifted IDs, and to the deterministic overview docs
    (`rules-register.md`, `rules-theme-register.md`, `complexity.md`,
    `test-plan-register.md`, `index.json`/`index.db`) via their own `mfdoc`
    commands. `MMP0400` has no narrated module doc yet, so its own 4 new
    rules needed no manual renumbering.
  - Scoped to Natural only, matching the issue's own repro; `mantis.py` was
    checked (per the issue's own suggestion) and has no equivalent
    fallback-swallow risk in its own arithmetic/assignment matching, so it
    was left untouched, as the issue itself only asked to check it.
  - Item 2 in the issue (loosening `_match_arithmetic`'s literal-only gate
    itself for the narrower case of a control-value-carrying variable) is
    left as a follow-up, per the issue's own framing of it as a stretch
    goal beyond the core fix.

**Progress (2026-09-08):**
- Fixed issue #133: neither completeness mechanism caught a whole
  `call_edge`/`interaction` statement (a `DO`/`PERFORM` subroutine call, a
  `PROGRAM`+`DO` external call, a `RELEASE`, a `PROMPT`, a `CHAIN`/
  `TRANSFER`) dropped from every citation in a generated document entirely.
  The #50 coverage-completeness gate (`module_completeness_problems`) only
  cross-references `rule_candidate` ids, which none of those constructs
  ever create; the #59 per-statement check (`_statement_completeness_problems`)
  only inspects the paragraph of a citation whose range *already* covers
  the statement's line, so a line missing from every citation has no range
  there for it to look inside.
  - Added `statement_citation_coverage_problems` (`validate.py`): for every
    non-dynamic `call_edge`/`interaction` row belonging to a member, checks
    that its line is covered by *some* `[[MEMBER:LINE]]` citation anywhere
    across that member's `doc_type: module` document set (unioned across
    chunks, the same way `module_completeness_problems` already unions
    `BR-nnn` coverage) — a citation-coverage check, not a mention check.
    `data_access` rows are out of scope (CRUD/field-level access already
    has `unused_entity_fields`); scoped to `doc_type: module` only, same
    reasoning `module_completeness_problems` and `_statement_completeness_
    problems` already document. Wired into `validate_tree` as
    `statement_coverage_problems` and folded into `mfdoc validate`'s exit
    code (`cmd_validate`, `cli.py`) the same way `completeness_problems`
    already is — a hard failure, unlike #59's advisory-only check.
  - Confirmed #59's `_statement_completeness_problems` check *is* wired into
    the standard `mfdoc validate` pass (`validate_doc`'s per-citation loop
    in `validate.py`, gated the same way the reversed-condition check is,
    on `module_doc_checks` and a resolved line range) — not dead code, so
    the "a DO call inside a cited range still went unmentioned" half of the
    issue's report is a real false-negative in that check's own heuristic
    (or the model regenerating differently), not a wiring gap. Left as-is:
    no reproduction of that narrower miss was found against the bundled
    fixtures, and the new coverage check above is the higher-leverage fix
    the issue asked to prioritise.
  - New tests in `tests/test_validate.py` (`_member_with_statements`
    fixture, already built for #59): flags a call never covered by any
    citation, accepts one inside a cited range regardless of prose mention,
    unions coverage across chunk documents, ignores dynamic call edges,
    ignores `module_index`/non-`module` doc types, and verifies the gap
    is folded into `validate_tree`'s hard-failure result end to end.
  - `docs/guides/architecture.md`'s stage-4 section now documents both
    hard-failure completeness checks (#50 and this one) alongside the
    three existing advisory ones.
  - Full suite green (726 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739, `mfdoc validate` exit 0)
    — no new coverage gap against the bundled fixtures, only the existing
    (pre-existing, unaffected) advisory `_statement_completeness_problems`
    findings for paraphrased-but-uncited-by-name targets.
- Fixed issue #123: three of the four `environment.py` extractors
  (`extract_sql_ddl`, `extract_copybook`, `extract_cics_csd`) never called
  `add_gap` — an unrecognised `CREATE TABLE` column or top-level statement,
  copybook line, or CSD `DEFINE` line was silently dropped with no trace in
  the gap register, unlike `natural.py`/`mantis.py`, which raise an
  `unparsed_line` gap for every statement they can't recognise.
  `extract_jcl` already raised one member-level `unparsed_line` gap when a
  JCL member produces zero `EXEC` steps and was not touched.
  - `extract_sql_ddl` now raises an `unparsed_line` gap for a column
    definition inside a `CREATE TABLE` body that matches neither
    `DDL_NOISE` (a constraint clause) nor `RE_COL`, and for any top-level,
    semicolon-delimited statement matching neither `RE_CREATE_TABLE` nor
    `RE_CREATE_INDEX`.
  - `extract_copybook` now raises an `unparsed_line` gap for a non-comment,
    non-blank line that doesn't match `RE_COB` (an 01-49-level field entry).
  - `extract_cics_csd` now raises an `unparsed_line` gap for a non-comment
    line with no `DEFINE` matching `RE_CSD_DEFINE`.
  - All three follow the exact same `add_gap(conn, "unparsed_line", ...,
    severity="low", raw=...)` convention already used by
    `natural.py`/`mantis.py`/`adabas.py`/`supra.py` — no new gap kind.
  - New `tests/test_environment_gaps.py`: invented (not client-derived) DDL,
    copybook, and CSD fixtures, each with one deliberately unrecognisable
    line, verifying the new gap is recorded, plus a "clean input records no
    gap" counterpart for each extractor.
  - `reference/environment.md` and `reference/adding-a-dialect.md` updated
    to describe the new per-line/per-statement gap behaviour instead of the
    prior "these three never gap" caveat.
  - Full suite green (739 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739, `mfdoc validate` exit 0)
    — the bundled `ddl`/`csd` fixtures were already fully recognised, so no
    new gaps appeared against them; the 4 pre-existing `unparsed_line` gaps
    in `unparsed_lines` coverage are unrelated Natural-scanner gaps, not new
    ones from this change.
- Fixed issue #132: `mantis.py`'s `extract()` only back-filled
  `rule_candidate.end_line` (and, for `IF`, its paired `ELSE`'s
  `pair_line_no`) when the popped block was an `IF` — `WHILE`/`FOR`/`CASE`
  opened and depth-tracked correctly but never remembered their own
  `rule_candidate.id`, so their `end_line` stayed `NULL` forever, and a
  `CASE`'s `WHEN` branches (which don't open their own `open_blocks` entry
  at all) had no extent tracked whatsoever. Observed effect on a real
  engagement codebase (no client content here — reproduced with an
  invented fixture): a guarded database read that lexically belonged to
  one `WHEN` branch got narrated as running unconditionally across every
  branch of the dispatch, because nothing in the fact store said which
  branch it was inside.
  - Generalised the old IF-only `if_rule_ids` dict into `block_rule_ids`
    (keyed by the opening line, same as before) so `WHILE`/`FOR`/`CASE`
    get `end_line` back-filled at their matching `END` the same way `IF`
    already did. Added a parallel `when_stack`, pushed/popped in lockstep
    with `open_blocks`, so each `WHEN`'s extent resolves to wherever comes
    first: the next sibling `WHEN`, or the enclosing `CASE`'s own `END`.
  - New tests in `tests/test_mantis_rules.py`:
    `test_case_when_branch_extent_is_recorded` (the exact reported shape —
    a `CASE`/`WHEN` dispatch with a guarded `GET`+`IF FOUND` in one branch
    and unrelated statements in its siblings; verified to fail against the
    pre-fix code) and `test_while_loop_extent_is_recorded`.
  - `natural.py`'s `_match_rules` has the same gap for its own multi-way
    dispatch (`DECIDE ON`/`DECIDE FOR` + `VALUE OF`), and additionally for
    `FOR`/`REPEAT`/`AT-EVENT`/`ON ERROR`/`IF NO RECORDS FOUND` — none of
    those get `end_line` populated today either. Left unfixed here: unlike
    Mantis's single `extract()` function, Natural's `_match_rules` is
    called from two sites in `extract()` and threads `if_rule_ids`/
    `else_rule_ids` through both, so generalising it touches every
    block-opening branch across both call sites — a real, scoped follow-up
    (not "Natural-only, defer forever"), but bigger than this issue's fix
    and deliberately left as a documented gap rather than scope-creeping
    this PR.
  - Full suite green (719 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739).
- Fixed issue #134: `mantis.py`'s
  `RE_CLEAR = re.compile(r"^\s*CLEAR\s+(?P<rest>.+)$", re.I)` matched
  greedily to end of line, and its handler did nothing with the captured
  `rest` beyond setting `matched = True` -- Mantis's colon-chained-clause
  convention lets one physical `CLEAR` line carry independent trailing
  statements (e.g. `CLEAR SCREEN:ATTRIBUTE(SCREEN)="RESET":FLAG=""`), so
  a plain field assignment chained after
  the screen-clear target had no path to ever being recognised: `RE_CLEAR`
  consumed the whole line and set `matched = True` before `RE_ASSIGN`
  further down the same if/elif chain ever got a look at it. Unlike every
  other "recognised but intentionally not a rule" statement type (`PAD`/
  `UNPAD`, `RELEASE`), the discarded content left no trace anywhere in the
  fact store -- not a `rule_candidate`, not a `gap`, nothing.
  - `RE_CLEAR`'s handler now splits `rest` on `:` at paren-depth 0 in the
    masked text (the same rule `_assignment_pairs` already uses for
    colon-chained `ASSIGN` lines, so a literal or subscript expression
    containing `:` can't wrongly split a clause), then checks each clause
    after the first (the actual clear-target list, never itself an
    assignment) against `RE_ASSIGN`. A clause shaped like `ATTRIBUTE(...)=
    ...` -- setting a screen attribute, not a plain field -- stays
    untracked exactly as before; a clause that resolves to a plain field
    assignment now gets its own `ASSIGN` `rule_candidate` row (and updates
    `last_assign`) like any other `ASSIGN` statement.
  - New tests in `tests/test_mantis_rules.py`:
    `test_clear_with_chained_plain_assignment_still_records_the_assignment`
    (verified to fail against the pre-fix code) and
    `test_clear_with_only_screen_formatting_clauses_records_no_rule`
    (no spurious rule or gap when every trailing clause is screen
    formatting).
  - Full suite green (731 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739).
- Fixed issue #124: the Supra gap message (`supra.extract`), the CLI's own
  `DIALECT_CALIBRATION_HINTS["supra_dir"]` hint, and `reference/mantis-
  supra.md` all told a calibrator to override `dialects.supra.labels` in
  project config when the shipped label patterns didn't match a site's
  directory report -- a config path that didn't exist. `supra.extract()`
  took no config parameter at all and always read the module-level
  `LABELS` dict directly; `config_validate.py`'s `OPTION_SPECS` had no entry
  for it either, so a project that added the documented block got no error
  and no effect.
  - Implemented the advertised mechanism instead of just correcting the
    docs: `supra.labels_from_options` reads `options.dialects.supra.labels`
    (a mapping of label name to a regex string) and merges it key-by-key
    over the `LABELS` defaults -- deliberately merge, not replace-whole-
    dict like `conditions.py`'s single-pattern `outcome_field_pattern`/
    `dispatch_field_pattern`, since a site's report usually only disagrees
    with the shipped wording for one or two of the eight label keys.
    `supra.extract` now takes an optional `options` parameter and uses it.
  - `DIALECT_ROUTER` (`cli.py`) now threads `cfg["options"]` through to
    every dialect's extractor (`(conn, mid, lines, name, options)`), not
    just Supra's -- almost every other extractor ignores the new parameter
    today, but the router no longer has a fixed signature that structurally
    can't pass config through.
  - Added `OptionSpec("options.dialects.supra.labels", ...)` to
    `config_validate.py`, with a new `_dict_of_supra_labels` check
    validating both that each key is one of the eight recognised label
    names (an unknown key is almost always a typo that would otherwise
    silently do nothing) and that each value is a valid regex.
  - Corrected the gap message and CLI calibration hint to reference
    `options.dialects.supra.labels` (matching the `options.*` convention
    every other `OPTION_SPECS` entry uses), and rewrote `reference/mantis-
    supra.md`'s Supra calibration section to describe both real paths:
    the new per-project config override for a one-off site quirk, and
    editing `LABELS` directly for a change that should apply to every
    project.
  - New `tests/test_supra_dialect.py`: a synthetic directory-report
    snippet using a nonstandard dataset label ("FILE-ID:") the shipped
    defaults don't recognise -- confirms zero datasets and a high-severity
    `unparsed_line` gap naming the config key with no override (regression
    guard for unset/default behaviour), confirms the override recognises
    the dataset and drops the gap, confirms overriding one key leaves the
    other `LABELS` keys' recognition intact (merge, not replace), and
    exercises `config_validate`'s acceptance/rejection of a well-formed
    override, an unknown label key, and an invalid regex.
  - Full suite green (750 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739) -- the bundled Supra
    fixture already matches the shipped `LABELS` defaults, so no fixture
    change was needed to exercise the non-override path.
- Fixed issue #128: a chunk's "documented in chunk N" forward reference
  (`module_brief`'s `chunk_map` parameter, the fix that gave a chunk
  something concrete to say instead of a vague "covered elsewhere") was
  never itself checked against the chunk it actually names -- each chunk
  validates and retries independently (`_generate_module_doc_chunked`'s
  per-chunk loop), so a wrong, stale, or hallucinated chunk number
  previously passed cleanly as long as the chunk making the claim was
  itself well-formed.
  - Added `forward_reference_problems` (`validate.py`), a new tree-level
    check alongside `module_completeness_problems`/
    `statement_citation_coverage_problems`: groups a member's `doc_type:
    module` chunk documents by the `.chunkNN.md` filename convention
    `_generate_module_doc_chunked` writes, then for each concrete "documented
    in/covered by chunk N" claim (`_CONCRETE_FORWARD_REFERENCE`, the
    literal-number counterpart to the existing vague-deferral pattern
    `_deferred_reference_problems`/`DEFERRED_REFERENCE` already matches --
    the two patterns are deliberately disjoint) finds the nearest of that
    member's known `routine` names mentioned before the claim in the same
    paragraph, then confirms chunk N both exists in the member's own
    chunk-file set and actually mentions that routine somewhere in its
    body. Flags either "chunk N doesn't exist" or "chunk N exists but never
    mentions the named routine."
  - Advisory only (wired into `validate_tree`'s `forward_reference_problems`
    key, printed by `cmd_validate` but never subtracted from
    `documents_ok`/the exit code) -- same reasoning `_statement_completeness_
    problems` (#59) already documents for a narrower heuristic: identifying
    which routine a deferral is about is a prose-proximity heuristic, not a
    fixed citation shape, so a false negative is more likely than a false
    positive here.
  - Refactored `_name_mentioned`'s inline regex into a reusable
    `_name_pattern` helper so this check can search for *where* a routine
    name occurs (not just whether it occurs), without duplicating the
    match rule.
  - New tests in `tests/test_validate.py`: accepts a forward reference the
    named chunk actually fulfils, flags one where the named chunk never
    mentions the routine, flags one naming a chunk that doesn't exist,
    ignores a single unchunked module doc (no chunk-file siblings to
    cross-check against), and ignores a concrete chunk number with no
    known routine name nearby to check the claim against.
  - `docs/guides/architecture.md`'s stage-4 section now documents this as
    a fourth advisory check, alongside `_deferred_reference_problems`.
  - Full suite green (756 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739, `mfdoc validate` exit 0)
    -- the bundled fixtures have no chunked module-doc set (none of their
    members are large enough to trigger chunking), so this check reports
    nothing against them; its behaviour is exercised entirely by the new
    unit tests' synthetic chunk-file fixtures.
- Fixed issue #129: `interface_matrix_brief`'s dispatch-branch scan only
  ever looked for the configured PF-key dispatch field
  (`dispatch_field`/`dispatch_field_pattern`), so a dialect or coding style
  that instead (or additionally) dispatches a screen's PF-key meanings
  through a central mode/panel/transaction-code-keyed block -- one that
  acts inline (sets a field, branches directly) rather than PERFORMing a
  distinct subroutine per value -- had no fact source at all: that whole
  class of PF-key-driven navigation was silently missing from the matrix.
  - Added `mode_field_from_options` (`conditions.py`), deliberately
    mirroring `dispatch_field_from_options` except for having *no* built-in
    default: unlike Natural's fixed `*PF-KEY` system variable, a mode/panel
    field's name is entirely application-chosen, so there's no equivalent
    convention to guess at -- a project opts in with its own
    `options.overview.mode_field_pattern` (new `OptionSpec` in
    `config_validate.py`, same shape as `dispatch_field_pattern`), and the
    mode/panel-dispatch half of the matrix is simply omitted (a documented
    gap, not a guessed pattern) until it does.
  - `interface_matrix_brief` (`brief.py`) now takes an optional `mode_field`
    param and, when supplied, reuses `structural.dispatch_edges_for_member`
    a second time per displaying module -- unchanged, since it was already
    generic over any field pattern -- keyed on `mode_field` instead of
    `dispatch_field`. Every row is tagged with a new `mechanism` column
    (`PF-key dispatch` vs. `mode/panel dispatch`) so a reader isn't left
    assuming both fact sources agree on the same set of actions per screen
    when they might not. `cmd_brief` (`cli.py`) wires
    `mode_field_from_options(cfg["options"])` through.
  - `templates/interface-matrix.md` and `SKILL.md`'s document-set section
    now describe the `mechanism` column and instruct carrying it into the
    written document as its own column rather than merging the two
    mechanisms into one undifferentiated "dispatch" fact.
    `docs/guides/architecture.md`'s brief-generation section and
    `README.md`'s command listing/sample config now mention the new
    `mode_field_pattern` option alongside `dispatch_field_pattern`.
  - New tests: `tests/test_conditions.py` (`mode_field_from_options` has no
    built-in default, honours a configured pattern),
    `tests/test_config_validate.py` (`mode_field_pattern` regex validation),
    `tests/test_interface_matrix_brief.py` (an inline mode/panel branch
    with no subroutine call of its own is surfaced and tagged
    `mode/panel dispatch` alongside an existing `PF-key dispatch` row when
    `mode_field` is supplied; omitted entirely, with no guessed field, when
    it isn't) -- all with invented field/member names, no real client data.
  - Full suite green (762 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739, `mfdoc validate` exit 0)
    -- the bundled fixtures configure neither dispatch field pattern, so
    this change reports nothing new against them; its behaviour is
    exercised entirely by the new unit tests' synthetic fixtures. No real
    Mantis/Supra fixture exercises the mode/panel mechanism in this change
    (documented gap, per CLAUDE.md's dialect-variant guidance) -- the
    derivation itself is dialect-neutral (any `rule_candidate`-populating
    dialect benefits), only a worked fixture is missing.

- Fixed issue #130: `mfdoc validate`/`mfdoc test-validate --docs <path>`
  walked *every* markdown file under `<path>`, with nothing in the
  directory structure itself saying which of several projects sharing a
  parent output/test directory a given file belonged to -- pointing
  `--docs` at that shared parent (rather than one project's own namespaced
  subtree, per `cli._project_namespace`) silently cross-checked a different
  project's generated docs against the currently loaded `--config`'s fact
  store, reported as ordinary "member is not in the index"/"not a known
  test_case scenario" failures indistinguishable from a real regression.
  - Added `_out_of_scope_sources`/`_partition_pipeline_docs` (`validate.py`):
    a member-scoped document's `sources` front matter (required on every
    narrative/generated-test document) is cross-checked against the
    currently loaded fact store's own `member` table before validation
    runs -- a document naming at least one source, but none of them present
    in this store, is skipped rather than validated against the wrong
    project's fact store. A document with no `sources` key, an empty list
    (`doc_type: register` documents, `interface-matrix.md`'s legitimately
    empty list), a malformed non-list value, or `doc_type: language-guide`
    (whose `sources` is descriptive placeholder text, not a member name --
    the one doc type where this front matter isn't member provenance)
    carries no signal either way and validates exactly as before.
  - Wired into both `validate_tree` and `validate_tests_tree` as a new
    `out_of_scope_documents` result key -- advisory only, never subtracted
    from `documents`/`documents_ok` and never affecting either command's
    exit code -- and printed by `cmd_validate`/`cmd_test_validate` (`cli.py`)
    the same way other advisory problem lists already are.
  - Dialect-neutral by construction: keyed off `sources`/`member`, tables
    every dialect's extractor populates the same way, so no dialect-specific
    variant is needed.
  - New tests: `tests/test_validate.py` (a cross-project module doc is
    skipped and reported separately rather than reporting invalid
    citations; a register doc with no `sources` and a doc with an empty
    `sources` list both still validate exactly as before) and
    `tests/test_test_validate.py` (a cross-project generated-test doc's
    scenario ref is skipped rather than reported as an invalid
    `test_case` scenario) -- all with invented project/member names, no
    real client data.
  - Full suite green (767 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739, `mfdoc validate` exit 0)
    -- the bundled fixtures are a single project, so nothing is newly
    flagged out of scope against them; the fix's behaviour is exercised by
    the new unit tests' synthetic two-project fixtures.
- Fixed issue #131: `mfdoc batch`'s per-chunk retry loop
  (`_generate_module_doc_from_brief`, shared by the plain single-call path
  and `_generate_module_doc_chunked`'s per-chunk calls) always regenerated
  an entire chunk from scratch on any validation failure, even when the
  only problem was a small number of uncited-and-unhedged assertive
  statements in a chunk that was otherwise already valid -- across two real
  regenerations this near-miss pattern accounted for 60-75% of the
  module-doc generation step's total token spend, since fixing 1-2
  sentences out of a 40-rule chunk cost the same full re-narration as a
  chunk that was genuinely broken.
  - Added `_is_near_miss_uncited` (`batch.py`): true only when
    `validate_doc` failed for exactly one reason -- `NEAR_MISS_MAX_UNCITED`
    (default 3) or fewer uncited assertive statements, nothing else wrong
    (no invalid citation, no front-matter problem, no reversed-condition
    hit). A chunk failing for any other reason, or with more uncited
    statements than that, is unaffected -- still goes straight to the
    existing full retry.
  - Added `build_uncited_patch_prompt` (`batch.py`): a far smaller
    follow-up prompt for the near-miss case -- the already-written document
    plus only the flagged sentences and the original fact brief (never the
    writing rules or template again, since the rest of the document is
    itself proof the model already followed them), asking for a citation
    drawn from the brief or an explicit hedge on just those sentences, with
    everything else asked to come back unchanged.
    `_generate_module_doc_from_brief` tries this once, immediately after a
    near-miss failure and before consuming one of `max_attempts`' full
    regenerations; if the patch attempt itself doesn't resolve validation,
    generation falls back to the existing full-chunk retry loop unchanged.
  - A failure that exhausts every attempt now also appends each flagged
    sentence's snippet (`validate_doc` truncates `uncited_assertions` to
    140 characters, so this isn't the full sentence) to
    `DocResult.problems` (prefixed `uncited (snippet):`), not just the
    summary count `validate_doc` already reported -- the issue's "at
    minimum" fallback ask, so a human can hand-patch immediately from the
    failure output instead of re-deriving which sentences were flagged
    from the document text.
  - Dialect-neutral by construction: keyed off `validate_doc`'s own
    `uncited_assertions`/`problems`, not any dialect-specific text --
    applies identically to Natural and Mantis/Supra chunks. Not mirrored
    into `testbatch.py`: `validate_test_doc`'s checks don't include an
    uncited-assertive-statement check (generated tests are field-inventory
    prose, not claim-per-sentence narrative), so the near-miss condition
    this fix keys on never arises there.
  - New tests in `tests/test_batch.py`: `_is_near_miss_uncited`'s boundary
    (exactly `NEAR_MISS_MAX_UNCITED` uncited statements alone vs. one more,
    vs. one alongside an unrelated problem), a near-miss response getting
    the targeted patch prompt (not the full writing-rules/template retry)
    and succeeding without consuming a full-retry attempt, a patch attempt
    that itself fails falling back to the existing full retry, and a
    genuinely broken response (more uncited statements than the threshold
    allows) still going straight to a full retry as before.
  - Full suite green (780 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739, `mfdoc validate` exit 0)
    -- the bundled fixtures' pre-generated docs already validate clean, so
    the near-miss path isn't exercised by the fixture run itself, only by
    the new unit tests' synthetic near-miss/broken responses.
- Fixed issue #141: generated modules' "Inputs"/"Data used" tables listed
  every field uniformly, with no signal telling a screen/MAP-bound field, a
  plain program (working-storage) variable, and a DB/VIEW field apart --
  an SME review flagged a case where a program variable was built by
  slicing a value with no indication that value was actually a screen
  array field, which made the construction unreadable on first pass.
  - `variable.scope` already distinguished these for Mantis (`screen`,
    `mantis_local`/`local`/`global`/`independent`, `view`) -- the gap was
    entirely in `brief.py`, which only ever rendered `parameter`/`entry`
    (as "Interface (parameters)") and `view` (as "Data views declared"),
    never surfacing any other scope at all. Added a new "Program variables
    and screen/MAP fields" section (`module_brief`) covering every other
    scope, each row tagged with an explicit kind (`screen field`, `program
    variable`, `program variable (global)`) via a new `_variable_kind`
    helper.
  - Natural has no equivalent `scope='screen'` value -- a Natural program's
    screen fields are declared as ordinary `DEFINE DATA LOCAL` variables,
    bound to a map only by naming convention against a `USING MAP` target.
    Rather than a new extraction pass, added `_natural_screen_field_names`
    (`brief.py`): a rendering-time join of facts already recorded --
    `natural._match_interaction`'s `call_edge` (`INCLUDE`/`USING MAP`) row
    against the target map member's own `MAP_FIELD` `interaction` rows
    (`natural._match_map_body`) -- to resolve which of a program's local
    variables are actually screen-bound, so those render as `screen field
    (bound via MAP)` instead of an indistinguishable `program variable`.
  - `templates/module.md`'s Inputs/Data used sections and
    `reference/writing-rules.md`'s prose-failures list now spell out the
    contract explicitly: carry the brief's kind label into the Source
    column rather than treating every field the same.
  - No real Mantis/Supra or Natural fixture under `examples/inputs/`
    exercises this (the bundled fixtures don't cross-reference a `USING
    MAP` target against a real map member) -- covered instead by two new
    `tests/test_brief.py` cases building minimal in-memory fact stores.
    Documented gap: `mantis_shared` is a schema-documented scope value
    with no current emitter in `mantis.py`, defensively mapped to
    `program variable (global)` here but never exercised by a test, since
    nothing produces it yet.
  - Full suite green (782 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739, `mfdoc validate` exit 0).
- Follow-up to issue #141 (PR #146), from automated review comments on the
  new "Program variables and screen/MAP fields" section: Natural's
  `DEFINE DATA <scope> USING <LDA/PDA/GDA>` records a synthetic `variable`
  row named `USING <NAME>` (alongside its own `call_edge`/`INCLUDE` row) so
  the include is visible in the fact store -- that's a data-area include,
  not a program variable, and the new section was rendering it mislabeled
  as one. `module_brief` now splits those rows out into their own "Data
  areas included" subsection instead. Also corrected `_variable_kind`'s
  docstring (it never actually sees a `scope='view'` row -- those are
  filtered out and handled by the pre-existing "Data views declared"
  section before `_variable_kind` is called) and reworded
  `templates/module.md`'s Inputs section paragraph, which had claimed the
  new section itself tags a `DB view field` kind -- it doesn't; that label
  only ever appears via the separate "Data views declared" section.
  - New `tests/test_brief.py` case covering a Natural member with a
    `DEFINE DATA ... USING` data-area include, asserting it's absent from
    the "Program variables and screen/MAP fields" section and instead
    appears under "Data areas included".
  - Full suite green (783 passed, 2 skipped); bundled fixture pipeline
    clean (71/71 docs, 0 invalid citations of 739, `mfdoc validate` exit 0).
- Fixed issue #142: `mfdoc test-gen`/`mfdoc test-batch` defaulted
  `options.testgen.out_dir` to the bare literal `"tests_generated"`
  regardless of where a `project.yml`'s top-level `docs_root` put that
  project's narrative docs, so a multi-project workspace ended up with two
  disconnected top-level trees -- each project's docs under its own
  `docs_root`, but every project's generated tests comingled under one
  shared top-level `tests_generated/`.
  - Added `_testgen_default_out_dir` (`cli.py`): when `docs_root` is set,
    defaults `out_dir` to `<docs_root>/tests`; when it's unset, keeps
    today's bare `"tests_generated"` literal unchanged, so a config without
    `docs_root` sees no behavior change. Wired into both `cmd_test_gen` and
    `cmd_test_batch` in place of the hardcoded `"tests_generated"` fallback
    they each had; an explicit `options.testgen.out_dir` (or `--out`) still
    overrides this exactly as before -- `_project_namespace` keeps applying
    on top, unchanged.
  - New tests in `tests/test_test_batch.py` cover all three cases: `docs_root`
    set + no explicit `out_dir`/`--out` (new nested default), `docs_root`
    unset + no explicit `out_dir`/`--out` (old top-level literal preserved),
    and an explicit `options.testgen.out_dir` (always wins regardless of
    `docs_root`, matching the pre-existing `--out`-always-wins coverage).
  - Full suite green (789 passed, 2 skipped); bundled fixture pipeline clean
    (71/71 docs, 0 invalid citations of 739, `mfdoc validate` exit 0); also
    manually confirmed `mfdoc test-gen` against the bundled fixtures with
    `docs_root` set and `out_dir` unset writes to
    `docs/functional/tests/<namespace>/...` rather than a top-level
    `tests_generated/`.

**Progress (2026-09-07c):**
- Closed out the two items 2026-09-07b left for a future pass:
  - `DEFINE WINDOW` and its `SIZE`/`BASE`/`FRAMED`/`FORMAT` attribute lines
    are now recognised as a presentation no-op (`RE_DEFINE_WINDOW`,
    `CONTINUATION_LEAD` extended), the same way `SET CONTROL`/`SET KEY`
    already were. Also: a continuation line already folded into a
    preceding statement was still raising its own redundant
    `unparsed_line` gap on its second visit (both `natural.py` and
    `mantis.py`) — now suppressed via a `folded_lines` set, since the
    content wasn't lost. (PR #120.)
  - Multi-line `WRITE`/`DISPLAY`/`PRINT`/`INPUT`/`REINPUT` operand lists
    that wrap with no column-spec token and no leading keyword: a
    continuation line opening with a quoted literal
    (`CONTINUATION_LEAD_QUOTE`, unconditional — no statement verb ever
    starts with a bare literal), a bare `/`/`//` with nothing else on it
    (`CONTINUATION_LEAD_SLASH`), or a bare field reference
    (`CONTINUATION_LEAD_FIELD`, restricted to a leading `#` and excluding
    `:=` so a genuine bare assignment isn't folded in as another operand)
    now fold the same way the column-spec case already did. New fixture
    `MMP9550.nsp` plus `test_write_operand_continuations.py` (including a
    false-positive guard: a `#FIELD := ...` assignment right after a
    `WRITE` must not be swallowed by it).
  - Full suite green (715 passed, 2 skipped); bundled fixture pipeline
    clean (33/33 docs, 0 invalid citations of 548).

**Progress (2026-09-07d):**
- Re-ran `mfdoc calibrate` against two engagement codebases' Natural/Mantis
  corpora (no client content in this repo -- findings generalised into
  invented fixtures/tests, same as 2026-09-07b) after 2026-09-07c's fixes
  landed. The Mantis corpus is now fully clean -- zero `unparsed_line`
  gaps. The Natural corpus dropped from ~30 gap rows (several at 3-8
  occurrences each) to ~26 rows, all now singleton occurrences: the
  quote/slash-lead fixes eliminated every occurrence of those shapes; the
  remaining ones are all bare field-reference lines (e.g. `#IDN(#I)`,
  `#STORE-*(#I)`) that turned out to precede a `COMPRESS`/`SEPARATE`
  statement's `INTO` clause, not a `WRITE` -- those two verbs build the
  same kind of operand list (see `RE_COMPUTE`) but weren't in the fold
  loop's WRITE-family scope. Added `RE_COMPRESS_SEPARATE` to that scope
  (`natural.py`); new fixture `MMP9560.nsp` (sibling of `MMP9800.nsp`'s
  existing COMPRESS+INTO fixture, which only exercised a continuation line
  that *already* carries `INTO` -- this one exercises a bare operand line
  *before* it) plus `test_compress_separate_operand_continuations.py`.
- The remaining single-occurrence gaps in that Natural corpus are left as
  genuine gap-register questions -- each looks like a distinct,
  lower-frequency construct (a different `DEFINE` form, a bare DDM/view
  field-list line, an unfamiliar report-writer page marker) that would
  need a real client source sample to generalise safely, not something to
  guess a fix for.
- Full suite green (717 passed, 2 skipped); bundled fixture pipeline clean
  (33/33 docs, 0 invalid citations of 548).

**Progress (2026-09-07b):**
- Natural dialect calibration pass, driven by `mfdoc calibrate --dialect natural`
  against two engagement codebases (no client content in this repo — findings
  generalised into invented fixtures/tests). Fixes in `dialects/natural.py`:
  - `RE_UPDATE`/`RE_DELETE` required `\s+` right after the verb, so the very
    common no-target form (`UPDATE`/`DELETE` alone, acting on whatever record
    the enclosing FIND/READ loop currently holds) and the no-space loop-label
    form (`UPDATE(R1.)`) both silently fell through as `unparsed_line` gaps
    instead of reaching the existing loop-label resolution path. Relaxed to
    `\s*` behind a `(?=[\s(]|$)` lookahead so a longer identifier starting
    with the same letters (`UPDATED-FLAG`) still can't false-match.
  - Same shape of bug in `RE_FIND`/`RE_READ` for the no-space occurrence-count
    form (`FIND(1) VIEW ...`, `READ(1) VIEW ...`).
  - `RE_SET_CONTROL` only matched `SET CONTROL`, not `SET KEY` (PF-key
    activation) — same presentation-not-business-decision no-op, extended
    the pattern to cover both.
  - Added `RE_REJECT` (`REJECT IF <cond>`, a real loop-filtering decision)
    as a recognised `rule_candidate` construct in `_match_rules`, alongside
    `ESCAPE`.
  - `CONTINUATION_LEAD` didn't include `SORTED`/`WHERE`, so a `FIND ... WITH`
    condition wrapped onto its own `SORTED BY`/`WHERE` line lost that clause
    from the folded condition text instead of being preserved the way
    `AND`/`OR`/`WITH`/`INTO` already are.
  - `RE_GENERIC_LABEL` only matched labels starting with a letter; some
    export/conversion tooling renumbers statement labels with a leading
    `#`/`&` (e.g. `##L100.`), which was previously an unconditional
    `unparsed_line` gap. Extended the label-start character class, and added
    `RE_BARE_LABEL` for a label alone on its own line (nothing after it) so
    that doesn't gap either.
  - New tests in `test_natural_rules.py` cover all of the above against
    invented fixtures. Full suite green (710 passed, 2 skipped); bundled
    fixture pipeline clean (64/64 docs, 0 invalid citations).
  - Left for a future pass, evidenced but lower-frequency/higher-risk to
    generalise safely: `DEFINE WINDOW` and its attribute lines
    (`SIZE`/`BASE`/`FRAMED`/`FORMAT`), and multi-line `WRITE`/`DISPLAY`
    operand lists that wrap with no leading keyword at all (only the
    report-writer column-position case is currently folded).

**Progress (2026-09-07):**
- Implemented issue #95 (docs + example for `sme-notes.md`, closing out the
  SME memory-file epic, #96, after #93/#94): `README.md` gets a new "SME
  notes (optional)" section explaining the schema, that it's optional, and
  that it's advisory-only, never a citable source; `CLAUDE.md`'s "Brief
  generation" bullet now mentions `sme_notes.py`/`_sme_notes_section`;
  `SKILL.md`'s interactive-writing step now tells a session to check for
  `options.sme_notes` and read it directly when writing `system-overview.md`/
  `processes/*.md`/`gap-register.md` from the system brief, since #94's
  wiring only covers `module_brief`/`entity_brief`/`executive_brief`/
  `test_case_brief`, not `system_brief`/`interface_matrix_brief`.
  `project.yml`'s `options.sme_notes` comment was already added by #93 --
  verified complete, no change needed. Added a worked example,
  `examples/sme-notes.md` (general section plus two member-scoped
  sections, all invented content against this repo's own MOM/MILLPROD
  fixture), linked from the new README section. Since `mfdoc validate`
  walks every `.md` file under a `--docs` root expecting pipeline-output
  front matter, the new example under `examples/` needed
  `validate._is_pipeline_doc` extended (alongside its existing `README.md`
  exception) to also skip `sme-notes.md`, or the bundled-fixture smoke test
  (`mfdoc validate --docs examples`) would fail on it -- covered by a new
  `test_validate_tree_skips_sme_notes_files` test, plus a
  `test_worked_example_parses_with_expected_sections` test in
  `test_sme_notes.py` asserting the checked-in example actually parses into
  the sections it claims to demonstrate.
- Implemented issue #94: wired issue #93's `sme_notes.py` parser into the
  actual brief-then-prompt path every document type already uses.
  `brief.module_brief`/`entity_brief`/`executive_brief` and
  `testplan.test_case_brief`/`test_case_brief_chunk` now take an optional
  `sme_notes` (the `Notes` dict `sme_notes.load()`/`parse()` returns);
  when a general and/or member/entity-scoped note matches, a new
  `brief._sme_notes_section` helper appends a
  "## SME notes (human-provided context, not verified against source)"
  section at the very *end* of the brief -- deliberately last, in plain
  prose with its own explanatory sentence, never `[[MEMBER:LINE]]`-cited
  like everything above it, so it can't be mistaken for cited fact-store
  content. The same `Redactor` already threaded through these functions is
  applied to the note text before inclusion, same as every other section.
  Wired end-to-end: `cmd_brief`/`cmd_batch`/`cmd_test_gen`/`cmd_test_batch`
  in `cli.py` all now call `sme_notes.load(cfg, base)` and pass it through
  `batch.run_batch`/`generate_module_doc`/`_generate_module_doc_chunked`
  and `testbatch.run_test_batch`/`generate_member_test_doc`/
  `_generate_member_test_doc_chunked` (mirroring the existing `lexicon`
  plumbing throughout) -- including folding it into both modules'
  `_corpus_signature` fingerprint, so an sme-notes.md edit with no source
  change still invalidates `mfdoc batch`/`mfdoc test-batch`'s resumable
  state instead of being silently skipped by the corpus-level fast path.
  Added `OptionSpec("options.sme_notes", ...)` to `config_validate.py`
  (missed by #93). `reference/writing-rules.md` gets a new "SME notes"
  rule (linked from `reference/test-writing-rules.md`): notes may inform
  interpretation/emphasis, but every business-rule claim still needs its
  normal citation -- SME notes are never themselves a citable source, and
  a note that contradicts the cited facts must lose to the facts (raised
  as a gap-register question instead). New `tests/test_sme_notes_briefs.py`
  covers all four brief functions (section present on match, absent
  otherwise, redaction applied, no citation marker inside the section) plus
  an end-to-end-ish check that `batch.build_prompt` carries the section
  through unchanged. Issue #95 (docs/example for `sme-notes.md`) is the
  follow-on, now that this has a stable, merged foundation.
- Added a regression test (`test_batch_absorbs_a_transient_caller_retry_while_another_member_succeeds`
  in `tests/test_batch.py`) exercising issue #79's internal retry and issue
  #78's per-future isolation *together* in one `run_batch` pass, which
  `tests/test_batch.py` didn't yet cover -- the existing isolation test
  (`FlakyCaller`) only covers a caller exception that *propagates out* to
  run_batch across two separate `run_batch` calls, not a transient failure
  a caller absorbs internally (as `AnthropicCaller`/`VertexCaller` do via
  `call_with_retry`) while a different member succeeds normally in the
  same pool. The new `RetryMaskedCaller` test double calls the real
  `mfdoc.retry.call_with_retry` directly (rather than hand-rolling an
  equivalent retry loop, per review feedback -- avoids the test drifting
  from the production retry helper's actual behavior over time) and
  asserts: no failure recorded for the retried member, its batch-level
  `attempts` stays 1 (the retry is invisible to run_batch), the other
  member's result is untouched, and no second `run_batch` call is needed
  to pick up the retried member.
  Issue #81 was investigated and closed without a code change: `VertexCaller`
  narrows its lock to wrap only the single `messages.create()` call inside
  each retry attempt (acquired and released fresh per attempt, never held
  across `call_with_retry`'s backoff sleep) -- the narrowest scope that's
  still safe given `AnthropicVertex`'s in-place credential refresh. See the
  closing comment on #81 for the full analysis.
- Implemented issues #87/#88/#89, giving `testbatch.py`'s harness the same
  checkpoint/retry/reuse discipline `batch.py` already has for module docs:
  (#87) every `caller()` call site now catches an exception instead of
  letting it propagate and crash the whole run with zero state saved, and
  state is checkpointed after every finished member/chunk (a new
  `_checkpoint` helper) rather than only once at the end; (#88) ported
  `batch._generate_module_doc_chunked`'s `prior_chunks` content-hash
  skip/reuse mechanism into `testbatch._generate_member_test_doc_chunked`,
  so a retry only regenerates the chunk(s) whose own brief actually
  changed (or that failed last run -- reuse requires the prior record's
  own `ok` to have been `True`, so a previously-failed chunk always gets a
  fresh model call rather than re-validating the same broken content
  forever); (#89) `mfdoc test-batch`/`test-gen`'s default resume-state
  file and output directory are now namespaced per project config (`cli.
  _project_namespace`, keyed by `system`, else `project`, else "default"),
  so two `project.yml` files sharing a working directory no longer
  silently share -- and a `rm -f` on one no longer clobbers -- the other's
  resume-state/output tree. An explicit `--out`/`--state` is still used
  exactly as given, matching how `index_db` itself is always an explicit,
  project-specific choice.
- Follow-up to issue #105 (PR #107 review): `flag_density_outliers`'s
  `avg_depth` comparison left a chunk unflagged whenever the run's other
  chunks' median depth was 0, on the same "a multiple of 0 is meaningless"
  reasoning used for `lines_per_item` -- but `rule_candidate.depth` is
  0-based (top-level depth is often 0), so a run whose other chunks are
  all flat never flagged a genuinely nested chunk under that rule, however
  deep it went. A 0 depth median with `avg_depth > 0` is now flagged
  directly (message names the flat baseline explicitly instead of a
  division), while a 0 `lines_per_item` median is still left unflagged (a
  0 span shouldn't occur in practice, unlike a 0 depth).
- Implemented issue #105: chunk-boundary logic (`brief.
  routine_aware_chunk_ranges`, shared by `batch.py`/`testbatch.py`) preserves
  routine boundaries while packing chunks by rule/scenario count, but that
  count is blind to how content-dense a chunk's *source* actually is -- a chunk could match its siblings' rule
  count while its source was far harder to narrate (more lines, deeper
  nesting per rule), and the only symptom was repeated retry failures on
  that one chunk. Added `brief.chunk_density_metrics`/
  `flag_density_outliers`/`format_density_note`: a cheap lines-per-rule and
  average-nesting-depth estimate per chunk from facts already at hand
  (`rule_candidate.line_no`/`.depth`), flagging a chunk well above the
  run's own median for either metric. Wired into both `_generate_module_
  doc_chunked` (batch.py) and `_generate_member_test_doc_chunked`
  (testbatch.py, lines-per-rule only -- no depth is joined onto test_case
  rows): a failed chunk's own reported problem now carries a `density: ...
  -- OUTLIER (...)` note when it's a density outlier, so that distinction
  is visible immediately rather than requiring a human to notice the
  pattern across several failed runs. Chose surfacing the estimate in
  diagnostics (approach (b) from the issue) over auto-splitting a dense
  chunk further (approach (a)): a synthetic fixture can demonstrate the
  estimate correctly flags a rule-dense chunk, but not that a smaller
  chunk boundary actually improves a real model's success rate on it --
  that would need validating against real (or much more elaborate
  synthetic) failure data this change doesn't have.
- Implemented issue #79: `AnthropicCaller` and `VertexCaller` now retry
  transient errors (`RateLimitError`, `APIConnectionError`,
  `InternalServerError`) with bounded exponential backoff and jitter, via a
  new dependency-free `retry.call_with_retry()` shared by both. Non-retryable
  errors (bad request, auth, malformed prompt) still propagate immediately —
  this only defers a bounded number of transient-looking failures, never
  swallows a real one. `VertexCaller`'s existing lock is now held only
  around the actual `messages.create()` call, not around backoff's sleep
  between retries, so a retry waiting out a rate limit doesn't also block
  every other worker's access to the shared client. Combined with #78's
  per-member isolation, a transient API error no longer forces a whole
  member to fail and be re-run from scratch. `ClaudeCLICaller` is
  intentionally out of scope here -- per issue #79's own text it "already
  has better timeout handling and can serve as a model for how the others
  should behave," and its failure mode (a subprocess timing out or exiting
  non-zero) isn't the same transient-network-error shape this retry helper
  targets; it already turns a hung/failed `claude -p` call into a clear
  `RuntimeError` on its own. (A retry addition was briefly tried and
  reverted for exactly this reason -- see this branch's history.)
- Implemented issue #80: `AnthropicCaller` and `VertexCaller` now take a
  configurable `timeout` (seconds), defaulting to 600 -- the same
  `DEFAULT_TIMEOUT_S` value and None-means-default pattern
  `claude_cli_caller.ClaudeCLICaller` already used, so a hung request
  surfaces as a clear timeout instead of blocking a worker thread
  indefinitely. A new `--api-timeout` CLI flag (mirroring the existing
  `--claude-code-timeout`) wires it through `classify-rules`/
  `test-overlay-draft`/`test-batch`/`batch` for `--provider anthropic`
  and `--provider vertex`.
- Implemented issue #91: a new document type, `interface-matrix`, for the
  screen-and-key interface matrix a client review asked for (mode x panel
  x map x PF-label x routine x outcome). It follows the interactive
  brief/template pattern (`brief.interface_matrix_brief()`,
  `templates/interface-matrix.md`, `mfdoc brief --interface-matrix`) like
  `system_brief`/`executive_brief`, not `batch.py`'s per-member path, so
  the brief can gather each screen's display reference(s) and the
  PF-key/dispatch branches found in the same modules that display it.
  Reuses `structural.dispatch_edges_for_member` (the same derivation
  `mfdoc dispatch-map` uses) for the PF-key branches, joined against
  `interaction.target` (dialect-general: Natural's `INPUT USING MAP`,
  Mantis's `CONVERSE`/`SHOW`) for which module(s) display each screen.
  Data-model decisions, since the fact store has no table shaped like the
  target document: "mode(s) reachable from" is every module whose own
  source displays the screen (no dedicated mode field exists in the fact
  store); PF-key labels are handed over as candidate literal text found on
  the screen (Natural `MAP_TEXT` interaction rows / Mantis `HEADING`-format
  `entity_field` rows on the `mantis_map` entity) rather than pre-matched
  to a trigger value; "outcome" (exit/navigate/error) is deliberately left
  for the narrative pass to characterise from the cited routine/call kind,
  not classified deterministically, since neither a PF-key's number nor a
  routine's name reliably implies its outcome. The bundled sample codebase
  has no member that both displays a screen and dispatches on a PF-key/
  configured field for it, so `examples/outputs/docs/interface-matrix.md`
  demonstrates the brief's honest "nothing to report" path rather than
  populated rows; `tests/test_interface_matrix_brief.py` covers populated
  rows with synthetic facts, for both Natural's `*PF-KEY` and a
  Mantis-style configured dispatch field.
- Implemented issue #86: centralized `options.*` config validation.
  `config_validate.py` is a new, declarative validation pass (`OPTION_SPECS`,
  a list of `OptionSpec(path, types, check=...)` entries, not an if/elif
  chain) covering every `options.*` leaf that was previously either
  validated ad hoc at point of use (`batch._resolve_max_rules_per_call`,
  `testbatch._resolve_max_scenarios_per_call`, `structural.py`'s
  `cluster_by`/`direction`/`metric` checks) or not type-checked at all
  (`options.redact.patterns` regex validity, `options.quality_gates`
  rate/count ranges). `cli.load_config` calls `raise_if_invalid` on every
  resolved config before returning it -- since every `cmd_*` calls
  `load_config` first, this is a single upfront check at CLI startup for
  every command, and `cli.main` catches the resulting `ConfigError` and
  exits 2 with a readable, multi-problem message instead of an unhandled
  traceback partway through a run. The existing point-of-use checks are
  left in place as a harmless second line of defence for callers that
  build a config dict without going through `load_config` (e.g. tests
  calling `batch.run_batch` directly).
- Implemented issue #85: coverage metrics now persist across runs for trend
  visibility. `db.coverage_history`/`db.record_coverage_history` (new
  `coverage_history` table, append-only, one row per `mfdoc coverage`/
  `mfdoc gate` invocation, `metrics_json` carrying the whole
  `graph.coverage()` dict so a future metric needs no schema change) —
  chosen over a bare JSONL file so it lives alongside the rest of a
  project's per-engagement state in the same `.mfdoc/index.db`, with no new
  runtime dependency. `mfdoc coverage --history` reads the trend back as a
  plain table (view-only — it does not itself append a row, so checking
  the trend repeatedly can't pollute it).
- Fixed issue #90: the reversed-condition checker's proximity heuristic
  (`conditions.prose_polarity`) misattributed a hedge word ("at least",
  "no more than", ...) to an unrelated outcome-field comparison sitting
  next to it in a compound `AND`/`OR` condition's narration, rather than to
  the clause it actually modifies. `_clip_at_clause_boundary` now clips
  each side of the literal's search window at the nearest `AND`/`OR`
  conjunction before scanning for a hedge/negation marker, so a marker on
  the far side of a conjunction from the literal (belonging to a different
  clause's operand) no longer bleeds into this literal's reading. Covered
  by new unit tests in `tests/test_conditions.py` (the false-positive
  case, plus its `OR` variant and a same-clause control) and an
  end-to-end pair in `tests/test_validate.py` (a compound-`AND` condition
  narrated correctly must not be flagged; a hedge word genuinely reversed
  on the same clause still must be). No dialect-specific change needed --
  the fix is in the dialect-neutral prose-polarity helper `validate.py`
  already calls for every dialect.
- Implemented issue #93: `src/mfdoc/sme_notes.py`, a standalone parser for
  an optional `sme-notes.md` file (path configured via a new
  `options.sme_notes` key, documented with a one-line comment in
  `project.yml` next to the other `options.*` keys). Schema is
  deliberately semi-structured: text before the first `##` heading (or an
  explicit `## General` heading) is general context applied to every
  member/entity; each `## <name>` heading afterwards scopes its body to
  just that member/entity, matched case-insensitively. `parse()` returns
  `{None: general_text, "module-alpha": ..., ...}`; `notes_for(notes, member_name)`
  combines the general section with the member's own section, general
  first. A missing or empty file parses to `{}` with no error, keeping the
  whole thing optional. `load(cfg, base)` is the convenience entry point
  that reads `options.sme_notes` off an already-`cli.load_config`-loaded
  project config. This PR is deliberately scoped to the parser only --
  wiring `notes_for()` into `brief.py`'s actual brief-building call sites
  is issue #94's job, kept separate so #94 has a stable, merged foundation
  to build on.
- Implemented issue #104 (two gate-gap classes miscalibrated as harder than
  they are): (1) `mantis.py`'s `'`-marked continuation fold recognised only
  a *leading* marker (continuation line starts with `'`); added the
  complementary *trailing* shape, where the marker sits at the end of the
  line being wrapped instead (e.g. a long quoted assignment split as
  `DESC="text so far '` / `more text"`) — new `_has_open_trailing_marker`
  helper, distinguishing a genuine trailing marker from an ordinary line
  that just happens to end with a real, closed literal. (2) new
  `graph.resolve_interface_literal_calls`, run from `resolve()` before the
  general callee_id lookup: a `CALL` on a Mantis `INTERFACE handle(...)`
  bound to a literal at its own declaration (`mantis.py` now also records
  that binding as a `variable` row, `scope='mantis_interface'`) is
  reclassified to the literal target instead of being left as a
  `dynamic_target` gap — that gap kind is reserved for targets genuinely
  not determinable from source, not ones the extraction-time check just
  hadn't looked up yet. Neither fixture set exercises either shape yet, so
  both are covered by new isolated unit tests only (`tests/
  test_mantis_rules.py`, new `tests/test_dynamic_call_resolution.py`); the
  bundled fixture pipeline's gap/coverage counts are unchanged.
- Implemented issue #83 (partial, scoped to the batch/test-batch pipeline
  per the issue's own suggested starting point): standard `logging` in
  place of ad hoc `print()` for the genuinely diagnostic/progress output of
  a long `mfdoc batch`/`mfdoc test-batch` run -- a resumed member/chunk
  skip, a chunk starting/completing, a validation-failure retry, a failed
  model call, and (in `retry.call_with_retry`, shared by `AnthropicCaller`/
  `VertexCaller`) a transient-error backoff being retried, previously
  invisible entirely. New top-level `mfdoc --verbose`/`-v` (DEBUG level)
  and `--log-file PATH` (also write to a file, in addition to stderr) flags
  in `cli.py`, given before the subcommand, configure the root logger once
  in `main()` via a new `_configure_logging()`; every module's own logger
  (`mfdoc.batch`, `mfdoc.testbatch`, `mfdoc.retry`) just calls
  `logging.getLogger(__name__)`-equivalent and propagates up to it, no
  per-module wiring needed. Every `cmd_*` function's actual CLI output
  (coverage numbers, gate pass/fail, the batch/test-batch OK/FAIL/SKIP
  table and cost summary, calibrate/coverage-history reports, ...) is
  deliberately untouched -- still `print()`, since a user piping/scripting
  against it needs it to stay real stdout, not diagnostic logging gated
  behind `--verbose`. The other ~90 `print()` call sites across `cli.py`
  (every other subcommand's own report/summary output) are left as-is for
  the same reason and are explicitly out of scope for this PR; issue #83
  itself says this can be done incrementally per-module. Covered by new
  `tests/test_logging.py` (`caplog` on `call_with_retry`'s retry-warning,
  `run_batch`'s resumed-skip/validation-retry/model-call-failure logging,
  `run_test_batch`'s equivalents, `_configure_logging`'s level/`--log-file`
  behavior, and an end-to-end `cli.main(["--verbose", "--log-file", ...])`
  check).
- Implemented issue #84: `DocResult` and `BatchSummary` now track per-call
  wall-clock duration and transient-error retry counts, so a multi-hundred-
  module `mfdoc batch` run can tell "is this run stuck or just slow" and
  which members needed retries. `retry.call_with_retry` (#79) gained an
  optional `on_retry(attempt, exc)` callback, fired once per retry actually
  taken -- its own return value and every existing caller are unaffected;
  `AnthropicCaller`/`VertexCaller` use it to count each call's own retries
  into a local variable (never a shared instance attribute, so concurrent
  callers under `run_batch`'s `ThreadPoolExecutor` can't race each other's
  counts) and set it on the `ModelResponse` they return (`.retries`, default
  0 for any caller -- `fake-echo`, `ClaudeCLICaller` -- that doesn't retry
  at all). `batch.py`'s new `_timed_call()` wraps every model call
  (single-call, pooled, per-chunk, and the whole-module narrative-
  reconciliation call) with `time.perf_counter()` timing, folded into
  `DocResult.duration_s`/`.retries` (0.0/0 for a skipped or caller-exception
  member -- no call to time) and summed onto `BatchSummary.total_duration_s`/
  `.total_retries`; `cmd_batch` prints both per member and as a run-wide
  average/total alongside tokens and cost.

**Progress (2026-09-06):**
- Implemented issue #64: an eighth document type, `language-guide`, that
  reports which Natural/Mantis/Supra constructs a codebase actually uses —
  Mantis/Supra especially have no public documentation, and this falls
  entirely out of facts already in the fact store, no dialect-scanner
  change required. `graph.language_profile(conn, dialect)` groups seven
  already-populated columns (`variable.format`, `rule_candidate.construct`,
  `data_access.verb`, `entity_link.link_kind`, `interaction.kind`,
  `transaction_marker.marker`, `call_edge.call_kind`) by keyword, with a
  count and one cited example each. `graph.unparsed_line_shapes` was
  extracted out of `cmd_calibrate` (pure refactor, output unchanged) so
  the language-guide appendix and `mfdoc calibrate` share one
  implementation instead of two. Basic tier: `mfdoc lang-guide --config
  project.yml --dialect <dialect> --out <path>` (`structural.language_guide`, `doc_type:
  register`, no model call). Narrative tier: `templates/language-guide.md`,
  written interactively per `SKILL.md`'s updated suggested document set,
  taking the basic tier's output as its cited fact source — no new
  `brief.py` function needed, since the basic tier already is the
  complete fact summary. Design spec and implementation plan at
  `docs/superpowers/specs/2026-09-06-language-guide-doctype-design.md` and
  `docs/superpowers/plans/2026-09-06-language-guide-doctype.md`. No
  `validate.py` change was needed — confirmed with tests, not just
  asserted, since `doc_type: register` and the "any other doc_type" branch
  both already covered the new shapes.
- Done: chunk file names (`_generate_module_doc_chunked` in `batch.py`,
  `testbatch.py`'s test-generation counterpart) now zero-pad the chunk
  index to the width of the member's total chunk count, so a 17-chunk
  member writes `chunk01.md`..`chunk17.md` instead of `chunk1.md`..
  `chunk17.md` -- the latter sorts `chunk1, chunk10, ..., chunk17, chunk2,
  ...` in a plain lexicographic directory listing. Internal bookkeeping
  (resume-state dict keys, `chunk_map` values, prose like "chunk 3 of 16")
  stays unpadded/numeric; only the on-disk filename changed. Issue #72.
- Done: connectivity-aware call-graph splitting and LR-by-default layout
  (#68). `mfdoc call-graph` now defaults to `graph LR` (matching
  `data-flow.md`'s existing convention; still overridable back to `TD` via
  `options.overview.diagrams.direction`), and `structural.call_graph_diagram`
  now computes the call graph's connected components
  (`graph.connected_components`, pure union-find over `call_edge`, no model
  call) and renders one diagram per independent component instead of
  applying `max_nodes_inline` to the combined node count across the whole
  graph. A component still over threshold falls back to today's
  `cluster_by` collapse-then-per-cluster behaviour, scoped to just that
  component. The repo's own bundled `examples/` fixtures already split into
  six components under this logic — previously all silently flattened into
  one diagram. See `docs/superpowers/specs/2026-09-06-call-graph-lr-components-design.md`
  for the naming convention chosen for per-component files and the
  deliberate decision not to let a shared unresolved-callee name bridge two
  otherwise-disconnected components.

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
  `docs/guides/architecture.md` updated with both behaviours.
- 2026-08-05: done: Phase 6 EBCDIC-fixtures sub-item (last of #9). New
  `examples/fixtures/natural/encoding/MMP0200.{cp037,cp500}.nsp` --
  `MMP0200.nsp` re-encoded with Python's own codecs, chosen because it
  already contains `#` (one of the characters `reference/natural-adabas.md`
  flags as moving between EBCDIC code pages). Live in a subdirectory the
  natural source spec's non-recursive `*.nsp` glob never reaches, so they
  don't touch project.yml or the coverage snapshot -- exercised only by
  `tests/test_ebcdic_fixtures.py`, standalone. Found and documented a real,
  pre-existing sniffing limitation along the way rather than papering over
  it: `sniff_encoding` cannot distinguish cp037 from cp500 for content with
  none of the differentiating characters, since cp037 is tried first in
  `EBCDIC_CODEPAGES` and also decodes cp500-encoded bytes of this shape
  just fine -- confirmed a genuinely-cp500 fixture auto-detects as cp037.
  Not a bug to fix here (the heuristic's own doc comment already says
  "detection is heuristic; the config can force an encoding" and
  `reference/natural-adabas.md`'s Traps section already told users to pin
  `encoding:` when the sniffer gets it wrong) -- the test asserts what
  actually matters (EBCDIC bytes are recognised as *some* EBCDIC codepage,
  never mis-detected as latin-1/utf-8) and separately proves the forced-
  encoding path round-trips correctly for both codepages specifically,
  plus a full-pipeline test confirming cp037- and cp500-encoded sources
  produce identical `source_line` rows to the UTF-8 original.
  `reference/natural-adabas.md`'s EBCDIC trap entry cross-references the
  new fixtures and states the limitation explicitly. **Phase 6 / issue #9
  is now fully closed** (indexes, incremental ingest, EBCDIC fixtures, and
  the synthetic scale fixture that proved the indexes' win).
- 2026-08-05: done: 4.11b (labelled statements, #25) -- split from #19.
  Every `RE_*` verb pattern anchors on `^\s*`, so a generic label
  (`SETA. MOVE 'CONF' TO #STATUS`) defeats all of them except the R#/F#/H#
  loop-label groups already inline in `RE_READ`/`RE_FIND`/`RE_HISTOGRAM`
  (issue 4.3's narrower, verb-specific thing, tracking which entity a
  labelled database loop opened -- untouched by this change). New
  `strip_generic_label()` is tried only as a last resort, after the
  unstripped statement has already failed every matcher in the cascade --
  this ordering is load-bearing, since trying it first would strip R#/F#/H#
  labels too (the regex can't tell them apart from a generic label; nothing
  distinguishes `R1.` from `SETA.` syntactically) and silently break issue
  4.3's resolution. New `MMP9400.nsp` fixture: labelled `MOVE`/`CALLNAT`/`IF`
  now extract correctly (literal survives unmasking, call edge recorded,
  condition captured), and a labelled but genuinely unrecognised verb still
  raises the honest `unparsed_line` gap rather than a false-positive match --
  a label must never manufacture a match. `tests/test_labelled_statements.py`
  covers both paths plus an explicit regression check against MMP9200 (the
  4.3 fixture) to prove the ordering guarantee holds.
  `reference/natural-adabas.md`'s Traps section updated.
- 2026-08-05: done: 4.11a (report-writer column-spec continuations, #24) --
  split from #19, the last of that split. Natural's report-writer
  column-position tokens (`5T` = tab to column 5, `2X` = skip 2 spaces) can
  appear on their own continuation line within a `WRITE`/`DISPLAY`/`PRINT`
  operand list, with no keyword for `CONTINUATION_LEAD` to key off. New
  `CONTINUATION_LEAD_COLSPEC`, ORed into the fold condition but scoped to
  when the statement being folded is itself `WRITE`/`DISPLAY`/`PRINT`
  (checked via `RE_WRITE.match(stmt)` on the in-progress fold) -- a bare
  `5T` on its own line in any other context is much more likely a genuine
  unrecognised construct than a continuation. New `MMP9500.nsp` fixture:
  its three-line `WRITE` folds into one `interaction` row carrying the
  whole operand list. Confirmed (and left alone, matching existing
  precedent) the same accepted quirk 4.5/MMP9000 already documents: a
  physical line already folded into the preceding statement is still
  visited on its own by the main loop afterwards, correctly doesn't stand
  alone as a statement, and raises its own (harmless, expected)
  `unparsed_line` gap -- not a defect this item needed to fix.
  `tests/test_column_spec_continuations.py` covers the fold and that
  quirk explicitly. `reference/natural-adabas.md` updated. **Issue #19's
  full split (4.11a/#24, 4.11b/#25, 4.11c/#26) is now closed.**
- 2026-08-06: done: 4.6 (reporting-mode block inference, #5) -- the largest
  single item left in Phase 4. Reporting mode has no `END-LOOP`; scope was
  previously always flagged unreliable rather than reported at all. New
  `reporting_loop_plan()` maps every `LOOP` in a reporting-mode member to
  its own indentation column, but only if every one of them passes a
  strict check: the very next code line's indentation must be *strictly
  greater* than the `LOOP` line's own. The moment any `LOOP` in the member
  fails that check, inference is abandoned for the *whole* member -- not
  per-`LOOP` -- since one ambiguous indentation convention casts doubt on
  whether the rest of the member's indentation can be trusted either.
  When it succeeds: `LOOP` is now recorded as a `rule_candidate`
  (`confidence='inferred'`), and it participates in `depth` the same way
  `IF`/`DECIDE`/`FOR`/`REPEAT` already do in structured mode, so anything
  nested inside a `LOOP` body gets a correctly-elevated depth too, for free
  -- no changes needed to `_match_rules` itself. Closing is indentation-
  based (dedent to or past the `LOOP`'s own column), tracked in a small
  stack decoupled from `open_blocks` (which stays exactly as it was, since
  `LOOP` has no closing keyword for that mechanism to key off of). The
  member's `reporting_mode` gap drops from high to medium severity when
  inference succeeds ("inferred, confirm before trusting" rather than
  "unreliable"); stays high, unchanged, when it doesn't. New schema column
  `rule_candidate.confidence` (`verified` default, matching the existing
  `entity_link`/`data_access`/`doc_claim` vocabulary -- no new terms
  invented). New `MMP9600.nsp` (unambiguous -- gets the inference) and
  `MMP9700.nsp` (deliberately ambiguous -- proves the conservative
  fallback) fixtures; `tests/test_reporting_mode_inference.py` covers both
  plus an explicit check that structured-mode members are untouched.
  `tests/test_batch.py`/`tests/test_coverage_snapshot.py` updated for the
  two new fixtures. `README.md`/`SKILL.md`'s "Known limitations" and
  `reference/natural-adabas.md`'s reporting-mode section updated to
  describe the new inferred/ambiguous split instead of a blanket
  "unreliable" claim.
- 2026-08-06: done: 5.2 (citation-accuracy sampling, #8) -- the last open
  item from this session's backlog sweep. `mfdoc validate` proves every
  citation *resolves*; it never proved one was *right*. New `mfdoc
  sample-citations` subcommand (new `src/mfdoc/sample.py`): samples up to
  N claims per document (reusing `validate.py`'s own `_logical_units`
  sentence-splitting, so a sampled claim is exactly what a reader sees as
  one assertion), resolves each against its cited source line(s), and
  records a verdict. `--judge human` is required first (interactive
  terminal labelling, resumable via a JSON state file -- same pattern as
  `batch.py`'s `_load_state`/`_save_state`) to calibrate what "the source
  supports the claim" means for this document set; `--judge llm` refuses
  to run standalone until at least one human verdict exists, then reports
  its agreement with the human labels rather than being trusted on its
  own. `--judge llm` reuses the exact same caller-construction path as
  `mfdoc batch` (extracted the shared logic into `cli._build_model_caller`
  to avoid duplicating the fake-echo/Anthropic/Vertex selection and the
  Vertex-needs-`--model` guard) and applies the same redaction discipline
  before anything reaches the prompt.

  The resulting `citation_accuracy_rate` is persisted via the existing
  `metric` table and surfaced in `graph.coverage()` -- but only once at
  least one human verdict has been recorded; omitted otherwise, so every
  existing coverage-snapshot test stays byte-for-byte unaffected until a
  project actually runs the sampling command. New
  `min_citation_accuracy_rate` entry in `cli.GATES`, following the
  existing `(options_key, coverage_key, kind, blocks)` shape -- an
  unsampled codebase evaluates against 0 and correctly fails the gate if
  configured, rather than silently passing.

  New `tests/test_sample_citations.py`: pure unit tests for the
  claim/citation extraction and verdict arithmetic, plus full-command
  tests against an isolated project (deliberately never the shared session
  `indexed_db` fixture other tests use -- this command's `set_metric` call
  would otherwise leak `citation_accuracy_rate` into
  `test_coverage_snapshot.py`'s exact-dict-equality check for whichever
  test file happens to run after it, a fragile order-dependency this repo
  has already been bitten by twice this session). `docs/guides/
  security-and-compliance.md` updated: replaced the "not yet built, Phase
  5" framing with what the sampling command does and its explicit
  limitation (still a sample, never a full-corpus check), and corrected
  the "no other network code" claim now that `--judge llm` is a second
  path that can reach a model provider.

  **All six issues taken up in this session's backlog sweep (#26, #9,
  #25, #24, #5, #8) are now closed.**
- 2026-08-06: done: follow-up on #9 -- a 2026-08-05 comment on the
  (still-open) issue asked to extend incremental ingest's file-level skip
  to also skip regenerating unchanged briefs, cross-referencing
  Azure-Samples' Legacy-Modernization-Agents `--reuse-re` flag. A
  per-member skip keyed on that member's own `source_file.sha256` would be
  unsound: `brief.module_brief()` also pulls in facts owned by *other*
  members (inbound callers, copycode-inherited rules), so a member's brief
  can change even when its own file didn't. The only correct cheap check
  is corpus-wide: new `batch._corpus_signature()` hashes every
  `(source_file.path, sha256)` pair; when it matches the prior successful
  run's signature (persisted as `_corpus_sha256` in the state file
  alongside the existing per-member entries), nothing in the whole corpus
  changed, so `run_batch()` skips calling `module_brief()` entirely for
  every member with a prior `ok` run and an existing output file -- not
  just skipping the model call, as it already did. A changed corpus falls
  back to exactly the pre-existing per-member brief-hash skip. New tests
  in `tests/test_batch.py` cover both: `module_brief()` isn't called at
  all when nothing changed, and it's still called (but the model isn't
  re-invoked) when a source file's sha256 changes but no dependent brief
  content actually did. Issue #9 is now closed for good.
- 2026-08-11: new feature area, not previously scoped in this plan: test
  generation. The same fact-vs-narrative split applied to tests instead of
  prose -- `testplan.py` derives cited Given/When/Then `test_case` rows from
  `rule_candidate`/`variable`/`data_access` facts (model-free), `testadvisor.py`
  classifies each unit for mockability/integration-only scope and suggests
  refactor seams (model-free, advisory prose only, never touches source),
  `testoverlay.py` is the one place a model may *propose* a bug-vs-spec
  status split (always `review_status: draft`; only a human promoting it
  past `draft` makes it real), and `testbatch.py` reuses `batch.py`'s
  `ModelCaller`/retry/resumable-corpus-signature harness verbatim to render
  scenarios into `language`/`framework`-specific test files (still Markdown
  + citations, so `validate_doc` already enforces trust on them --
  `validate_test_doc` adds one check specific to tests: every bare
  `MEMBER:BR-nnn` reference must name a real derived scenario). New CLI
  commands: `test-plan`, `test-advisory`, `test-overlay-draft`, `test-gen`,
  `test-batch`, `test-validate`. New doc:
  `docs/guides/testing-strategies-for-mainframes-and-4gl.md` -- modern
  testing vocabulary mapped to mainframe/4GL equivalents, and why a shared
  vocabulary/artifact between legacy and migration engineers is the actual
  organisational payoff, not the generated files themselves. Scoped to
  Natural/Adabas first (clearest per-unit parameter contracts); Mantis/Supra
  follow once calibrated, same as the docs side.
- 2026-08-11: wired `options.testgen` into `project.yml`
  (`default_language`/`default_framework`/`overlay_path`/`out_dir`) --
  `test-plan`'s `--overlay`, `test-overlay-draft`'s `--out`, and
  `test-gen`/`test-batch`'s `--language`/`--framework`/`--out` now fall
  back to it, same pattern as `options.narrative`/`options.redact` for the
  docs pipeline. CLI flags still override per-run. `--language`/
  `--framework` stay unset by default -- no built-in guess at a migration
  team's destination stack, same policy as dialect/redaction config.
  `test-gen`/`test-batch` exit 2 with a clear message if neither the flag
  nor the config key is present. `mfdoc test-plan --config project.yml`
  (no flags) now works standalone once `options.testgen.overlay_path` is
  set.
- 2026-09-06: follow-up from an external verification report reviewed on a
  client engagement (a third-party review of first-cut module docs for two
  Natural programs), generalized into pipeline fixes rather than one-off
  patches where the underlying gap was dialect-neutral. Opened as PR #67:
  - **Dangling cross-chunk references (fixed at the source, not just
    detected)**: `_generate_module_doc_chunked` (`batch.py`) now computes
    the full routine -> chunk-number mapping before narrating any chunk and
    hands it to every chunk's own brief (`module_brief`'s new `chunk_map`
    param) -- a chunk whose own rules dispatch to a routine documented in a
    *different* chunk can now write "documented in chunk 15" instead of an
    unresolved "covered by a later chunk". `validate.py`'s new
    `_deferred_reference_problems` (advisory) catches a regression back to
    the vague phrasing, for any dialect.
  - **Stale-regeneration check**: `validate.py`'s new `_staleness_problem`
    (advisory) flags a document whose `generated_by` version differs from
    the `mfdoc` version installed now -- same version-bump-only caveat
    `_corpus_signature` already documents.
  - **`label_control_mismatch` gap kind** (`graph.py`): a same-branch
    on-screen label literal and internal control-field literal (e.g. a
    PF-key labelled "Send" that actually sets an internal option field to
    "AMEND") that don't obviously agree, surfaced as an `sme_question` for
    a human to confirm -- deliberately never asserts the two are wrong
    (unlike the existing reversed-condition check, there's no formal ground
    truth for whether a label and a control code are *supposed* to match).
    Dialect-neutral (runs over `rule_candidate`, same as `natural`/`mantis`
    both populate it).
  - **New `mfdoc dispatch-map` command** (`structural.py`): for every
    branch comparing a configurable dispatch field (default Natural's
    `*PF-KEY`) against a literal, the routines it calls and fields it sets
    within that branch -- a "what does dispatching on this value actually
    do" table. New `options.overview.dispatch_field_pattern` config key
    (replace-not-merge, same convention as `outcome_field_pattern`) is the
    generalization seam for Mantis/Supra or any other dialect's own
    dispatch idiom, since there's no single fixed field name to default to
    the way Natural has `*PF-KEY`.
  - Documented the existing `unresolved_call`/`no_ddl_for_entity` gap kinds
    plus `mfdoc gate`'s `max_high_severity_gaps` as the pre-flight missing-
    dependency workflow (`docs/guides/architecture.md`) -- this already
    existed; it just wasn't written down as a named workflow anywhere.
  - Added a "Planning an improvement or bug fix" section to `CLAUDE.md`:
    generalize first, add explicit per-dialect variants only where general
    truly isn't possible, update docs as part of the same change -- codifying
    the approach taken in this entry itself.
  - **New known gap found while regenerating a chunked member against
    these fixes, not yet fixed**: bumping `__version__` alone did not force a chunked
    member's regeneration. `run_batch`'s per-member resume check (`batch.py`)
    hashes a bare, unchunked `module_brief()` call that never receives
    `chunk_map` -- so when a code change (like this one) only affects what
    `_generate_module_doc_chunked` builds per-chunk, that top-level
    fingerprint is byte-identical to before the change even though the
    actual per-chunk briefs (and `_generate_module_doc_chunked`'s own,
    separately-correct `prior_chunks` skip logic) would differ. The
    version-bump-in-`_corpus_signature` mechanism only short-circuits the
    *whole-batch* fast path; it doesn't make the per-member fallback check
    version-aware. Worked around this round with `--state ""` (full,
    deliberate regeneration, no resume) rather than fixing the fingerprint
    itself -- a real fix would need the top-level per-member check for a
    to-be-chunked member to defer to `_generate_module_doc_chunked`'s own
    (already-correct) per-chunk hashing instead of short-circuiting on a
    coarser whole-member hash first. Dialect-neutral; not specific to this
    round's fixes -- would recur for *any* future change with the same
    shape (chunked-path-only behavior change with no effect on the bare,
    unchunked brief text).
  - Not attempted this round: retroactively regenerating already-published
    module docs for other engagements to pick up these fixes -- each
    project's own team re-runs `mfdoc batch` when ready, per the existing
    corpus-signature/resume model.

**Progress (2026-09-06):** Implemented #69 (whole-module overview for a
chunked member's index document). `_render_module_chunk_index` (batch.py)
was a mechanical index only -- a chunk file list plus a flat `BR-nnn`
enumeration -- with no single document to start reading a large module
from. It's now `_render_module_index_doc`, producing a new `doc_type:
module_index` document (`templates/module-index.md`) with: deterministic
BR-id ranges and a chunk-derived processing-sequence skeleton (both
computed from facts already recorded when the chunks were built --
`fetch_routines`/`routine_for_line`, no model call), consolidated/
deduplicated gap-register entries and `sme_questions` across every chunk,
plus one reconciled Purpose/How-invoked/Inputs/Data-used/Outputs-and-effects
narrative from a single bounded model call per chunked member (never per
chunk) -- `_generate_module_index_narrative`, wired automatically into
`_generate_module_doc_chunked` right after every chunk validates ok, reusing
the same call/validate/retry-once machinery every other model call in
`batch.py` already uses. The call's only input is each ok chunk's own
already-validated, already-cited sections -- never source, never a fresh
`module_brief()` -- so it can't reintroduce the silent-truncation risk
chunking exists to guard against; a chunk failure skips it entirely. It's
also resumable the same way per-chunk generation is: a state-file entry
keyed `_narrative` skips the call when every ok chunk's body is unchanged
from the prior run. `validate.py`'s `doc_type == "module"` special-cases
(first-line-heading check, the reversed-condition/statement-completeness
gate) now also cover `module_index`; `module_completeness_problems`
deliberately does not, since chunks alone already satisfy it. Design spec:
`docs/superpowers/specs/2026-09-06-chunked-module-index-overview-design.md`.
Tested with the repo's existing fake-caller pattern (no live API key used);
a real `mfdoc batch` run against `ANTHROPIC_API_KEY` to eyeball actual model
output quality for the reconciliation call is a documented follow-up, not a
blocker.

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
