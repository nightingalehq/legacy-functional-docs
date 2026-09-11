# Design: persistent narrative-memo cache — reusing an already-validated sentence for a structurally identical rule elsewhere

Date: 2026-09-10
Status: draft (design spike, no code)
Issue: nightingalehq/legacy-functional-docs#186 (part of #182)

## Why this is a spike, not a locked-in implementation ask

This is the highest-ceiling, highest-risk idea in this epic — reusing model
*output* across contexts, rather than deriving fresh prose from code (#173's
already-merged design spike, `2026-09-10-deterministic-rule-templating-design.md`)
or avoiding redundant computation (this epic's other issues, #170/#171).
Per `CLAUDE.md`'s "Planning an improvement or bug fix" guidance, this needs a
design pass before code, and the deliverable is this document, not code.

Read #173's spec before this one. It measured the same fixture set this
document measures, established the "does this require judgement about which
of several overlapping facts to trust" eligibility test this document reuses
verbatim, and its Recommendation ("narrowest slice first, re-measure before
further investment") is the template this document's own recommendation
follows.

## Problem

Mainframe codebases commonly repeat the same guard-clause/status-check/
validation idiom across many programs — "if the return code is not zero,
set an error status and stop" shows up in one shape or another in nearly
every batch program a shop has ever written. Today, `mfdoc batch` narrates
every occurrence independently: the model sees the same fact-store shape
over and over across a project's members and re-derives a fresh sentence
for it every time, even when an earlier occurrence's sentence — already
narrated, already cited, already validated — describes the exact same
idiom.

If a meaningful fraction of a real codebase's rules are structurally
repeated (plausible, unverified until measured against a real engagement's
fact store), reusing an already-validated sentence instead of asking the
model to write a new one again cuts both a generation call and its output
tokens for genuinely duplicate work. But the correctness risk is sharper
here than in #173: a *wrong* reused sentence is a silent documentation
error attributed to the wrong rule, exactly the failure mode this whole
tool exists to prevent, and (unlike a bug in an algorithmic template, which
is at least deterministic and reproducible) a bad cache hit is a *content*
error dressed up as a citation-clean sentence. This is not a design that
gets to trade recall for safety and call it done the way #173 can — it
needs a self-check discipline strong enough that a false-positive match is
caught before it ever reaches a document, or it should not ship at all.

## Method: measuring against the bundled fixtures

Same method and same caveat as #173's spec: there is no client data
available to this repo, and none should ever be used here (`CLAUDE.md`'s
client-content rule). The fact store was rebuilt fresh from this repo's own
bundled fixtures (`mfdoc ingest --config project.yml && mfdoc derive
--config project.yml`) and `rule_candidate` queried directly, joined
against `member` for the dialect/name. This is the same 59-row, 13-member
result #173's spec reports (unchanged fixtures, unchanged derive logic) —
re-run here specifically to look for *signature repetition*, not to
re-classify template-eligibility, which #173 already did.

**This is the same small, curated fixture set #173's spec caveated. The
findings below are not a sizing estimate; they are what this one small,
deliberately varied set happens to contain — read them the same way, as
directional, not as a business case.**

### What actually repeats

Two genuine shape repetitions turned up, and one important near-miss that
turns out to be the most useful finding in this document:

**1. The guard-clause idiom, twice, safely.** `MMP0100:34-36` (`IF NO
RECORDS FOUND` → `MOVE 10 TO #RETURN-CODE` → `ESCAPE ROUTINE`) and
`MMP0400:33-35` (the identical construct sequence, `MOVE 40 TO
#RETURN-CODE`) are structurally identical down to the target field — only
the literal return-code value differs. This is a real, clean repeat. It is
also — per #173's own spec — already fully covered by that design's second
eligibility path (the guard-clause idiom is one of #173's two v1-eligible
shapes, templated algorithmically with the literal substituted in). There
is nothing left for a narrative-memo cache to add here: the sentence is
already producible from facts alone, at zero model cost and zero
staleness/false-match risk, which is strictly better than caching a
model-written version of the same sentence.

**2. A genuine false-match risk, found in the fixtures themselves, not
hypothesized.** `ORDENQ:18` (`IF STATUS <> 0` → `ASSIGN MSG = "Order not
found"`) and `PRODSCHED:16` (`IF STATUS <> 0` → `ASSIGN MSG = "Schedule
could not be added"`) are two *different* Mantis members, in the same
project, each with a local field literally named `STATUS`, compared
against the same literal (`0`) with the same operator (`<>`), each
assigning a free-text string to a field literally named `MSG`. Under any
signature scheme loose enough to be useful — condition field name +
operator + a literal-shape class, action target field name + a
literal-shape class — these two rows are **signature-identical**. They are
also narratively unrelated: one is about a missing order record, the other
about a schedule that failed to save. A cache keyed on this signature would
confidently splice "the order was not found" onto a scheduling failure, or
vice versa, the first time either member was documented after the other —
a silent, confidently-cited, wrong sentence. Nothing about dialect,
member-qualification, or field-role normalization saves this pair: both
fields really are named `STATUS` and `MSG` in their own local scope: the
collision is not a normalization bug, it is what "the same bare field name
means something different in two different modules" looks like at the
fact-store level, in a project with only 13 members. See "Eligibility and
false-match risk" below for what this implies about which action shapes
can ever be safe to cache.

**3. A same-shape, same-risk sibling for a non-conditional action.**
`MMP9560:12` (`COMPRESS 'BATCH' #BATCH-SEQ INTO #MESSAGE`) and
`MMP9800:18` (`COMPRESS 'A' 'B' INTO #MESSAGE`) both build a `#MESSAGE`
field via `COMPRESS ... INTO`, with different source pieces. #173's own
classification already excludes `COMPRESS` from template-eligibility
("output shape is free-form, not condition→action") — the same reasoning
applies here even more directly: two `COMPRESS` statements with the same
target field prove nothing about what message either actually assembles.

**No genuine example of a judgement-heavy rule (a compound condition, a
threshold expression, a dispatch default) recurring with reusable prose
was found.** The two compound-condition rows in the fixtures (`MMP9000`'s
`AND`, `ORDENQ`'s `OR`) differ in every dimension — connective, field
types, resulting action — and the one threshold/arithmetic row
(`MMP0100:55`, the tolerance-percentage `WHEN`) has no sibling anywhere in
the set. This is the honest, small-sample-size result to report, in the
same spirit as #173's own caveat: this fixture set is simply too small and
too deliberately varied (each member exists to exercise a distinct defect
class, not to represent a real shop's repetition patterns) to say anything
about how often the *hard* rule shapes actually repeat in a real codebase.
What it *does* show, concretely, is that the shapes which recur most
readily in this set (a guard clause, a `COMPRESS`) are exactly the two
ends of the risk spectrum this design has to reason about: one already
safe by other means (#173), one demonstrably unsafe to cache at all.

## Non-goals

- Not a change to `rule_candidate` extraction, and not a re-litigation of
  #173's eligibility test — that test is reused here as the *first* gate
  (see "Relationship to #173" below), not reimplemented.
- Not a cross-project cache in v1 — see "v1 scope" below for why this is a
  much higher bar than project-local reuse, not a natural v2 extension to
  reach for quickly.
- Not a relaxation of `validate_doc`'s citation/hedge/reversed-condition
  checks, or of #171's conservative "no ambiguous match" discipline, for a
  memo-sourced sentence. A memo-sourced sentence must clear the exact same
  bar a model-written sentence does, checked twice (once before insertion,
  once by the ordinary `validate_doc` pass on the assembled document) —
  see "Self-check before splicing" below.
- Not a confidence-scored or "probably fine" cache. Per the false-match
  finding above, this design proposes no middle tier between "signature
  and every substitutable token match exactly, and the self-check passes"
  and "fall back to the model" — the same binary shape #173 chose for its
  own eligibility test, for the same reason: a graded threshold has no
  principled place to sit between a real match and a real miss when the
  cost of a wrong match is a silent content error.

## Design

### Canonical fact-signature scheme

A signature is computed for a `rule_candidate` row **only after #173's own
eligibility test has declined it** (see "Relationship to #173"). It has
four components, each dialect-normalized:

1. **Condition shape**: `(operator_class, literal_shape_class)` for the
   row's single comparison, when it has exactly one (a compound condition
   gets its own multi-clause signature, described below, not silently
   dropped to its first clause). `operator_class` reuses the same
   normalized set `conditions.py`'s polarity table already establishes
   (`eq`/`ne`/`ge`/`le`/`gt`/`lt`) so a Natural `NE` and a Mantis `<>`
   signature identically. `literal_shape_class` buckets the literal into
   one of a small, fixed set of shapes — **never the literal's own text**,
   since matching on the literal's actual value would only ever match a
   rule against its own earlier self, not a structurally similar sibling:
   - `short_code` — ≤ 8 characters, no lowercase letters, no embedded
     space, quoted or a bare unquoted number (`'X9'`, `'CONF'`, `10`,
     `40`) — the shape a status/return/flag code takes. Bucket order
     matters: this test is checked *before* `numeric` below, so any bare
     number of 8 characters or fewer is `short_code`, not `numeric` —
     the two buckets are mutually exclusive by length, not by whether the
     literal happens to be a number.
   - `numeric` — a bare number, no surrounding quotes, **longer than 8
     characters** (so it falls outside `short_code`'s length cap) — a
     value shaped like an amount or an identifier rather than a
     status/return/flag code.
   - `blank_or_empty` — an empty string or all-space literal (the
     "mandatory field not entered" idiom).
   - `free_text` — contains a lowercase letter and a space — a
     human-readable message, not a code.
2. **Condition field identity**: the field name itself, normalized only
   cosmetically (hyphen/underscore folded, a `VIEW-NAME.` qualifier
   stripped to the bare field, matching the same de-qualification
   `structural.py`'s glossary renderer already does) — **not** reduced to
   a role class (`conditions.OUTCOME_FIELD` or otherwise). The
   `ORDENQ`/`PRODSCHED` finding above is exactly why: two different local
   fields can share a role (both are "an outcome/status field") while
   meaning entirely different things, so collapsing to role would only
   make the false-match risk worse, not better. Field *name* identity is
   the strictest cheap signal this scheme has, and — as that same finding
   shows — it is still not sufficient on its own.
3. **Action shape**: the same `literal_shape_class` bucket (above) applied
   to the single consequence statement's assigned literal, plus the
   target field's normalized name, computed identically to (2).
4. **Construct class**: the row's `construct` value, plus, for a compound
   condition, the ordered sequence of `(operator_class, literal_shape_class,
   field_name)` triples joined by their connective (`AND`/`OR`) rather than
   a single triple — a compound condition's signature is its whole clause
   sequence, never a hash that could accidentally collide with a
   single-clause row's signature.

The **false-match risk gets worse, not better, as any of these components
is loosened**, and the fixture finding above shows the risk is real even at
the *strictest* useful combination (exact field names, not just roles):

- Loosening (2)/(3) from field-name identity to role class (e.g.
  "outcome-like field") reproduces the `ORDENQ`/`PRODSCHED` collision
  immediately — two unrelated `STATUS`/`MSG` pairs, same signature.
- Loosening the literal-shape buckets (e.g. merging `free_text` into
  `short_code`, or dropping shape entirely) only adds more collisions on
  top of that.
- **Tightening** further — for instance, requiring the condition and
  action field names to *also* appear together in the same lexicon entry,
  or requiring the two source members to share the same `library` — cuts
  the `ORDENQ`/`PRODSCHED` collision (they're in different Mantis members
  with no shared lexicon entry) but starts to look like re-deriving
  "these two rules are actually about the same business concept," which is
  precisely the judgement call a signature computed from bare facts,
  with no model call, cannot make reliably. A signature tight enough to
  avoid every false match this way stops being a *signature* and starts
  requiring the same interpretive step the cache exists to avoid asking
  the model to repeat.
- The one structurally safe narrowing this scheme's own finding actually
  supports: **exclude `free_text` from ever being an eligible literal
  shape, on either side.** A `free_text` literal (a human-readable message,
  not a code) is exactly the case where the literal's own content *is* the
  business meaning — there is no such thing as "the shape of this message
  matches that message" the way "both are short status codes" is a
  meaningful, safe similarity. This single exclusion removes both the
  `ORDENQ`/`PRODSCHED` collision and the `COMPRESS`-into-`#MESSAGE`
  near-miss from the eligible set entirely, at the cost of also removing
  the (plausible, in a real codebase) case of a literally-repeated
  user-facing error message idiom — which is an acceptable trade given the
  asymmetry: a false positive here is a wrong business explanation
  attributed to a real citation, worse than the tokens saved by a true
  positive.

### Storage design

A new fact-store table, `narrative_memo`, alongside `rule_candidate` rather
than folded into it (a memo is a cross-member fact about *reuse*, not a
per-row extraction result, and needs its own lifecycle — created after a
sentence validates, looked up before a different row is narrated, never
touched by `derive`):

```sql
CREATE TABLE IF NOT EXISTS narrative_memo (
    id                       INTEGER PRIMARY KEY,
    signature                TEXT NOT NULL,   -- canonical signature string, see above --
                                               -- dialect-normalized (operator_class unifies
                                               -- a Natural `NE` and a Mantis `<>`), so this
                                               -- is the sole lookup/uniqueness key by design:
                                               -- cross-dialect reuse *within* one project is
                                               -- intended, not an oversight -- see (1) above
    dialect                  TEXT NOT NULL,   -- informational only (the source row's own
                                               -- dialect, for debugging/audit) -- never part
                                               -- of the lookup key or the UNIQUE constraint,
                                               -- since the signature is already normalized
                                               -- across dialects on purpose
    sentence_template        TEXT NOT NULL,   -- validated sentence, substitutable
                                               -- tokens left as literal backtick- or
                                               -- quote-delimited spans (see below),
                                               -- not a separate placeholder syntax
    source_rule_candidate_id INTEGER NOT NULL REFERENCES rule_candidate(id),
    source_member            TEXT NOT NULL,
    source_citation          TEXT NOT NULL,   -- the [[MEMBER:LINE]] the template
                                               -- sentence originally validated with
    created_at               TEXT NOT NULL,
    hit_count                INTEGER NOT NULL DEFAULT 0,
    UNIQUE(signature)
);
```

A memo is *derived* from an already-validated sentence, not authored
separately: after `_generate_module_doc_from_brief` returns `ok=True` for a
chunk, a pass over that chunk's own body locates the one logical unit
(`_logical_units`, imported directly from `validate.py` the same way
`batch.py` already does) carrying each non-#173-eligible rule's own
citation, and — only for a rule whose signature isn't already in
`narrative_memo` — records it as a candidate template.

**Turning a freeform sentence into a substitutable template reuses #171's
own conservative convention rather than inventing a new one.** #171's
`_key_tokens` treats only backtick- or quote-delimited spans as "specific
things a sentence asserts" — a bare-word match is never trusted. This
design reuses exactly that convention in reverse: the only spans in a
newly-validated sentence eligible to become substitutable slots are the
backtick/quote-delimited tokens that also appear, verbatim, as the
`condition`/`literals` values on the source `rule_candidate` row itself
(the same literal-and-field values `module_brief` already rendered into
the brief that prompted the sentence in the first place). Everything else
in the sentence — the connecting prose, the business framing — is stored
as fixed text. A sentence whose model-chosen wording doesn't cleanly
isolate those tokens (paraphrased away, or split across a citation the
splice can't locate uniquely — the same condition `_splice_citation`
already checks for) simply never becomes a memo at all; nothing forces the
extraction, and a row that can't produce a clean template contributes zero
tokens to future runs, not a corrupted one.

At reuse time, substitution is the same operation `_splice_citation`
already performs for a citation: replace each identified slot's stored
value with the new occurrence's own `condition`/`literals`/field text and
its own `[[MEMBER:LINE]]` citation, verbatim, string-for-string — never a
re-derivation or a paraphrase of the new value.

### Self-check before splicing — plugging into the existing call/validate loop

**The mandatory rule: a memo match is never trusted on the strength of its
signature alone.** Before a memo's substituted sentence is ever surfaced
to the model (as a #173-style "reuse this, do not rewrite it" brief
annotation) or spliced directly into a document, the substituted candidate
must independently pass three checks, matching the same discipline #171's
near-miss flow already applies around a splice — #171's own
`_auto_cite_uncited_assertions` itself only splices a citation onto an
uncited assertion; the checks below are the ones the surrounding
`_generate_module_doc_from_brief` flow already runs (a fresh `validate_doc`
re-check on the assembled document after the splice, see "plugging into
the existing call/validate loop" below) — reused here as an *independent*
check on a memo candidate before it is ever surfaced, not attributed to
`_auto_cite_uncited_assertions` itself:

1. **Citation resolution** — the new `[[MEMBER:LINE]]` inserted by
   substitution must resolve against the fact store exactly as
   `validate_doc`'s own citation check requires (never assumed correct
   because the *template's* citation once resolved).
2. **Token-level re-confirmation, not signature re-confirmation** — every
   substitutable slot in the template must be found, verbatim, among the
   new row's own `condition`/`literals`/`fields_used` values (the same
   backtick/quote-delimited convention #171 already applies) before its
   value is trusted for substitution. This is a stricter, independent
   check than "the signature matched": a signature is a bucketed summary
   computed once; this check re-derives the actual overlap from the target
   row's own facts every time, the same defense-in-depth `_find_confident_
   citation` already applies to a citation splice.
3. **`validate_doc`'s reversed-condition and statement-completeness
   checks**, run locally against the *substituted* sentence exactly as
   they would against a model-written one — a template captured from a
   `NE` condition must not be reused, even after substitution, against a
   target row whose own condition polarity differs (this is why the
   signature's `operator_class` component exists at all, but the
   self-check re-verifies it rather than trusting the signature bucket).

**Concretely, in `_generate_module_doc_from_brief`'s own call shape**
(unchanged mechanically, same as #173 proposes): `module_brief` gains one
more annotation source, checked in this order for each rule row that
*isn't* #173-template-eligible:

1. Compute the row's signature.
2. Look up `narrative_memo` for that signature.
3. If found, substitute and run the three checks above, entirely in code,
   no model call.
4. If all three pass, annotate the brief bullet exactly the way #173
   annotates a template-eligible row — "memo-eligible: a previously
   validated sentence for this exact rule shape exists and has passed a
   local re-check; reuse it verbatim, citation included" — with the
   substituted sentence text inline.
5. If any check fails, the annotation is never added — logged at
   `logger.info`, never raised — and the row falls through to ordinary
   model narration with zero special-casing downstream, identical to how
   #173's own self-check degrades a bad template match to "no savings for
   this row" rather than "a bad sentence reaches output."

The assembled document then goes through the *existing*, entirely
unmodified `_generate_module_doc_from_brief` call → `validate_doc` →
`_is_near_miss` → `_auto_cite_uncited_assertions`/targeted-patch → retry
loop. A memo-sourced sentence that somehow still fails `validate_doc` on
the assembled document (the self-check above missed something) is just
another sentence to that loop — it can become a near-miss finding and get
patched or retried exactly like a model-written sentence would, with no
new code path. This is the same "self-check, not blind trust — but also
not a special downstream case" shape #173 already established for its own
templated sentences, applied here to a memo-sourced one instead.

**Writing a new memo is gated on real validation, not on a draft.** A
candidate template is only ever extracted from a chunk that
`_generate_module_doc_from_brief` already returned `ok=True` for — never
from an intermediate retry attempt's text, and never from a chunk that
needed the near-miss/targeted-patch path without arriving at a clean
`validate_doc` result first. This mirrors the same principle #171 already
applies to its own auto-cited splice (re-validate the *candidate* before
trusting it, never assume) at the write side of the cache instead of only
the read side.

### Relationship to #173 (read before implementing either)

**Disjoint by construction, checked in a fixed order, never both at
once:**

- #173's algorithmic eligibility test runs first, for every rule row,
  every time — it is cheaper (no lookup, no stored history needed) and it
  works on a project's very first `mfdoc batch` run, before any memo could
  possibly exist. A row #173 accepts is rendered by pure algorithm and
  never reaches the memo-lookup step at all.
- Only a row #173 *declines* is eligible for a memo lookup. The two
  mechanisms partition `rule_candidate` rows by what each needs: #173
  covers rows a pure algorithm can phrase correctly from facts alone, with
  no model ever in the loop for that sentence, on the very first
  occurrence; this design covers a row that genuinely needed a model's
  judgement once, where an identically-shaped row recurs later and that
  same judgement, once validated, is safe to reuse rather than re-ask.
- **Consequence, made explicit because it undercuts this design's own
  value case:** any shape #173 later extends to cover (the recommendation
  in #173's own spec names the guard-clause idiom, the `ELSE`-paired
  variant, and dispatch branches as plausible future v2/v3 additions)
  simply stops reaching the memo-lookup step the moment #173 covers it —
  and, per the "What actually repeats" findings above, the shapes that
  repeat *cleanly* in this fixture set are exactly the ones #173 already
  claims or is likely to claim next. This is not a coincidence to route
  around; it is evidence that the niche this design serves — a rule shape
  that both repeats structurally *and* still needs real interpretive
  prose rather than mechanical assembly — may be genuinely narrow. See
  Recommendation.
- A stale `narrative_memo` row (its signature no longer reachable because
  #173 grew to cover it) is harmless — it is simply never looked up again
  — not a correctness hazard, though worth pruning eventually so the table
  doesn't grow unboundedly across a long project history.

### v1 scope: project-local only, not cross-project

**Recommendation: project-local (one `.mfdoc/index.db`, one `mfdoc batch`
run's worth of history) only. Cross-project reuse is a substantially
higher bar this design does not recommend attempting even as a v2.**

Two independent reasons, not one:

1. **The signature scheme is already only marginally reliable within one
   project's own naming conventions** — the `ORDENQ`/`PRODSCHED`
   collision happened *inside* this single small fixture set, between two
   members that were never intended to represent the same system. Across
   genuinely different codebases (different shops, different naming
   conventions, no shared lexicon, no shared `library`/`system` grouping
   at all), the same bare field or literal shape is even less likely to
   denote the same concept — the signature would need to generalize over
   vocabulary and domain it has no way to know it's crossing, which is a
   categorically harder problem than the project-local false-match risk
   already documented above, not an incremental extension of it.
2. **A cross-project memo table is a client-data-handling problem, not
   just a correctness one.** A `narrative_memo` row stores actual narrated
   sentence text — lexicon terms, business framing, occasionally a
   near-paraphrase of what a specific client's code does — derived from
   one engagement's own source. `docs/guides/security-and-compliance.md`
   already treats far less (infrastructure layout inferable from a
   generated document) as something to reason about per engagement before
   it leaves a client's own workspace. A cache designed to travel between
   projects would mean one client's narrated prose (or its structural
   residue, which is often just as identifying) shaping another
   engagement's documentation — exactly the kind of cross-contamination
   `CLAUDE.md`'s client-content rule exists to prevent, just one layer
   removed from the repo itself. Project-local storage, living inside one
   project's own gitignored `.mfdoc/index.db` and never leaving that
   engagement, avoids this category of problem entirely rather than
   needing a redaction or scrubbing step to manage it.

## Rollout/risk

**The chief risk is the one the fixture measurement already surfaced: a
signature scheme loose enough to be useful is also loose enough to collide
on real, differently-meaning rules — and the collision found here needed
no adversarial construction, it fell out of thirteen ordinary fixture
members.** Every mitigation below is secondary to that finding, not a
replacement for taking it seriously:

1. Exclude `free_text`-shaped literals from eligibility on either side of
   a signature (see "Canonical fact-signature scheme" above) — this
   removes both collisions actually found in this measurement.
2. The mandatory, three-part self-check (citation resolution, token-level
   re-confirmation, `validate_doc`'s reversed-condition/completeness
   checks) runs on every substituted candidate before it is ever surfaced,
   never only on a sampled subset.
3. Project-local scope only (see above) — bounds how much unrelated
   vocabulary a signature could ever be compared against.
4. Same qualitative gate #173's own rollout section proposes: before
   shipping past the smallest slice, a blind read-through of real,
   memo-assisted documents by someone who didn't build this feature,
   checking specifically for a sentence that reads correctly on its own
   but describes the wrong module's actual situation — a failure mode a
   citation-resolution check cannot catch by itself, since the citation
   still resolves; only a human noticing the *content* is wrong for this
   specific module catches it.
5. `hit_count` on `narrative_memo` gives an operator a direct signal of
   how much a memo table is actually being used on a real project — a
   memo that never gets a second hit across a whole engagement is itself
   useful evidence that this mechanism isn't earning its complexity there,
   independent of any measurement this spec can do against fixtures.

**Fallback for an uncertain match:** identical in shape to #173's — there
is no partial-confidence tier. A signature miss, or any self-check
failure, falls straight back to ordinary model narration with zero
special-casing downstream. A false negative (a genuinely safe reuse
declined) costs only the tokens this design was trying to save; a false
positive costs exactly the kind of silent, confidently-cited content error
the `ORDENQ`/`PRODSCHED` finding demonstrates is not hypothetical.

## Recommendation

**Do not build the general narrative-memo cache. If anything is worth
building at all, it is a single, extremely narrow, project-local slice —
and this measurement gives good reason to expect even that slice's real
value to be small, which should be checked before investing further, not
assumed.**

Reasoning, laid out because it runs counter to the issue's own "if a
meaningful fraction of rules are structurally repeated" framing:

- The shapes that repeat *cleanly and safely* in the one real measurement
  available (the guard-clause idiom) are already fully served by #173's
  cheaper, zero-staleness-risk algorithmic templating — a cache adds
  nothing there but a second mechanism to maintain.
- The shapes that would need a cache (because #173's own eligibility test
  declines them — compound conditions, thresholds, dispatch defaults,
  free-form message construction) are, in this measurement, exactly the
  shapes whose repeated instances turned out to carry *different*
  business content behind an identical or near-identical fact-store shape
  — the `ORDENQ`/`PRODSCHED` and `COMPRESS` findings are both drawn from
  this bucket, not from the mechanical one.
- This leaves a narrow middle ground — a rule shape that (a) #173 cannot
  template algorithmically, (b) genuinely recurs with the *same* business
  meaning, not just the same surface shape, and (c) is common enough in a
  real codebase to be worth the design's own complexity and ongoing
  false-match vigilance — that this fixture set gives no evidence for one
  way or the other, because it is simply too small and too deliberately
  varied to contain a real instance of it.

If this is revisited, the smallest safe slice, matching #173's own
"narrowest slice" framing:

- Project-local only, from day one, with no cross-project path ever
  proposed.
- `free_text`-shaped literals excluded from eligibility entirely, on
  either side of the signature, from day one — not a v2 tightening.
- Gated behind its own `options.narrative` opt-in flag (e.g.
  `narrative_memo_cache: true`, default `false`), the same pattern #173
  proposes for itself and this repo's existing convention for any new
  behaviour that changes default output shape.
- Shipped only after a real engagement's fact store is measured the same
  way this document measured the fixtures — specifically checking not
  just "does the signature scheme find repeats" but "when it finds a
  repeat, do the two occurrences actually mean the same thing" — since
  this document's own measurement shows those are not the same question,
  and the second one is the only one that matters for whether this is
  safe to ship at all.
- If that real-engagement measurement shows the same pattern this
  fixture set showed — clean repeats already covered by #173, and
  everything else either not repeating or repeating with different
  content — that is itself a complete, useful answer, and this line of
  investigation should close rather than proceed to build the caching
  mechanism at all.
