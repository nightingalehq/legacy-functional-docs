# Writing rules for the narrative pass

Read this before writing the first document. It defines the contract the validator
enforces and the prose failures it cannot catch.

## Contents

- [Citation format](#citation-format)
- [Stable rule IDs](#stable-rule-ids)
- [Confidence taxonomy](#confidence-taxonomy)
- [Front matter](#front-matter)
- [How to turn a rule candidate into a documented rule](#how-to-turn-a-rule-candidate-into-a-documented-rule)
- [Prose failures to avoid](#prose-failures-to-avoid)
- [Audience calibration](#audience-calibration)
- [Naming and the lexicon](#naming-and-the-lexicon)

## Citation format

```
[[MEMBER:LINE]]        single line
[[MEMBER:LINE-LINE]]   inclusive range
[[MEMBER]]             whole member, for statements about the module as a whole
```

`MEMBER` is the member name as it appears in the index, which is the name an SME
would type to list it on the mainframe. `LINE` is the ordinal within that member,
so a reviewer can open the member and count to it.

Put the citation immediately after the claim it supports, not at the end of the
paragraph. A reviewer checking one sentence should not have to work out which of
four citations covers it.

Do not cite a line that does not support the claim. A citation that resolves but
points at the wrong statement passes the validator and destroys trust the moment
someone checks it — worse than an obvious missing citation, because it is
invisible until it matters.

## Stable rule IDs

The brief assigns each candidate business rule an ID of the form `MEMBER:BR-nnn`
(e.g. `MMP0100:BR-003`), listed right before its citation. Copy it into the
"Business rules" section of the generated document, immediately after the rule's
own citation — do not invent one, renumber it, drop the member qualifier, or drop
the ID entirely.

The ID exists so a rule can be referred to on its own, system-wide — in a later
revision, in a gap-register conversation with an SME ("what does MMP0100:BR-003
mean by partial release?") — without spelling out the full citation every time, and
without the ambiguity a bare `BR-003` would have across a system with hundreds of
modules each numbering from 1. It is derived from the rule's position in the fact
store (member + source order), not written by the model, which is what makes it
stable: re-running the pipeline against unchanged source reproduces the same IDs.
Inserting a new rule earlier in the source will shift every later ID in that
module — the same trade-off any purely positional numbering makes, and not a reason
to invent a different scheme per document.

There is not yet a single document that lists every `BR-nnn` across the whole
system in one place — each module doc only shows its own. See
`docs/plans/legacy-functional-docs-plan.md` for the tracked follow-up (a
system-wide rules register) if that becomes a real need on an engagement.

## Confidence taxonomy

Every substantive claim is one of three things. Mark inferred and unresolved
claims inline; verified is the default and needs no marker.

**`verified`** — a direct consequence of cited source. "The module reads
`STOCK-BALANCE` by `GRADE-CODE` [[MMP0100:43]]" is verified: the statement is
there.

**`inferred`** — reasoning over cited source, where the reasoning could be wrong.
Mark it: *(inferred)*. "The tolerance check appears to allow release when
available stock is within 2.5% of the ordered weight *(inferred from
[[MMP0100:55]] — the literal is 2.50 and the expression divides by 100; confirm
the intended unit)*."

Business intent is almost always inferred. Code shows what happens, not why. "The
status is set to `PART` [[MMP0100:56]]" is verified; "which represents a partial
release awaiting further stock" is inferred unless a comment or an SME says so.

**`unresolved`** — needed for the documentation to be complete, not determinable
from the inputs. Say what is missing and what it blocks: "The set of programs
reachable from [[MMP0200:22]] cannot be determined, because the target is held in
`#PGM` and assigned at runtime *(unresolved — needs SME confirmation of which
programs are stacked here)*."

An `unresolved` marker is a success, not a failure. It converts an unknown into a
question somebody can answer.

## Front matter

Every generated document carries this. The validator rejects missing keys.

```yaml
---
title: MMP0100 — Mill order release to production
doc_type: module            # system-overview | data-entity | module | process | gap-register | coverage-report
system: MOM
module: MMP0100             # for doc_type: module
dialect: natural
library: MILLPROD
generated_by: legacy-functional-docs 0.1.0
generated_at: 2026-08-04
index_sha: 3f2a9c1          # first 7 chars of the ingest run's config hash
review_status: draft        # draft | in_review | sme_approved | signed_off
reviewers: []
confidence_summary:
  verified: 14
  inferred: 6
  unresolved: 3
sources:
  - MMP0100
  - MILL-ORDER
  - MMB0100
sme_questions:
  - "Is the 2.5% release tolerance still current business policy?"
  - "What is MMN0250 and is its source available?"
---
```

`confidence_summary` counts marked claims and must match the body. `sme_questions`
duplicates this module's items from the gap register so a reviewer working one
document at a time sees what to ask.

## How to turn a rule candidate into a documented rule

The brief gives exact conditions. Convert them to business language without
losing precision.

**Good:**
> An order is released in full when the total available stock at the requested
> plant is at least the ordered weight [[MMP0100:53]]. If available stock falls
> short but is within the 2.5% tolerance held in `#TOLERANCE-PCT`, the order is
> marked as a partial release and `MMN0250` is invoked
> [[MMP0100:55-57]]. Otherwise the module returns code 30 without changing the
> order [[MMP0100:58-60]]. *(The meaning of return code 30 is not defined in the
> supplied source — unresolved.)*

That works because it names the business outcome, keeps the threshold and the
status codes exact, cites each branch, and flags the one thing it does not know.

**Bad:**
> The system intelligently evaluates stock availability and releases orders
> according to business rules, with tolerance handling for edge cases.

No citation, no threshold, no status values, and "intelligently" is editorial. It
would survive review because it is unfalsifiable, which is precisely the problem.

**Also bad:**
> If `#AVAIL-TOTAL >= ORDER-VIEW.ORDER-WEIGHT` then `MOVE 'RLSD'` to
> `ORDER-VIEW.ORDER-STATUS` [[MMP0100:53-54]].

Correct and cited, but it is a transliteration. The reader could have read the
code. Functional documentation earns its keep by saying what the branch means for
the business.

## Prose failures to avoid

**Inventing intent from names.** A field called `PRIORITY-FLAG` may not drive
priority. Cite the code that uses it, or say the usage was not found.

**Smoothing over dead code.** If a branch is unreachable, say so and cite why.
Documenting it as live functionality is how impossible requirements reach a
migration project.

**Describing a module by its comments.** Header comments are frequently a decade
out of date. The brief marks them as unverified author prose. Use them as leads to
check, never as facts. Where a comment contradicts the code, document the code and
record the discrepancy — those are often the most valuable findings in the whole
exercise.

**Filling in a missing module.** When a `CALLNAT` target was not supplied, write
that the target is unavailable. Do not describe what a module named `MMN0900`
probably does.

**Silent aggregation.** "Several validation checks are performed" hides the
checks. List them, each cited, or state how many were found and that they are
enumerated in the module document.

**Implying a transaction boundary that is not there.** Where a module writes
without any commit, the brief flags it. Say that commit handling was not found in
this module and needs confirmation; do not write "changes are then committed".

## Audience calibration

The default audience is set in `options.narrative.audience`. For the common case —
a business analyst inheriting the system with no mainframe background — expand
jargon on first use in each document, since documents are read out of order:

- "a *descriptor* (an indexed field Adabas can search on)"
- "a *linkpath* (the Supra construct that connects a master record to its
  dependent records)"
- "*END TRANSACTION*, which commits all database changes made since the last
  commit point"

Do not expand it every time after that. Do not explain what a database is.

## Naming and the lexicon

Use the business term from `options.narrative.lexicon` where one exists, with the
technical name alongside on first use in each document: "the mill order
(`MILL-ORDER`)". This is what makes the documentation searchable by the people who
will maintain it.

The brief surfaces this for you: any lexicon entry whose technical term actually
appears somewhere in a given member's own facts shows up under "## Business
vocabulary" near the top of that member's brief, with the citation-free reasoning
already done — you don't need to cross-reference `project.yml` by hand, and neither
does `mfdoc batch`'s headless prompt, which has no other way to see it. Use the
term shown there verbatim; do not invent a different phrasing for the same entry.

When you find a term that ought to be in the lexicon, add it to the config rather
than deciding case by case — inconsistent vocabulary across a document set makes
it much harder to review, and reviewers notice.
