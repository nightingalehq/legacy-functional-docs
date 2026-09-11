# Writing rules for the narrative pass

Read this before writing the first document. It defines the contract the validator
enforces and the prose failures it cannot catch.

## Contents

- [Citation format](#citation-format)
- [Stable rule IDs](#stable-rule-ids)
- [Confidence taxonomy](#confidence-taxonomy)
- [Front matter](#front-matter)
- [How to turn a rule candidate into a documented rule](#how-to-turn-a-rule-candidate-into-a-documented-rule)
- [Business-first sentence framing](#business-first-sentence-framing)
- [Prose failures to avoid](#prose-failures-to-avoid)
- [The reversed-condition check](#the-reversed-condition-check)
- [Audience calibration](#audience-calibration)
- [Naming and the lexicon](#naming-and-the-lexicon)
- [SME notes](#sme-notes)

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

## Business-first sentence framing

Citation discipline (above) says every claim needs a `[[MEMBER:LINE]]` citation.
It says nothing about what leads the sentence, and that matters just as much: a
rule sentence's main clause should state the business trigger and the resulting
action, with field names, literals, and the citation as supporting detail — not
as the sentence's subject. This does not relax the citation requirement at all;
every claim still needs its citation, exactly where it always went. It changes
only what the reader meets first.

The test is simple: read only the sentence's main clause (drop the field names,
literals, and citation) and ask whether a business analyst with no mainframe
background — this repo's own stated default audience — would understand what
happened and why, without reconstructing it from code shape. A sentence that
leads with a statement description ("the routine performs a FIND on...") fails
that test even when it is fully cited; the reader has to do the interpretation
work the documentation exists to do for them.

**Before** (statement-first — describes the code, leaves intent implicit):
> The routine performs a `FIND` on `SCHED-VIEW` with `SCHED-KEY` held equal to
> `'RESET'` [[MMP0100:40]], then moves blanks to `SCHED-VIEW.SCHED-STATUS` and
> `SCHED-VIEW.LAST-RUN-DATE` [[MMP0100:41-42]].

**After** (business-first — trigger and action lead, the same facts follow as
support):
> When a reset record exists for the schedule [[MMP0100:40]], the routine clears
> the schedule's tracking fields back to their initial state — status and last-run
> date are both blanked [[MMP0100:41-42]].

Both sentences cite the same lines and assert nothing the first doesn't. The
difference is only which fact leads: the first makes the reader infer that a
`FIND` returning a sentinel record means "this is a reset," and that blanking two
fields means "tracking resets to initial state"; the second states both outright
and lets the field names and citation confirm it, which is exactly the order a
functional-spec reader needs. This applies to "How it is invoked" narration too —
summarize what a caller checks/confirms and under what condition before it
reaches this member, not just that a call statement exists.

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

**Describing only one branch of an IF/ELSE.** When a rule candidate is `IF`
and the brief's "Candidate business rules" table marks its `notes` column
with `paired-else@[[...]]` (or the paired ELSE row itself with
`pairs-with-if@[[...]]`), document what happens on *both* branches, not just
the one that reads as interesting. It is easy to write up the error/
validation branch in detail and let the other branch's effects go
unmentioned; when that same `notes` column carries a `branch-access:...`
entry against the IF or ELSE row, every access it lists belongs in the
generated document, attributed to the branch that performs it -- not merged
into the surrounding narrative as if unconditional, and not dropped.

**Reproducing this brief's own table-escaping artifacts.** The brief's
tabular sections (Interface, Program variables, Outbound calls, Candidate
business rules, ...) render one fact per `|`-delimited row. Where a
condition, literal, or arg value itself contains a literal `\` or `|`
character, the brief escapes it (`\` -> `\\`, `|` -> `\|`) so the column
boundaries stay unambiguous -- that escaping is a rendering artifact of
*this brief*, not part of the actual source value. When quoting such a
value in the generated document, write the real, unescaped character (`|`,
`\`) the source actually contains, never the backslash-escaped form as it
appears in the brief's row.

**Inventing a not-found branch for FIND/READ/HISTOGRAM.** Statements inside a
`FIND`/`READ`/`HISTOGRAM` block run when a record is actually read or
matched. There is no implicit "not found" branch — a Natural database loop
that finds nothing simply skips its body and falls through past the
`END-FIND`/`END-READ`/`END-HISTOGRAM`, with nothing else needed to say so.
This is easy to get backwards for a single-record existence check (`FIND
(1) <view> WITH <key>` immediately followed by a small block of
assignments and no `IF`/`ELSE` anywhere): the assignment block is the
*found* branch, never a "default when not found." Only document a
not-found path when the source actually shows one — `IF NO RECORDS FOUND`
(its own `rule_candidate` row), a separate counter check after the loop, or
similar. The brief's "Data access" section marks a FIND/READ/HISTOGRAM's own
body extent explicitly (`found-body extent [[MEMBER:LINE-LINE]]`) whenever
its matching END- was found — treat that line range as the found branch, not
as something to infer from proximity to the following code.

**Blurring screen fields, program variables, and DB view fields together.**
The brief's "Program variables and screen/MAP fields" section (and "Data views
declared") tag every field's kind explicitly. Carry that into the Inputs and
Data used tables' Source column rather than listing every field the same way
-- "a screen field the operator enters" reads very differently from "a value
the program computes and holds only in memory", which is different again
from "a value read off a DB view". A field built by slicing another field
(e.g. a program variable assembled from a screen array field) needs both
halves named with their own kind, not just the result.

## The reversed-condition check

The validator does not just resolve citations to real lines — for a narrow
class of fields it also checks that a narrative sentence's *claimed*
comparison direction matches what the cited condition actually says. A
citation can point at a perfectly real line and still narrate the logical
inverse of it (a reversed comparison on a status/return-code field silently
swaps a documented pass/fail interpretation), and this needs no model call to
catch: the operator is already sitting in `rule_candidate.condition` as plain
text.

This only fires for **outcome fields** — fields whose name matches
`conditions.OUTCOME_FIELD` (`RETURN-CODE`, `RESP(ONSE)-CODE`, `RET-CODE`,
`ERROR-CODE`, bare `RC`, `STATUS`, `STAT`, `FLAG`, case-insensitive), or a
project-supplied replacement via `options.validate.outcome_field_pattern` in
`project.yml`. It is deliberately narrow: a false positive on an unrelated
field costs more reviewer trust than a missed reversal elsewhere would.

`conditions.comparisons_in` extracts every outcome-field comparison from a raw
condition string — a plain scan over the string (not a boolean-structure
parse), so it finds every comparison in a compound `AND`/`OR` condition without
attempting to model how they combine. Two shapes are recognised:
`<outcome-field> <op> <literal>` and `<outcome-field> <op> <other-field>` (the
latter checked against the *other field's own name* appearing in prose, since
there's no concrete value to search for). `conditions.prose_polarity` then
reads the direction a narrative sentence claims about that same
literal/field, by scanning a 40-character window on either side of its first
appearance for relational wording (`at least`/`no less than` → `ge`, `at
most`/`no more than` → `le`, `greater than`/`exceeds` → `gt`, `less than`/`fewer
than` → `lt`) or a negation marker (`not`, `isn't`, `unless`, `other than`,
`differs from`, `NE`, `<>`, `!=`, etc. → `ne`), falling back to plain equality
(`eq`) when nothing of the sort is found. Each side of that window is clipped
at the nearest `AND`/`OR` conjunction first, so a hedge word belonging to the
*other* clause of a compound condition ("STAT='FAIL' AND OBS_COUNT>ZERO",
narrated as "STAT equals 'FAIL' and at least one observation was counted")
can't be misread as modifying this clause's literal instead.

A citation range spanning a paired `IF`/`ELSE` (a `rule_candidate` row with
`construct='ELSE'` and a `pair_line_no` pointing back at its `IF`) is resolved
against **whichever branch the sentence's own wording indicates** —
`SUCCESS_WORDS` ("success", "succeed(s)/(ed)") versus `FAILURE_WORDS`
("fail(ure/ed/s)", "error", "reject(ed/s)", "backout", "abort(ed/s/ion)",
"unsuccessful"). A sentence with a success hint and no failure hint is checked
against the `ELSE` branch's condition (the `IF`'s condition, logically
inverted via `conditions.invert`); a failure hint, or no `ELSE` comparisons at
all, checks against the `IF`'s own condition. With **no hint either way**, only
the `IF`'s own condition is checked — guessing which branch an ambiguous
sentence means is worse than not checking it at all. A sentence citing more
than one location is skipped entirely, since it is usually deliberately
cross-referencing two conditions rather than narrating one.

**Wrong** (reads as claiming equality, but the source condition is an
inequality):

> If the return code equals `'0000'`, the update did not complete
> [[MMP0100:60]].

where `[[MMP0100:60]]` cites `IF #RETURN-CODE NE '0000'` — the sentence claims
`eq`, the source means `ne`. The validator flags this as
`comparison direction may be reversed`.

**Correct:**

> If the return code does not equal `'0000'`, the update did not complete
> [[MMP0100:60]].

now matching the cited condition's `NE` polarity. The same reversal risk
applies to relational wording — narrating a `<=` condition as "at least" when
the source says "at most" (or vice versa) trips the same check.

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

## SME notes

A brief may carry a "## SME notes (human-provided context, not verified against
source)" section near the end, sourced from an optional `sme-notes.md` file an SME
maintains alongside the project (see `options.sme_notes` in `project.yml`). Use it
to inform interpretation and emphasis — which rules matter most, a piece of
domain context that makes an otherwise-dry condition make sense, a known gotcha to
phrase carefully.

It is never itself a citable source and never overrides what the fact store says.
Every business-rule claim in the generated document must still carry its own
`[[MEMBER:LINE]]` citation from the cited sections above, exactly as if the SME
notes section didn't exist — do not cite the SME notes section, do not treat it
as confirming or superseding a fact, and do not let it justify dropping a
citation you'd otherwise need. If an SME note contradicts what the cited facts
show, write the cited facts and raise the discrepancy as a gap-register question;
never quietly prefer the SME's prose over the source.
