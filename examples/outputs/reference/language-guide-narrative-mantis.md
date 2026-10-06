---
title: "mantis — language guide (narrative tier)"
doc_type: language-guide
system: "OE"
dialect: "mantis"
generated_by: legacy-functional-docs 0.2.0
generated_at: "2026-10-06"
review_status: draft
reviewers: []
confidence_summary:
  verified: 10
  inferred: 2
  unresolved: 0
sources: ["ORDENQ", "PRODSCHED"]
sme_questions: []
---

# mantis — language guide (narrative tier)

Mantis and Supra have no public documentation, so this guide is the closest
thing to a reference for how the dialect is used here. The counts and
examples come from the deterministic tier at
[`language-guide-mantis.md`](./language-guide-mantis.md), produced by
`mfdoc lang-guide --config project.yml --dialect mantis`.

## Structure / declarations

Declarations are typed and sized inline: `TEXT ORDER_NO(10)` [[ORDENQ:3]]
for text (10 uses) and `NUMERIC ORDER_WT(9.3)` [[ORDENQ:5]] for numbers
(3 uses). Names use underscores, in every cited declaration.

## Control flow

Plain assignment is the most common statement (7 uses), mostly feeding a
message field, as in `MSG = "Order number required"` [[ORDENQ:14]], directly
under an `IF` check [[ORDENQ:13]]. Multi-way branching uses `CASE` with
`WHEN` arms on a status field [[ORDENQ:27]] [[ORDENQ:28]], and the single
loop is a `WHILE` driven by a status variable [[ORDENQ:23]].

## Data access (DML)

Each verb appears once: `READM` [[ORDENQ:17]], `OBTAIN` with a `WHERE`
[[ORDENQ:22]], `RDNXT` to walk child rows [[ORDENQ:25]], `WRITM`
[[ORDENQ:33]] and `ADD-M` in the scheduler [[PRODSCHED:15]]. Reads precede
the write in the order-enquiry program, with `WRITM` appearing after the
read loop (inferred from line order [[ORDENQ:17]] [[ORDENQ:33]]). No entity
relationships are recorded, so none are claimed.

## Screen interaction

`SHOW` (4) and `CONVERSE` (3) both name a screen, for example
`SHOW ORDSCR1` [[ORDENQ:15]] and `CONVERSE ORDSCR1` [[ORDENQ:12]]
(inferred: `SHOW` displays output and `CONVERSE` waits for input, which the
table alone does not state).

## Transactions

`ENDTR` closes the unit of work [[ORDENQ:34]], immediately after the
`WRITM` [[ORDENQ:33]].

## Calling conventions

Modules include shared screens and code (7 `INCLUDE` rows) and make
external calls via `EXTERNAL "STEELLIB","PRICECALC"` [[ORDENQ:10]], which is
the one cited `CALL` shape.

## Not yet recognized

None recorded in the basic tier.
