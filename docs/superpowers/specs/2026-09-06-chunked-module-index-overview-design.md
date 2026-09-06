# Design: whole-module overview for chunked module-doc index

Date: 2026-09-06
Status: approved
Issue: nightingalehq/legacy-functional-docs#69

## Problem

When a member is chunked (`_generate_module_doc_chunked` in `batch.py`,
triggered above `options.narrative.max_rules_per_call`), the document at the
member's normal `out_path` is currently just a mechanical index: a list of
chunk files with their rule ranges and status, plus a flat list of every
`BR-nnn` id covered (`_render_module_chunk_index`). That renderer is
deliberately deterministic -- never model-generated -- because chunking
exists precisely to guard against a single oversized completion silently
truncating, so the summary of what got covered must not itself be asked of
a model that could get it wrong the same way.

The gap: a large module (the exact case chunking exists for) ends up with
no single document a reader can start from to understand the module as a
whole. Someone inheriting a 16-chunk module has to open all 16 files and
build their own mental model of purpose/inputs/outputs/processing flow --
exactly the problem this tool exists to solve, pushed one level up.

## Non-goals

- Not a second full module-doc generation pass. The narrative call this
  design adds never reads source or the module's fact brief -- only the
  Purpose/How-invoked/Inputs/Data-used/Outputs sections each chunk *already
  wrote and already validated*. It reconciles, it does not re-derive.
- Not a replacement for per-chunk detail. The full per-rule enumeration,
  full processing narrative, and per-chunk gap register entries continue to
  live in each chunk document exactly as today.
- Not a blocker on chunk generation. The synthesis call only ever runs
  after every chunk in the member is `ok`; a member with any failed chunk
  gets the deterministic sections only (as today), with a clearly-labelled
  note instead of a narrative.

## Design

### Split: deterministic vs. synthesized

Two kinds of content, split by the same test the issue's own reasoning
uses -- does producing this line require a judgement call about which of
several overlapping (or conflicting) sources to trust, or is it a pure
aggregation of facts already recorded once?

