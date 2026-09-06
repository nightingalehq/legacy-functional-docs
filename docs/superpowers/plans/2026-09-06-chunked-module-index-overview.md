# Chunked module-index whole-module overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a chunked member's index document (currently a mechanical chunk list + flat BR-id enumeration, `_render_module_chunk_index`) the same section set as `templates/module.md`, at whole-module level: deterministic BR-id ranges, a chunk-derived processing-sequence skeleton, and consolidated/deduplicated gaps, plus one reconciled Purpose/How-invoked/Inputs/Data-used/Outputs-and-effects narrative from a single bounded model call per chunked member (never per chunk), run automatically inside `mfdoc batch` right after a member's last chunk validates ok.

**Architecture:** `_render_module_chunk_index` is replaced by `_render_module_index_doc`, which assembles a new `doc_type: module_index` document (new `templates/module-index.md`) from: (a) purely deterministic sections computed from already-recorded facts (chunk ranges + routine labels, consolidated gap rows + chunk `sme_questions`), and (b) a `sections` mapping for the five narrative headings, either the model's reconciled text or a "skipped/failed" placeholder. `_generate_module_index_narrative` is a new call -> validate -> retry-once loop (modelled on `_generate_module_doc_from_brief`) that builds its prompt purely from each ok chunk's own already-validated Purpose/How-invoked/Inputs/Data-used/Outputs-and-effects sections (extracted via a new `_extract_section` helper) -- never source, never the module's fact brief. `validate.py` extends its `doc_type == "module"` special-cases to also cover `module_index`. No new orchestration path: this reuses `_generate_module_doc_chunked`'s existing per-member call/retry/state machinery, one more step in the same function.

**Tech Stack:** Python ≥3.10, stdlib `re`/`datetime`/`hashlib`, SQLite via `conn.execute(...).fetchall()`. pytest with the repo's existing fake-caller pattern (`FakeCaller`/`_chunk_aware_module_caller` in `tests/test_batch.py`) -- no live Anthropic call.

**Spec:** `docs/superpowers/specs/2026-09-06-chunked-module-index-overview-design.md`

## Global Constraints

- No Natural/Mantis-specific logic anywhere touched -- `batch.py`/`templates/` stay dialect-neutral.
- The reconciliation call's only input is already-cited, already-validated per-chunk prose -- never raw source, never `module_brief()`. It must not become a second full-derivation pass.
- The reconciliation call runs only when every chunk's `DocResult.ok` is `True`; any chunk failure skips it entirely (deterministic-only index, same as today, plus a plain skipped-note).
- No client-specific content anywhere (code, tests, docs, commit messages, PR text) -- invented fixture names/content only.
- No new runtime dependency; `anthropic`/model-calling still only ever reached through the existing `ModelCaller` abstraction.

---

## File Structure

- **Modify:** `src/mfdoc/batch.py` -- replace `_render_module_chunk_index` with `_render_module_index_doc`; add `_extract_section`, `_chunk_processing_labels`, `_consolidated_gap_lines`, `build_reconciliation_prompt`, `_split_reconciled_sections`, `_generate_module_index_narrative`; extend `_generate_module_doc_chunked`, `generate_module_doc`, `run_batch` to thread an optional `index_template` through.
- **Add:** `templates/module-index.md` -- the new document contract.
- **Modify:** `src/mfdoc/validate.py` -- extend the `doc_type == "module"` first-line-heading check and `module_doc_checks` gate to also match `module_index`.
- **Modify:** `src/mfdoc/cli.py` -- `cmd_batch` loads `templates/module-index.md` and passes it through to `run_batch`.
- **Modify:** `tests/test_batch.py` -- update existing chunked-index assertions for the new shape; add new tests for range aggregation, gap dedup, chunk-derived sequence skeleton, and the narrative synthesis call (ok, missing-section retry, all-chunks-not-ok skip).
- **Modify:** `docs/guides/architecture.md` -- update the Narrate bullet.
- **Modify:** `docs/plans/legacy-functional-docs-plan.md` -- append dated progress entry.

---

### Task 1: Deterministic BR-id ranges + routine labels

