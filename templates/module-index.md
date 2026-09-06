---
title: "{MODULE} — module documentation, chunked"
doc_type: module_index
system: "{SYSTEM}"
module: "{MODULE}"
generated_by: legacy-functional-docs 0.1.0
generated_at: "{YYYY-MM-DD}"
review_status: draft
reviewers: []
confidence_summary:
  verified: 0
  inferred: 0
  unresolved: 0
sources: ["{MODULE}"]
sme_questions: []
---

# {MODULE} — module documentation, chunked

This document is a whole-module overview for a member whose business-rule set
was too large for one completion and was rendered as several independent
chunk documents instead (`{MODULE}.chunk1.md`, `{MODULE}.chunk2.md`, ...).
It exists so a reader has one place to start; it is not a substitute for the
chunk files, which still carry every per-rule detail.

This is a different document shape from `doc_type: module` (`templates/
module.md`) on purpose: a chunked member's index has no per-rule enumeration,
no full processing-sequence transliteration, and no per-branch error-handling
detail of its own -- those stay in the chunk files. What belongs here is
either a genuine whole-module synthesis (the five sections below, reconciled
once from every chunk's own already-validated statement of the same thing)
or a purely mechanical rollup of facts already recorded once (ranges, a
chunk-derived sequence skeleton, consolidated gaps).

## Purpose

One coherent statement of what this module does for the business, reconciled
across every chunk's own Purpose section rather than restated once per
chunk. Cite the statements that establish it, copying citations forward from
the chunks rather than inventing new ones. Where chunks disagree in scope or
emphasis, say so and mark the discrepancy *(unresolved)* rather than silently
picking one chunk's framing.

## How it is invoked

Who calls this module, with what, and from where -- reconciled the same way,
citing forward from whichever chunk(s) recorded the invocation facts.

## Inputs

Parameters and context-establishing reads, reconciled across chunks. A table
is fine if the chunks between them cover enough interface detail to make one
useful; a short paragraph is fine if they don't.

## Data used

One entry per data store this module as a whole touches, reconciled across
chunks -- say what each access is *for*, not merely that some chunk mentions
it.

## Business rules

Not a restatement of any chunk's own numbered rule list. This is a *map* of
what's where and why: this member's full `BR-nnn` range, broken into the same
sub-ranges the chunk boundaries already use, each labelled with the routine
name(s) (or "member main body") whose rules fall in it -- purely mechanical,
computed from facts already recorded when the chunks were built, never
model-authored. A reader who wants the actual rule text follows the range to
its chunk file.

```
- `MODULE:BR-001`..`MODULE:BR-055` -- `SESSION-INIT`
- `MODULE:BR-056`..`MODULE:BR-089` -- `VALIDATE-TAG`
```

## Processing sequence

One line per chunk, in execution order, naming the same range and routine
label as above from a "what happens in what order" angle -- a skeleton, not
a transliteration of any chunk's own detailed step-by-step sequence.

```
- chunk 1 (MODULE.chunk1.md): `BR-001`..`BR-055` -- `SESSION-INIT`
- chunk 2 (MODULE.chunk2.md): `BR-056`..`BR-089` -- `VALIDATE-TAG`
```

## Outputs and effects

Data written, messages shown, files produced, modules invoked with side
effects -- reconciled across chunks the same way Purpose is.

## Gaps and questions for review

Every gap-register entry recorded for this module, plus every chunk's own
`sme_questions`, deduplicated into one list -- so a reviewer sees the whole
module's open questions in one place instead of collating them from every
chunk file by hand. Purely aggregation; nothing here is synthesized.

## Chunk files

The mechanical list every chunked member's index has always had: each chunk
file, the `BR-nnn` range it covers, and whether it validated.

```
- [MODULE.chunk1.md](./MODULE.chunk1.md) -- rules 1-55 -- OK
- [MODULE.chunk2.md](./MODULE.chunk2.md) -- rules 56-89 -- OK
```