**Deterministic (no model call, computed the same way `_render_module_chunk_index`
already computes today's fields):**

- **Business rules, as ranges** -- each chunk's own `(start, end)` rule-ordinal
  range (already computed by `routine_aware_chunk_ranges` to pick chunk
  boundaries) rendered as `MEMBER:BR-nnn..MEMBER:BR-mmm`, labelled with the
  routine name(s) whose rules fall in that range (via `brief.routine_for_line`
  against the member's own `fetch_routines` rows -- the same facts
  `module_brief`'s "Internal routines" section already surfaces). Replaces
  the old flat per-id enumeration; the full enumeration still lives in each
  chunk.
- **Processing sequence, as a skeleton** -- one line per chunk, restating the
  same range + routine-name label from a sequence-of-chunks angle ("chunk 2
  (FAKEMOD.chunk2.md): BR-006..BR-010 -- routine `VALIDATE-TAG`"). This is
  the chunk-list-derived skeleton the issue describes, not a
  transliteration of any chunk's own detailed processing-sequence prose.
- **Gaps and questions for review, consolidated** -- every `gap` table row for
  the member (same query `module_brief`'s "Known gaps" section already runs)
  plus every ok chunk's own `sme_questions` front-matter entries, deduplicated
  by exact text. Both are already-recorded facts; this just collates them
  instead of leaving a reviewer to open 16 chunks and collect them by hand.
- **Chunk files** -- the original per-chunk file/range/status list, kept for
  navigation, renamed from the old top-level "Chunks" section.

None of the above touches a model. A bug in computing them fails loudly via
`validate_doc` on the assembled document, same as `_render_module_chunk_index`
does today -- deliberately, per that function's own docstring reasoning.

**Synthesized (one bounded model call per chunked member):**

- Purpose, How it is invoked, Inputs, Data used, Outputs and effects -- one
  coherent whole-module statement for each, instead of N chunks each saying
  something similar for their own slice. This genuinely needs judgement:
  several already-validated statements that overlap, and occasionally
  disagree in emphasis or scope, need reconciling into one.

### Why a new model call is safe here (and where the line is)

The chunking problem this issue must not reintroduce is: a single
completion asked to cover too much source risks silently dropping content,
in a way that still passes citation validation because whatever *is* there
cites cleanly. That risk is specific to asking a model to *derive* new
claims from a large amount of raw source. The reconciliation call this
design adds is structurally different:

- Its only input is already-cited, already-validated prose (the five named
  sections extracted from each ok chunk's own body) -- never source lines,
  never the module's fact brief, never anything not already reviewed once.
- Every citation it may use already resolved once, when the chunk that
  carries it validated. Copying a citation forward cannot regress; the risk
  surface is "did it drop or misstate something", which is exactly what
  `validate_doc`'s existing uncited-assertion and citation-resolution checks
  already catch when run over the assembled document, no new check needed.
- The instructions explicitly forbid inventing any claim not already present
  in the given excerpts (see `build_reconciliation_prompt` in
  `batch.py`), and ask for a discrepancy between chunks to be surfaced as an
  `unresolved` item rather than silently resolved one way.
- Its input size is bounded by five short sections times the chunk count,
  not by the member's full rule set -- the same order of magnitude as one
  ordinary (unchunked) module's brief, not larger just because the module
  itself is large.

This mirrors how `brief.executive_brief()` and the interactive
system-overview path already treat "judgement about what to say once, given
several overlapping fact sources" as the one place a model call belongs,
while everything reused verbatim from prior output stays deterministic.

### Wiring into the batch pipeline

**Decision: runs automatically, as part of `mfdoc batch`, immediately after
the last chunk of a member completes -- not a separate opt-in step.**

Reasoning:

- The issue's own gating language ("only runs for chunked members, only
  after every chunk is `ok`") describes an automatic gate, not a manual
  trigger a human has to remember to run.
- `executive_brief()`'s own model-authored counterpart (the
  executive-summary doc) lives in the *interactive* CLI/Claude Code path,
  not `mfdoc batch`, specifically because judgement about grouping benefits
  from a session holding the whole system in mind (see
  `docs/guides/architecture.md`'s Narrate bullet). That reasoning doesn't
  transfer here: this call's scope is one already-chunked module, not the
  whole system, and its input is fixed and mechanical (five sections times
  N chunks) rather than open-ended judgement about what to include from an
  entire codebase. There's no session-level context this call would benefit
  from that a bounded per-member call inside `mfdoc batch` doesn't already
  have.
- A separate opt-in step would mean every chunked member silently keeps
  today's placeholder-only index until someone remembers to run a second
  command -- the "no single document a reader can start from" problem this
  issue exists to fix would persist by default.
- It reuses `batch.py`'s existing per-item `ModelCaller`/retry/state
  machinery (the same call -> validate -> retry-once loop
  `_generate_module_doc_from_brief` already runs per chunk) rather than
  inventing a new orchestration path, exactly as `testbatch.py` reuses that
  machinery for a different document shape.

Concretely: `_generate_module_doc_chunked` gains one more step after its
existing per-chunk loop, before writing the index document:

1. If every chunk's own `DocResult.ok` is `True`: build a reconciliation
   prompt from the five named sections of each ok chunk's body (extracted
   with a small `## Heading` section-splitter, `_extract_section`), call the
   member's own `caller`, and assemble the full index document from the
   deterministic parts above plus whatever five sections came back. Validate
   the assembled document with `validate_doc`, retrying the model call once
   (same `_retry_note` machinery) on failure -- of `validate_doc`'s own
   problems, or of the response missing one of the five required sections
   outright.
2. If any chunk failed, or the narrative call still doesn't validate after
   retrying: write the deterministic sections plus a plainly-worded note
   that narrative synthesis was skipped/failed, and add a problem to the
   member's `DocResult` (mirroring exactly how a single bad chunk today
   makes the whole member `ok=False` while every other chunk still renders
   and reports on its own).

Concurrency: this call happens on the same thread `_generate_module_doc_chunked`
already runs on (serial, after the pooled small-member work, per
`run_batch`'s existing chunked-members-are-serial rationale) -- it needs
`conn` for `validate_doc`, same constraint every other DB-touching step in
that path already has.

Cost/tokens: the reconciliation call's `input_tokens`/`output_tokens` are
folded into the member's own `DocResult` totals, same as every chunk's.

### Template: new `templates/module-index.md`, not an extension of `module.md`

`module.md`'s contract is per-rule detail: numbered rules with citations,
a full processing-sequence transliteration, transaction boundaries, error
handling. None of that belongs in the index document even conceptually --
the index's "Business rules" section is a *map* of what's where, not a
restatement of the detail, and its "Processing sequence" is a chunk
skeleton, not a step-by-step narrative. Extending `module.md` to describe
two genuinely different section contracts under one `doc_type` would make
neither reading clear. A new template with its own `doc_type` is cleaner:

- New `doc_type: module_index` (distinct from `doc_type: module`, following
  the precedent `testbatch.py` already set with `doc_type: generated_test`
  for its own chunked-index document).
- `templates/module-index.md` documents the exact section set: Purpose, How
  it is invoked, Inputs, Data used, Business rules (ranges), Processing
  sequence (skeleton), Outputs and effects, Gaps and questions for review,
  plus a Chunk files appendix -- both for a human reading the contract and
  as the (optional) template text folded into the reconciliation prompt so
  the model sees the same section-naming contract this file documents.

### `validate.py` changes

- `doc_type: module_index` documents go through the same general front-matter
  contract as `doc_type: module` (not the lighter `doc_type: register`
  contract) -- they carry real prose making real claims (the five
  reconciled sections), so the same citation/uncited-assertion checks that
  already apply to any narrative document must apply here too.
- The existing `doc_type == "module"` first-line-heading check and the
  `module_doc_checks` gate (which additionally runs the reversed-condition
  and statement-completeness checks) both extend to also match
  `module_index` -- the reconciled sections carry citations copied from
  already-validated chunks, so these checks are exactly as meaningful here
  as they are on an ordinary module doc, and cheap to run.
- `module_completeness_problems` (the check that every `rule_candidate`
  BR-id shows up somewhere across a member's `doc_type: module` documents)
  is deliberately **not** extended to include `module_index` documents --
  it's already fully satisfied by the chunks alone (each chunk is still
  `doc_type: module`), and the index's new range-based summary doesn't
  enumerate every individual id, so including it would only risk diluting
  that check's precision for no benefit.

## As built

Matches the design above. `_render_module_chunk_index` was replaced by
`_render_module_index_doc` (deterministic assembly, called with an explicit
`sections` mapping so the "narrative skipped/failed" and "narrative ok"
paths share one assembly function); `_generate_module_index_narrative`
implements the call -> validate -> retry-once loop for the reconciliation
call, modelled directly on `_generate_module_doc_from_brief`.

Two corrections found in review, before this shipped:

- **Gap dedup was keyed on `detail` text alone.** Several gap kinds emit
  identical `detail` text for every occurrence (e.g. every `unparsed_line`
  gap for a member reads the same templated sentence, differing only by
  `line_no`) -- deduping on text alone collapsed all of them into one line
  and silently dropped the rest, contradicting `templates/module-index.md`'s
  own promise ("every item from this module's gap register"). Fixed by
  keying gap-row dedup on `(gap_kind, line_no, first_sentence)`, with a
  separate, deliberately looser text-only key still used to catch an
  sme_question that just restates a gap row's own finding.
- **`validate_doc`'s citation-resolution check alone isn't proof a
  reconciled citation was actually reused, not invented.** It only proves a
  `[[MEMBER:LINE]]` citation resolves to a real source line -- a model
  could satisfy that by citing a *different*, still-real line never present
  in any excerpt it was given, attaching it to a broadened whole-module
  claim, and `validate_doc` would not catch it. Added
  `_uncited_provenance_problems`: every citation actually present in a
  reconciled section is checked, deterministically, against the set of
  citations literally present in the excerpts the call was given
  (`_citations_in`) -- a citation outside that set fails the attempt and
  triggers a retry, the same as any other `validate_doc` problem.