**Files:**
- Modify: `src/mfdoc/batch.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Produces: `_chunk_processing_labels(rule_rows: list, routines: list[dict], ranges: list[tuple[int, int]]) -> list[str]` -- one label per range, naming the routine(s) (backtick-quoted, in first-seen order) whose rules fall inside it, or `"member main body (no internal routine)"` when none do.
- Consumes: `brief.routine_for_line` (already imported indirectly -- add `routine_for_line` to the existing `from .brief import ...` line), `rule_rows`/`routines`/`ranges` already computed in `_generate_module_doc_chunked`.

- [ ] Add `_chunk_processing_labels` to `batch.py`, next to `_aggregate_chunk_confidence`.
- [ ] Unit test in `tests/test_batch.py`: three rule rows across two routines plus one main-body rule, two ranges -- assert the labels list matches the expected routine-name-or-fallback text per range.
- [ ] Run: `pytest tests/test_batch.py -k processing_labels -v`

---

### Task 2: Consolidated, deduplicated gaps

**Files:**
- Modify: `src/mfdoc/batch.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Produces: `_consolidated_gap_lines(conn, member_id: int, ok_chunk_paths: list[Path]) -> list[str]` -- `gap` table rows for `member_id`, ordered by real severity priority via `db.GAP_SEVERITY_ORDER_SQL` (same as `module_brief`'s own "Known gaps" section, which shares that constant), formatted `"[severity] gap_kind: detail"`, followed by every ok chunk's own `sme_questions` front-matter strings not already present verbatim, deduplicated overall.
- Consumes: `_split_frontmatter` (already imported in `batch.py`).

- [ ] Add `_consolidated_gap_lines` to `batch.py`.
- [ ] Test: two `gap` rows for a member plus two chunks whose `sme_questions` include one duplicate (of each other) and one duplicate of a gap row's own detail text -- assert the result has no duplicates and preserves gap-rows-first ordering.
- [ ] Run: `pytest tests/test_batch.py -k consolidated_gap -v`

---

### Task 3: `templates/module-index.md`

**Files:**
- Add: `templates/module-index.md`

- [ ] Write the template: front matter block with `doc_type: module_index` (mirror `templates/module.md`'s other front-matter fields), then `## Purpose`, `## How it is invoked`, `## Inputs`, `## Data used`, `## Business rules` (describe: ranges + routine labels, cross-reference to chunk files, not per-rule detail), `## Processing sequence` (describe: one line per chunk, chunk-skeleton not per-rule transliteration), `## Outputs and effects`, `## Gaps and questions for review` (describe: consolidated across chunks, deduplicated), `## Chunk files` (the per-chunk file/range/status list).
- [ ] Cross-check against `reference/writing-rules.md` for tone/section-description consistency; no code change needed there since this template's contract is genuinely different (ranges vs. per-rule detail), but keep phrasing style consistent.

---

### Task 4: `validate.py` -- extend `module` special-cases to `module_index`

**Files:**
- Modify: `src/mfdoc/validate.py`
- Test: `tests/test_validate.py`

**Interfaces:**
- Modifies: the `if fm.get("doc_type") == "module":` first-line-heading check (around `validate.py:458`) to `if fm.get("doc_type") in ("module", "module_index"):`.
- Modifies: `module_doc_checks = fm is not None and fm.get("doc_type") == "module"` (around `validate.py:475`) to `fm.get("doc_type") in ("module", "module_index")`.
- Does **not** modify `module_completeness_problems`'s `fm.get("doc_type") != "module"` gate (around `validate.py:676`) -- per the design's non-goal, `module_index` documents are deliberately excluded from that union.

- [ ] Make both edits.
- [ ] Test: a minimal `doc_type: module_index` document with one valid citation and one deliberately uncited assertive sentence -- assert `validate_doc` flags the uncited sentence (proves the general check applies) and that a `doc_type: module_index` doc is *not* counted toward `module_completeness_problems`'s BR-id union (construct alongside a `doc_type: module` doc for the same member missing one BR-id -- assert the missing id is still reported even though the module_index doc happens to mention a BR-id text near it).
- [ ] Run: `pytest tests/test_validate.py -k module_index -v`

---

### Task 5: `_extract_section` + reconciliation prompt building

**Files:**
- Modify: `src/mfdoc/batch.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Produces: `_extract_section(body: str, heading: str) -> str | None` -- text of the first `## {heading}` section (stripped), or `None` if absent/blank.
- Produces: `NARRATIVE_SECTIONS: tuple[str, ...] = ("Purpose", "How it is invoked", "Inputs", "Data used", "Outputs and effects")`.
- Produces: `build_reconciliation_prompt(member_name: str, chunk_sources: list[str], writing_rules: str, index_template: str | None, retry_note: str | None = None) -> str`.
- Produces: `_split_reconciled_sections(text: str) -> tuple[dict[str, str], list[str]]` -- `(sections_found, missing_headings)`.

- [ ] Add `import re` to `batch.py`'s imports.
- [ ] Implement `_extract_section`, `NARRATIVE_SECTIONS`, `build_reconciliation_prompt`, `_split_reconciled_sections`.
- [ ] Test `_extract_section`: a body with three `## Heading` sections -- assert correct extraction, `None` for an absent heading, `None` for a heading present but blank.
- [ ] Test `_split_reconciled_sections`: a response text missing one of the five headings -- assert it lands in `missing`, the other four in `sections_found`.
- [ ] Run: `pytest tests/test_batch.py -k "extract_section or split_reconciled" -v`

---

### Task 6: `_generate_module_index_narrative` (the new model-call loop)

**Files:**
- Modify: `src/mfdoc/batch.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Produces: `_generate_module_index_narrative(conn, member_name: str, chunk_bodies: list[tuple[int, str]], caller: ModelCaller, writing_rules: str, index_template: str | None, out_path: Path, assemble: Callable[[dict[str, str]], str], max_attempts: int = 2) -> tuple[bool, int, int, int, list[str]]` -- `(ok, attempts, input_tokens, output_tokens, problems)`. Builds one prompt from all given chunk bodies' five named sections, calls `caller` once, splits the response, assembles the full candidate document via `assemble(sections)` (missing headings filled with a placeholder string), writes it to `out_path`, validates with `validate_doc`, retries once (via `_retry_note`) on any problem (missing-section or `validate_doc` problems), same shape as `_generate_module_doc_from_brief`.

- [ ] Implement `_generate_module_index_narrative`.
- [ ] Test (fake caller returning all five sections, each citing a real line already used in the given chunk bodies): assert `ok is True`, `attempts == 1`, tokens summed, and the written `out_path` validates.
- [ ] Test (fake caller whose first response is missing `## Inputs`, second response complete): assert `ok is True`, `attempts == 2`.
- [ ] Test (fake caller that never includes a required citation, so `validate_doc` keeps failing both attempts): assert `ok is False`, `attempts == 2`, problems non-empty.
- [ ] Run: `pytest tests/test_batch.py -k narrative -v`

---

### Task 7: `_render_module_index_doc` (replaces `_render_module_chunk_index`)

**Files:**
- Modify: `src/mfdoc/batch.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Replaces `_render_module_chunk_index(member_name, system, chunk_entries, confidence)` with `_render_module_index_doc(member_name: str, system: str | None, chunk_entries: list[tuple[int, tuple[int, int], Path, DocResult]], confidence: dict[str, int], routine_labels: list[str], gap_lines: list[str], sections: dict[str, str]) -> str`, with `doc_type: module_index` in front matter, sections ordered: intro, Purpose, How it is invoked, Inputs, Data used, Business rules (ranges + labels), Processing sequence (skeleton), Outputs and effects, Gaps and questions for review (from `gap_lines`), Chunk files (the old per-chunk list, renamed).
- Produces: a small `_SKIPPED_NARRATIVE_SECTIONS` builder (or inline dict comprehension) used when chunks aren't all ok -- one placeholder string per `NARRATIVE_SECTIONS` heading, phrased so it doesn't trip `validate.py`'s `ASSERTIVE` regex (must not open with "The module"/"This module"/etc.).

- [ ] Implement `_render_module_index_doc`.
- [ ] Update the four existing chunked-index tests in `tests/test_batch.py` (`test_generate_module_doc_chunks_a_member_with_many_rules` and its siblings) for the new front matter (`doc_type: module_index`) and range-based Business rules assertions (`BR-001..BR-00N` style, not per-id enumeration) -- keep every existing behavioural assertion (confidence aggregation, chunk failure reporting, resume/skip machinery) intact, only the index document's own shape changes.
- [ ] Run: `pytest tests/test_batch.py -v`

---

### Task 8: Wire the narrative step into `_generate_module_doc_chunked`

**Files:**
- Modify: `src/mfdoc/batch.py`
- Test: `tests/test_batch.py`

**Interfaces:**
- Modifies: `_generate_module_doc_chunked(..., index_template: str | None = None)` -- after the existing per-chunk loop, compute `routine_labels` (Task 1) and `gap_lines` (Task 2); if every chunk `ok`, call `_generate_module_index_narrative` (Task 6) with the ok chunks' bodies and fold its tokens/problems into the member's totals; otherwise use the skipped-narrative placeholder sections and append a problem noting synthesis was skipped. Assemble and write the final document via `_render_module_index_doc` (Task 7) either way, then keep the existing final `validate_doc` safety-net call unchanged.
- Modifies: `generate_module_doc(..., index_template: str | None = None)` and `run_batch(..., index_template: str | None = None)` to thread the same parameter through to `_generate_module_doc_chunked`.

- [ ] Make the changes; keep `DocResult.chunked`/`chunk_state` semantics unchanged (chunk_state is still per-chunk only -- the narrative call's own success/failure is not part of resumable per-chunk state, since it always re-runs when re-entering the chunked path with `prior_chunks` reused, same as the index document itself is always rebuilt).
- [ ] Test: end-to-end with `_chunk_aware_module_caller`-style fakes covering (a) all chunks ok + narrative ok -> index has real Purpose/Inputs/etc text and `result.ok is True`; (b) all chunks ok but narrative caller never satisfies validation -> `result.ok is False`, problem mentions narrative synthesis, chunk files themselves are still `ok`; (c) one chunk fails -> narrative call never invoked (assert via a call-counting fake caller wrapping the narrative-only prompts) and index carries the skipped-note placeholders.
- [ ] Run: `pytest tests/test_batch.py -v`

---

### Task 9: CLI wiring

**Files:**
- Modify: `src/mfdoc/cli.py`

- [ ] In `cmd_batch`, load `templates/module-index.md` the same way `template` is loaded (tolerate a missing file with an empty string, so a project without this template file doesn't crash `mfdoc batch` -- only the reconciliation prompt loses the optional contract text, nothing else changes), pass as `index_template=` to `run_batch`.
- [ ] Run the bundled-fixtures pipeline check per `CLAUDE.md`'s Commands section (`mfdoc ingest/derive/coverage/validate` against the checked-in `project.yml`/`examples/`) to confirm nothing pipeline-adjacent regressed. Do **not** run `mfdoc batch` itself (needs `ANTHROPIC_API_KEY`).

---

### Task 10: Docs

**Files:**
- Modify: `docs/guides/architecture.md`
- Modify: `docs/plans/legacy-functional-docs-plan.md`

- [ ] Update the "Narrate" bullet in `docs/guides/architecture.md` to mention the automatic per-chunked-member reconciliation call and its scope (five sections, already-validated per-chunk input only).
- [ ] Append a `**Progress (2026-09-06):**` entry near the top of `docs/plans/legacy-functional-docs-plan.md`, same style as existing entries -- summarise what shipped, reference issue #69, note that live end-to-end verification of the new model call (a real `mfdoc batch` run with `ANTHROPIC_API_KEY`) is a documented follow-up.

---

### Task 11: Full test suite + wrap-up

- [ ] `pytest` (full suite) -- must pass.
- [ ] Re-read the diff and this plan's Global Constraints once more before committing -- especially the no-client-content rule, since PR text is exactly where a real system's codename has slipped through before (see `CLAUDE.md`).
- [ ] Commit in logical chunks (deterministic parts; template + validate.py; narrative call + wiring; docs), consistent with the repo's commit style.
