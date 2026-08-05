---
title: "ORDERMST — Order master (Mantis/Supra order enquiry)"
doc_type: data-entity
system: OE
entity: ORDERMST
entity_kind: supra_master
physical_ref: "schema STEELDB"
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-05"
review_status: draft
reviewers: []
confidence_summary:
  verified: 8
  inferred: 1
  unresolved: 3
sources:
  - ORDERMST
  - ORDLINE
  - ORDENQ
  - STEELDB
sme_questions:
  - "What was the export utility used to pull this library out of the DBMS, and is it known to be complete?"
  - "ORDSCR1 and ORDSCR2 (the screen definitions ORDENQ converses with) were not supplied — what fields do they show or capture?"
  - "PRICECALC is called with different argument orders at [[ORDENQ:8]] (EXTERNAL declaration) and [[ORDENQ:27]] (positional call) — which one is correct, and does the site's convention put the program name first?"
---

# ORDERMST — Order master (Mantis/Supra order enquiry)

## What it holds

One record per order, keyed by `ORDER-NO` [[STEELDB:6]]. `ORDER-STAT` carries a
status code, seen in source as `"CONF"` and `"HELD"` [[ORDENQ:26]], [[ORDENQ:28]];
no other values were found in the supplied source, so those are the only two
confirmed here *(unresolved — other status values may exist and simply weren't
exercised by this program)*.

## Physical implementation

Defined in the Supra directory export at [[STEELDB:3]], kind `supra_master`, schema
`STEELDB`. No FDT-equivalent listing was supplied for Supra in this export, so field
formats below come from the directory report alone — there is nothing to cross-check
them against.

## Fields

| Field | Short name | Format | Length | Key/index | Business meaning | Confidence | Citation |
|---|---|---|---|---|---|---|---|
| `ORDER-NO` | — | A | 10 | primary_key | the order this record describes | verified | [[STEELDB:6]] |
| `CUST-NO` | — | A | 8 | — | *(not established — no comment or validation rule in supplied source names the customer)* | unresolved | [[STEELDB:7]] |
| `ORDER-STAT` | — | A | 4 | — | order status; `CONF` = confirmed, `HELD` = held, per the CASE branches that test it | inferred | [[STEELDB:8]], [[ORDENQ:25-28]] |
| `ORDER-WT` | — | P | 9.3 | — | *(not established from supplied source)* | unresolved | [[STEELDB:9]] |

No business meaning is asserted for `CUST-NO` or `ORDER-WT` beyond format and
length: nothing in the supplied Mantis or Supra source comments on them, and the
field names alone are not evidence.

## Relationships

| Related store | Relationship | Implemented by | Citation |
|---|---|---|---|
| `ORDLINE` | one order to many order lines | Supra linkpath `ORDLNK` | [[STEELDB:19]] |

This is a declared linkpath, not one inferred from application code, so it should
survive a naive migration to a relational schema more reliably than a
code-enforced join would.

## Which modules use it

| Module | Operations | Access path | Purpose | Citation |
|---|---|---|---|---|
| `ORDENQ` | Read | `READM` keyed by `ORDER-NO` | initial lookup of the order to enquire on | [[ORDENQ:15]] |
| `ORDENQ` | Read | `OBTAIN` where `ORDER_NO = ORDER_NO` | re-fetch via the `ORDVIEW` view before the status branch | [[ORDENQ:20]] |
| `ORDENQ` | Update | `WRITM` keyed by `ORDER-NO` | write back after enquiry processing, inside a transaction ended at [[ORDENQ:32]] | [[ORDENQ:31]] |

## Data quality and integrity rules

- `ORDER-STAT` is tested against the literals `"CONF"` and `"HELD"` [[ORDENQ:26]],
  [[ORDENQ:28]] — these are the only two values this program's logic distinguishes;
  whether other values exist elsewhere in the system is unresolved.
- `ORDER_NO = " "` is treated as "no order number supplied" [[ORDENQ:11]], i.e. a
  blank is not a valid key value for this field in practice.

## Gaps and questions for review

1. `CUST-NO` and `ORDER-WT`'s business meaning is not established from the supplied
   source [[STEELDB:7]], [[STEELDB:9]].
2. The export came out of a utility (per the engagement's own framing of this
   source) — completeness of the Mantis/Supra source set, including whether any
   other order-status values exist, cannot be confirmed from what was supplied.
3. `ORDSCR1` and `ORDSCR2`, the screen definitions `ORDENQ` converses with, were not
   supplied [[ORDENQ:10]], [[ORDENQ:13]], [[ORDENQ:18]], [[ORDENQ:33]] — field-level
   validation and prompts shown to the user are undocumented.
4. `PRICECALC`'s argument order needs confirmation: it is declared `EXTERNAL` at
   [[ORDENQ:8]] and called positionally at [[ORDENQ:27]] with `(ORDER_NO, ORDER_WT)`;
   some Mantis installations list the program name as the first positional argument,
   which this source does not do, so the calling convention should be confirmed
   before relying on this call for anything beyond "an external price calculation
   happens here".
