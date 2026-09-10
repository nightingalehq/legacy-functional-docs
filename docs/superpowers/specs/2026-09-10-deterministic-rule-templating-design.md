# Design: deterministic rule-to-sentence templating for the most mechanical `rule_candidate` shapes

Date: 2026-09-10
Status: draft (design spike, no code)
Issue: nightingalehq/legacy-functional-docs#173 (part of #156)

## Problem

`mfdoc batch`'s narrative stage asks a model to write English prose for every
`rule_candidate` row in a chunk, regardless of how mechanical it is. A rule
like "when the grade code equals `'X9'`, set the validation return code to
`99`" carries exactly the same generation cost as a genuinely nuanced rule
that requires weighing several overlapping facts. If a meaningful fraction of
rows are that mechanical, rendering them deterministically instead of asking
a model to narrate them is a direct reduction in output-token volume — the
dominant cost driver per the 2026-09-08 token-cost report — independent of
every other fix in this epic (#131/#170/#171), which reduce *wasted*
regeneration rather than baseline generation volume.

This is a design spike per this repo's own "Planning an improvement or bug
fix" guidance in `CLAUDE.md`: it is the largest architectural change proposed
in the epic, and needs a design pass, with real measurement, before anyone
writes production code.

## Method: measuring against the bundled fixtures

There is no client data available to this repo, and none should ever be used
here (see `CLAUDE.md`'s client-content rule). The only "real" data available
is this repo's own bundled `examples/` fixtures, which `project.yml` already
points `mfdoc ingest`/`derive` at. Built the fact store fresh
(`mfdoc ingest --config project.yml && mfdoc derive --config project.yml`)
and queried `rule_candidate` directly (`SELECT * FROM rule_candidate JOIN
member ...`).

**This is a small sample from one illustrative fixture set (a handful of
Natural/Mantis members invented for this repo's own tests), not a
representative engagement codebase.** The fraction below should be read as
"what one small, deliberately varied fixture set looks like," not as a
number that transfers to a real codebase's actual mix of validation logic,
dispatch tables, and record-construction sequences. Treat every number in
this section as directional, not a sizing estimate to build a business case
on.

### The 59 rows, by shape

The fixtures currently derive 59 `rule_candidate` rows across 12 code
members. Classifying each by hand against the issue's own eligibility
criteria — single condition, single operator, single literal, a single
simple action, no cross-routine reference, and no ambiguity in what
business-first framing (`reference/writing-rules.md`) would say about it —
gives roughly this split:

| category | count | eligible? |
|---|---|---|
| single-condition-equals-literal → single MOVE/ASSIGN of a literal | 6 | yes |
| "no records found" guard → set return code → escape (idiom, 2-3 rows together) | 2 occurrences | yes, as a named idiom |
| unconditional single-literal assignment (no condition at all) | 4 | yes |
| single-literal `WHEN`/`CASE` dispatch branch → single assignment | 1 | yes |
| compound condition (`AND`/`OR` of two comparisons) | 2 | no — needs interpretation of the combination |
| threshold/arithmetic `WHEN` condition (e.g. a tolerance percentage) | 2 | no — needs interpretation of the expression's business meaning |
| default/`NONE` dispatch branch | 1 | borderline — framing as "otherwise" needs the sibling branches for context |
| dynamic call-target assignment (`MOVE 'X' TO #PGM` feeding a later call) | 1 | no — cross-routine by construction |
| multi-field record-construction sequence (5 sequential MOVEs building one record) | 5 (1 sequence) | no, individually — see below |
| control-flow headers with no business content (`WHILE`, `LOOP`, bare `CASE`/`DECIDE`) | 5 | no — not a business-rule sentence at all |
| data concatenation (`COMPRESS`) | 2 | no — output shape is free-form, not condition→action |
| accumulation (`ADD ... TO`) | 1 | no — needs interpretation of what's being accumulated and why |
| `IF` with a condition but no reconstructable single-statement body | 3 | ambiguous — excluded from both counts |
| IF/branch headers already counted as part of the guard-clause idiom above | (rolled up) | — |

Counting only the rows that unambiguously meet the strict criteria (rows 1,
3, and 4 in the table): **13 of 59 rows, ~22%.** Including the borderline
default-branch case and being generous about the record-construction
sequence (if a *sentence-per-field* template were built for it, which this
design does not propose — see Non-goals) pushes a generous upper bound to
roughly **a third of rows**. The remaining two-thirds need real
interpretation: compound conditions, threshold/arithmetic expressions,
dispatch defaults, cross-routine call-target assignments, and multi-fact
record construction, all of which is exactly the "does this line require a
judgement call about which of several overlapping facts to trust" test
`docs/superpowers/specs/2026-09-06-chunked-module-index-overview-design.md`
already uses to separate deterministic from synthesized content elsewhere
in this pipeline.

**Caveat repeated plainly: this is 59 rows from one small, curated fixture
set.** A real codebase with a lot of straightforward field validation
(mandatory-field checks, single-code status gates) could plausibly run
noticeably higher; one dominated by dispatch tables and multi-condition
business logic could run lower. The right next step, if this spec is
approved, is not to trust this number for a build/no-build decision on its
own — it's to build the smallest possible slice (see Recommendation) and
re-measure the eligible fraction against a real engagement's fact store
before investing further.

### A relevant existing precedent: `testplan.py`

`testplan.py`'s `build_member_test_cases` already derives `test_case` rows
deterministically from these same `rule_candidate` facts (`_is_branch_row`,
`_branch_body_lines`) — but it deliberately stops short of writing a
business-quality sentence: `then_json` carries the raw cited source excerpt,
not a paraphrase, precisely because "turning that excerpt into a concrete
assertion is the narrate stage's job" (the module's own docstring). That
split is the right precedent to reuse structurally (a deterministic pass
that recognizes eligible shapes from facts alone), but this design proposes
going one step further for the narrow eligible subset: producing the actual
business-first sentence, not just flagging the shape as templatable.

## Non-goals

- Not a change to what counts as a `rule_candidate` in the first place, and
  not a new fact-store table. The renderer reads `rule_candidate`,
  `routine`, and (for the guard-clause idiom) neighbouring rows in the same
  member — all already populated by `derive`.
- Not an attempt to template compound conditions, threshold/arithmetic
  expressions, dispatch defaults, cross-routine call-target assignments, or
  multi-field record-construction sequences in v1. Each of those is a
  plausible *future* template shape (the "as built" sections of prior specs
  in this directory show this codebase iterates that way), but attempting
  several shapes at once in one change makes both the measurement and the
  stylistic-seam risk (see Rollout/risk) harder to isolate if something goes
  wrong.
- Not a replacement for the model call on a chunk that has *any*
  non-eligible rule in it. See "Chunk/prompt interaction" below for why a
  chunk is never split mid-narration between "all-templated" and
  "all-model."
- Not a relaxation of `validate_doc`'s citation/hedge/reversed-condition
  checks for templated sentences. A templated sentence must pass the exact
  same validation any model-written sentence does — see "Interaction with
  `validate_doc`" below for why this should be true by construction, not by
  special-casing the check.

## Design

### Eligibility test (deterministic, no model call)

A `rule_candidate` row is v1-template-eligible when **all** of:

1. `construct == 'IF'` (or `'REJECT IF'`) with exactly one comparison in
   `condition` — no `AND`/`OR` conjunction present (a plain string scan, the
   same kind `conditions.py`'s `comparisons_in` already does for the
   reversed-condition check).
2. The comparison is `<field> <op> <literal>` — `condition`/`literals`
   already record this distinction; a `<field> <op> <field>` comparison
   (two variables) is excluded, since "the module compares X against Y" is
   inherently descriptive of *what* rather than *why*, and reliably framing
   the *why* in one templated clause is exactly the ambiguity v1 defers.
3. The row's reconstructed single-statement consequence (same
   `_branch_body_lines`-style reconstruction `testplan.py` already does,
   reused rather than reimplemented) is exactly one `MOVE`/`ASSIGN` of a
   single literal to a single field — no second consequence statement, no
   nested condition, no data access, no call.
4. The consequence's target field is not itself later read by a
   `call_edge`/`interaction` row as a dynamic call or screen target (the
   same "target name" facts `_statement_completeness_problems` already
   reads) — i.e. this assignment doesn't feed a cross-routine dispatch.
5. No paired `ELSE` with its own body needing separate narration in the
   same sentence (an `IF`/`ELSE` pair where *both* branches are
   single-literal-assignment is still v1-eligible, rendered as two
   templated sentences joined by "Otherwise" — see the sentence template
   below — but an `ELSE` with a compound or multi-statement body falls back
   to the model for the whole `IF`/`ELSE` pair, not just its own branch,
   since splitting one logical if/else into "templated true branch,Modelled
   false branch" would itself be an internal stylistic seam).

A second, narrower idiom is eligible without going through the general test
above: the guard-clause shape already named as its own `construct` variant
by `natural.py`/`mantis.py` — `IF NO RECORDS FOUND` (or an equivalent
existence-check header) immediately followed by exactly one `MOVE <literal>
TO <return/status field>` and an `ESCAPE`/terminal statement, with no
intervening statement. This is recognizable as a fixed three-row sequence
(condition row, assignment row, terminal row) rather than a single
`rule_candidate` row, which is why it is called out separately from the
general test above rather than folded into it.

Everything else — compound conditions, non-literal comparisons,
multi-statement consequences, dispatch branches, control-flow headers,
arithmetic — falls through to the model exactly as today. **The renderer's
job is only to say "yes, definitely eligible" or "no" — there is no
low-confidence middle tier that guesses.** A shape the eligibility test
can't positively confirm is, by definition, not eligible; see Rollout/risk
for why this binary framing (rather than a confidence score with a
threshold) is the right shape for a deterministic pass.

### Sentence template design

**Inputs** (all already-recorded facts, no new extraction): the row's
`condition` (field, operator, literal — already split apart in
`rule_candidate.condition`/`literals`/`fields_used`), the field/value pair
from its single consequence statement, the `[[MEMBER:LINE]]` citation for
the condition and for the consequence, the rule's `BR-nnn` ID
(`_rule_id`), and — critically for the "reads as business-first" test —
whichever field/lexicon entry (`options.narrative.lexicon`) has a business
term mapped to it. A template with no lexicon entry for either field still
renders, but with the raw field name only (see the fallback rule below).

**Output shape**, mirroring `reference/writing-rules.md`'s own "How to turn
a rule candidate into a documented rule" example almost exactly, because
that example already *is* this shape's ideal target sentence:

```
When {business term or field name} {is/equals/does not equal} {literal},
{business term or field name} is set to {literal} [[MEMBER:LINE-LINE]].
```

Concretely, for an invented example matching this repo's convention of
naming a mill-order-release scenario (no real system's names — see
Client-content check below):

> When the item grade is `'X9'`, the validation return code is set to `99`
> [[MMC0100:2-3]].

For the paired-IF/ELSE variant:

> When the item grade is `'X9'`, the validation return code is set to `99`
> [[MMC0100:2-3]]. Otherwise, the validation return code is set to `0`
> [[MMC0100:4-5]].

For the guard-clause idiom:

> If no matching order record is found for the given key, the module sets
> the return code to `10` and exits [[MMP0100:34-36]].

Every rendered sentence:

- **Leads with the business trigger and the resulting action**, per
  `writing-rules.md`'s "Business-first sentence framing" section — the
  template's fixed clause order (`When <condition> ... <action> ...`)
  encodes that rule structurally, so this cannot regress by omission the
  way a model's own choice of framing occasionally can.
- **Carries the citation immediately after the claim**, exactly as
  citation-format rules require, using the row's own `line_no`/`end_line`
  (and the consequence statement's line, if different) — never a
  whole-module `[[MEMBER]]` citation, since the fact is line-specific.
- **Uses the lexicon term when one exists** for either field, exactly the
  same `options.narrative.lexicon` lookup `module_brief`'s "Business
  vocabulary" section already performs, with the technical name alongside
  on first use per `writing-rules.md`'s "Naming and the lexicon" section.
  When no lexicon entry exists, the template falls back to the field name
  itself, lightly humanized (`ORDER-VIEW.ORDER-STATUS` → "the order status")
  by the same kind of mechanical de-hyphenation `structural.py`'s glossary
  renderer already does for its own entries — never an invented business
  gloss for a field the lexicon doesn't cover, since that would be exactly
  the kind of "inventing intent from a name" `writing-rules.md`'s "Prose
  failures to avoid" section already forbids.
- **Marks confidence exactly like a model-written sentence would.** A
  templated sentence is always `verified` — it asserts nothing beyond what
  the cited condition/action literally state — so it needs no `*(inferred)*`
  marker, and `confidence_summary`'s `verified` count in front matter is
  incremented for it exactly as for a model-written verified sentence (see
  "Chunk assembly and front matter" below).

**Quality-contract test**: is a rendered sentence indistinguishable from a
model-written one under `writing-rules.md`'s own contract? The template's
fixed shape passes the "read only the main clause" test
(`writing-rules.md`'s "Business-first sentence framing" section) by
construction — trigger and action always lead, field/literal/citation always
trail. It cannot produce the "statement-first" failure mode
(`writing-rules.md`'s own "before" example) because the template has no
slot for a bare statement description. It is intentionally *terser* than a
typical model sentence — no elaboration, no "which represents..." — which
is a deliberate design choice, not an oversight: elaboration beyond what the
cited fact states is exactly the inferred-content risk the eligibility test
exists to exclude in the first place. A short, correct, citation-anchored
sentence reads as a natural stylistic variation next to a model's longer
one, not as an obviously different origin — see Rollout/risk for how this
gets checked before shipping, not just asserted here.

### Chunk/prompt interaction

**Decision: the model always narrates the full rule set of whatever chunk
it is given, including eligible rows — the renderer never removes a rule
from the brief the model sees. Instead, `module_brief` marks each eligible
row's bullet in the "Candidate business rules" section with a pre-rendered
sentence and an explicit instruction to reuse it, and the assembly step
splices the model's non-eligible narration together with the template's own
output only for rows the model is told never to touch.**

Reasoning for this over the alternative (strip eligible rows from the brief
entirely and splice template output in after the fact, at pure sentence
level):

- **Narrative continuity.** A module doc's "Business rules" section is
  grouped by routine/decision (`writing-rules.md`, `module_brief`'s
  "Internal routines" instruction), often as connected prose, not a flat
  numbered list only. A model narrating rules 4-9 while rules 1-3 are
  silently missing from its context has no way to write a coherent
  transition sentence ("in addition to the grade check above, ...") — it
  would either invent a discontinuity or, worse, silently re-derive and
  re-narrate the same rule from the raw condition still visible elsewhere
  in the brief (rules can reference each other's fields), duplicating cost
  in the accidental case and producing two subtly different sentences for
  the same rule in the failure case.
- **Splicing at the section/paragraph level, not the individual-sentence
  level inside a paragraph the model also wrote content into**, avoids ever
  asking a diff-and-merge step to insert templated text into the middle of
  a model-authored paragraph without disturbing surrounding prose — a
  brittle operation `_render_module_index_doc`'s own reconciliation
  precedent deliberately avoids by keeping deterministic and synthesized
  content in cleanly separated sections, never interleaved sentence by
  sentence within one paragraph.
- **The instruction "reuse this sentence verbatim, do not rewrite it, just
  cite it as your own" is cheap to give and cheap for the model to follow**,
  and keeps the model's own output tokens down for exactly the eligible
  rows — the actual cost saving this design is chasing — without asking the
  renderer to reconstruct paragraph-level narrative flow after the fact.

Concretely, `module_brief`'s "Candidate business rules" section gains one
more annotation per eligible row (next to the existing `BR-nnn`/citation/
condition/literals bits already rendered):

```
- **MMC0100:BR-001** [[MMC0100:2]] depth 0 `IF` — condition:
  `#GRADE-CODE = 'X9'` — literals: `X9` — **template-eligible: use this
  sentence verbatim, citation included, do not rewrite or re-derive it:**
  "When the item grade is `'X9'`, the validation return code is set to
  `99` [[MMC0100:2-3]]."
```

`build_prompt`'s writing-rules text gains one short, standing instruction
(added once, not per-call): "Some rules in the brief above are marked
template-eligible with an exact sentence already provided. Copy that
sentence into the appropriate place in your output exactly as given —
citation, wording, and confidence — do not paraphrase, expand, or
re-derive it. Everything else in the document should read as naturally
alongside these sentences as around any other rule's; do not call out
which sentences were pre-supplied."

The model still produces one complete document per chunk, same
call-shape as today (`_generate_module_doc_from_brief` is unchanged
mechanically) — the saving comes from shorter *generation*, not a
different orchestration path: an eligible rule's sentence is already fully
specified in the prompt, so the model reliably reproduces roughly the same
few tokens instead of independently generating and re-deriving them, and
(secondarily) from a shorter necessary *reasoning* pass per eligible row.
This is a smaller, safer first step than removing eligible rows from
generation entirely; see Rollout/risk and Recommendation for why v1 should
not attempt the more aggressive "renderer assembles the document, model
never sees eligible rows at all" version yet.

### Interaction with `validate_doc`

A templated sentence is generated straight from cited facts already in the
fact store — the same reasoning
`docs/superpowers/specs/2026-09-06-chunked-module-index-overview-design.md`
uses for reconciled index-document content applies here even more directly,
since there is no reconciliation step at all, just direct substitution:

- Its citation is not invented — it is the row's own `line_no`/`end_line`,
  the same value `_cite`/`_rule_id` already compute. `validate_doc`'s
  citation-resolution check passes on it exactly as it would on any other
  citation to a real line.
- It asserts nothing beyond the literal condition/action — there is no
  inference step, so it can never trigger the reversed-condition check
  (`conditions.comparisons_in`/`prose_polarity`) incorrectly, *provided* the
  template's own polarity wording is fixed and correct once, at design
  time, for each recognized operator (`=`/`EQ` → "is"/"equals", `NE` →
  "does not equal", etc.) — this needs exactly the same care
  `conditions.py`'s existing polarity table already took, reused rather
  than reinvented.
- It should never itself be the *cause* of a validation failure. If it
  ever is (a bug in the eligibility test misclassifying a row, or a
  polarity-wording bug), that failure is far more informative appearing
  *before* any model call is made — see "Self-check" below.

**Self-check, not blind trust**: rather than assuming a templated sentence
is correct by construction and skipping validation for it, the renderer
should run `validate_doc`'s existing per-sentence checks (citation
resolution, reversed-condition, statement-completeness) against every
templated sentence *before* it is ever inserted into a brief or a prompt —
a fast, local, no-model check that catches an eligibility-test or
sentence-template bug immediately, at the source, rather than downstream
in a generated document days or weeks later. A templated sentence that
fails this self-check is not sent to the model as "template-eligible" at
all; the row silently falls back to ordinary model narration, and the
failure is logged (not raised) so a bug in the eligibility test degrades to
"no savings for this row," never to "a bad sentence reaches output."

This deliberately does **not** interact with the near-miss/targeted-patch
machinery from #131/#170/#171 (see next section) — a templated sentence
should never appear in `uncited_assertions` or any other near-miss list at
all, because it either passes its pre-insertion self-check (in which case
`validate_doc` on the assembled document also finds nothing wrong with it,
same citation, same text) or it never reached the document in the first
place.

### Relationship to #170 and #171 (read before implementing either)

Read `gh issue view 170`/`gh issue view 171` in full before touching
`batch.py`'s retry path — both are already-proposed, independent
generalizations of the near-miss/targeted-patch mechanism this design must
not duplicate or contradict:

- **#170** generalizes `_is_near_miss_uncited` to any `validate_doc` failure
  that names a specific, locatable sentence/citation (not just uncited
  assertions) — a reversed-condition flag, for instance. This design's
  templated sentences should, per the self-check above, essentially never
  produce such a flag; if one somehow does (a bug), #170's generalized
  near-miss patch is the right repair path for it — same as for any other
  sentence in the document, model-written or templated. This design adds
  nothing new to #170's mechanism; it just needs #170's check to not
  special-case templated sentences differently from any other sentence
  once they're in the document (there is no reason it should).
- **#171** adds a deterministic auto-citation pass before spending a model
  call on a near-miss: search the brief's own cited lines for one matching
  a flagged uncited sentence's key terms, splice the citation in code. This
  is architecturally adjacent to (but distinct from) the present design:
  #171 repairs a *missing* citation on a model-written sentence by finding
  a fact that already supports it; this design *prevents* the sentence
  (and its citation) from needing to be model-written at all, for the
  narrow eligible subset. They compose cleanly and independently — a
  document produced under this design still benefits from #171 for any
  uncited near-miss among its model-written (non-template) sentences,
  exactly as it would without this design.

No part of this design proposes changing `_is_near_miss_uncited`,
`build_uncited_patch_prompt`, or either of #170/#171's proposed mechanisms;
it only adds a pre-generation step that reduces how much of a chunk's
"Business rules" section the model needs to generate from scratch.

### Rollout/risk

**Stylistic seam risk.** The chief risk this design must guard against is a
reviewer being able to tell — or worse, being distracted by — which
sentences came from a template versus the model. Mitigations, in order of
how load-bearing they are:

1. The template's output shape is deliberately modelled on
   `writing-rules.md`'s own canonical "good" example, not invented
   independently — it should read as what the writing rules already ask
   for, not a separate style.
2. The model is told to write everything else "as naturally alongside
   these sentences as around any other rule's" — explicitly discouraging
   any meta-commentary that would flag a sentence as pre-supplied.
3. **Before shipping past the smallest slice (see Recommendation)**, run a
   blind read-through: take a handful of already-validated real module docs
   regenerated with templating on, and check informally whether a reviewer
   who didn't build this feature can reliably guess which sentences are
   templated. This is a qualitative gate, not a metric with a threshold —
   this spec does not propose a numeric target for it, since "a reviewer
   noticing" is itself the failure condition to avoid, and noticing is
   binary in practice, not a rate worth quantifying prematurely.
4. If (3) reveals a detectable seam, the fix is to adjust the template's
   wording (more variation in phrasing, e.g. rotating between "When X..."/
   "If X..."/"X triggers...") rather than expanding scope — a seam problem
   in a narrow v1 slice is a signal to fix the template, not to template
   more shapes and dilute the signal further.

**Fallback for low confidence.** As designed above, there is no
"low-confidence, do it anyway" path at all — the eligibility test is
binary (see "Eligibility test" above), and a row that doesn't pass it
outright, or that fails its own pre-insertion self-check, falls straight
back to ordinary model narration with zero special-casing downstream. This
is deliberately conservative: a false negative (an eligible-looking row
templated by hand would have worked, but the automated test declined it)
only costs the tokens this design was trying to save in the first place; a
false positive (a row wrongly templated) risks the exact trust erosion
`writing-rules.md`'s citation-format section already warns is "worse than
an obvious missing citation" for citations generally. Given that asymmetry,
this design intentionally trades some recall for near-zero false-positive
risk, rather than tuning a confidence threshold to balance the two.

**Rollout mechanics**, once built: a new `options.narrative` flag (e.g.
`template_eligible_rules: true`, default `false` until the blind
read-through in step 3 above has actually been run on real output) gates
whether `module_brief` ever annotates a row as template-eligible at all —
consistent with this repo's existing pattern of introducing a new
behaviour behind an opt-in `options.*` key
(`OPTION_SPECS`/`config_validate.py`) rather than changing default output
shape for every existing project the moment the code ships.

## Recommendation

**Worth building, but only the narrowest slice first, and only after this
spec's fixture-derived estimate is re-checked against at least one real
engagement's fact store before further investment.**

The smallest useful first slice:

- **Just the single-condition-equals-literal → single-MOVE/ASSIGN-of-a-
  literal shape** (rows 1 and 4 in the fixture table above — a plain `IF`,
  no `ELSE`, one consequence statement), **nothing else** — not the
  guard-clause idiom, not the `ELSE`-paired variant, not dispatch branches.
  This is the shape with the least ambiguity in every dimension that
  matters here: eligibility detection is simplest (no idiom-matching across
  rows, no branch-pairing), the sentence template has no "Otherwise" clause
  to get wrong, and the risk surface for the stylistic-seam concern is
  smallest since there's only one sentence shape to get consistently right
  before adding a second.
- Ship it behind the `options.narrative.template_eligible_rules` opt-in
  flag from day one, off by default.
- Do the blind read-through (Rollout/risk, step 3) against this slice
  alone, on whatever real project first opts in, before considering the
  guard-clause idiom or the `ELSE`-paired variant as v2 additions.
- Re-run the eligible-fraction measurement (this spec's method, but against
  that project's real fact store) once the flag has been used on a real
  engagement, and use *that* number — not this spec's fixture-derived
  ~22% — to decide whether investing in the next shape (guard-clause idiom,
  then `ELSE`-paired, then dispatch branches) is worth it.

If the real-engagement fraction for even this narrowest shape turns out to
be near zero, that is itself a useful, cheap result: it means the more
elaborate shapes deferred here (compound conditions, dispatch defaults,
record-construction sequences) are very unlikely to be worth the
considerably higher design/implementation cost each would carry, and this
whole line of investigation can be closed out rather than incrementally
expanded on a false premise.
