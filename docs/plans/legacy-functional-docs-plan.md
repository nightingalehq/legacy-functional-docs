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
    every document's `sources` front matter (required on every narrative/
    generated-test document) is cross-checked against the currently loaded
    fact store's own `member` table before validation runs -- a document
    naming at least one source, but none of them present in this store, is
    skipped rather than validated against the wrong project's fact store.
    A document with no `sources` key or an empty list (`doc_type: register`
    documents, `interface-matrix.md`'s legitimately empty list) carries no
    signal either way and validates exactly as before.
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
