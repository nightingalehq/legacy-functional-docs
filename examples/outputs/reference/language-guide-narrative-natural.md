---
title: "natural — language guide (narrative tier)"
doc_type: language-guide
system: "MOM"
dialect: "natural"
generated_by: legacy-functional-docs 0.2.0
generated_at: "2026-10-06"
review_status: draft
reviewers: []
confidence_summary:
  verified: 14
  inferred: 3
  unresolved: 1
sources: ["MMP0100", "MMP0200", "MMP0400", "MMC0100", "MMM0150"]
sme_questions:
  - "Are the three `DOEND` lines and the stray `SETD. FROBNICATE` line in the unrecognised appendix genuine constructs or leftovers from an older release of the language?"
---

# natural — language guide (narrative tier)

The counts, keywords and cited examples come from the deterministic tier at
[`language-guide-natural.md`](./language-guide-natural.md), produced by
`mfdoc lang-guide --config project.yml --dialect natural`. The prose below
describes how those constructs combine in this sample codebase; where a
pattern is not backed by a cited row it is hedged or raised as a question.

## Structure / declarations

Alphanumeric variables dominate (19 `A` declarations, against 9 `N`), for
example `1 #ORDER-NO (A10)` [[MMM0150:8]]. Numeric fields are short integers
such as `1 #RETURN-CODE (N2)` [[MMP0100:11]], while packed decimals appear
once, for a weight total [[MMP0100:27]]. Local working variables consistently
carry a `#` prefix in every cited declaration [[MMM0150:8]] [[MMP0100:11]]
[[MMP0400:25]].

## Control flow

Plain `MOVE` is the most frequent control-flow construct (16 uses), and it
is used to set return codes and flags, for instance
`MOVE 99 TO #VALIDATION-RC` [[MMC0100:3]]. Branching is mostly a simple `IF`
[[MMC0100:2]]; the one multi-way decision uses `DECIDE FOR FIRST CONDITION`
with `WHEN` branches comparing a running total against an order weight
[[MMP0100:52]] [[MMP0100:53]]. Early exit is expressed with `ESCAPE ROUTINE`
(4 uses) [[MMP0100:36]] and, once, `ESCAPE BOTTOM` inside a read loop
[[MMP0100:45]]. `ON ERROR` appears once, in the certificate program
[[MMP0200:24]], and `REJECT IF` once, for a held-order check [[MMP0400:37]].

## Data access (DML)

The recurring idiom is find-then-update: an order is located with `FIND`
[[MMP0100:33]], changed with `UPDATE` [[MMP0100:63]] and an audit record is
written with `STORE` [[MMP0100:71]]. Stock is read with a sorted `READ`
[[MMP0100:43]]. Deletion is rare (one `DELETE`) [[MMP9200:16]]. The entity
relationship table is empty, so no relationship idiom is claimed here.

## Screen interaction

Maps declare text and field positions side by side [[MMM0150:13]]
[[MMM0150:14]]. Programs prompt with `INPUT USING MAP` [[MMP0200:11]], and
reject bad input with `REINPUT` plus a message [[MMP0200:13]] (inferred:
this makes `REINPUT` the validation-failure path for screens).

## Transactions

`END TRANSACTION` appears three times, and in the cited order-release
example it follows the `UPDATE` of the order [[MMP0100:63]] [[MMP0100:64]]
(inferred: the commit lands directly after the change it protects).

## Calling conventions

Static `CALLNAT` is the main way modules call each other (4 uses), passing
the order number and a return code [[MMP0100:57]]. Shared data comes in
through `LOCAL USING` includes [[MMP0100:7]]. Non-Natural calls are rare:
a single external `CALL` [[MMP0200:23]], and internal subroutines are invoked
with `PERFORM` [[MMP0100:68]]. `FETCH RETURN` appears once [[MMP0200:22]]
(inferred: dynamic program transfer is the exception, not the norm).

## Not yet recognized

Three shapes are not yet matched: `DOEND` (2), `SETD.` (1) and a bare
`LOOP` (1), per the basic tier's appendix. No pattern is claimed for them;
see the SME question above.
