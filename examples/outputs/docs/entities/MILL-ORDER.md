---
title: "MILL-ORDER — Mill order master"
doc_type: data-entity
system: "MOM"
entity: "MILL-ORDER"
entity_kind: "ddm"
physical_ref: "DBID 012 FNR 045"
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 9
  inferred: 2
  unresolved: 3
sources:
  - MILL-ORDER
  - MMP0100
  - MMP9200
  - MMP9600
  - MMP9700
sme_questions:
  - "Is ORDER-CUST-KEY (the ORDER-NO/CUSTOMER-NO superdescriptor) actually used by any caller, or is it dead descriptor definition left over from a retired access path?"
  - "MMLDA01, the local data area MMP0100 includes alongside this DDM, was not supplied — does it redeclare or extend any of these fields locally?"
  - "What does an ORDER-STATUS other than CONF, RLSD or PART mean, and is there a defined status-code list anywhere outside the application code that tests these three literals?"
---

# MILL-ORDER — Mill order master

## What it holds

One record per mill order, keyed by `ORDER-NO` (format `A10`) [[MILL-ORDER:5]].
`ORDER-STATUS` carries the order's lifecycle state; the only values evidenced
anywhere in the supplied application code are `CONF` (confirmed) [[MMP0100:38]],
`RLSD` (released) [[MMP0100:54]] and `PART` (partially released) [[MMP0100:56]]
*(inferred from the business-vocabulary lexicon plus the branches that test and
set these literals — no other status value appears in the supplied source)*.

## Physical implementation

Defined in the Adabas DDM listing for DBID 012, FNR 045 [[MILL-ORDER:1]]. An FDT
report for the same physical file was also supplied and merges cleanly with this
DDM — no field-level disagreement between the two was found for this store.

## Fields

| Field | Short name | Format | Length | Key/index | Business meaning | Confidence | Citation |
|---|---|---|---|---|---|---|---|
| `ORDER-NO` | AA | A | 10 | DE | the order this record describes | verified | [[MILL-ORDER:5]] |
| `CUSTOMER-NO` | AB | A | 8 | DE, NU | the customer the order belongs to | inferred (field name only; no comment or validation rule confirms this) | [[MILL-ORDER:6]] |
| `ORDER-DETAIL` (group) | AC | — | — | — | container for grade/status/weight | verified (group) | [[MILL-ORDER:7]] |
| `ORDER-DETAIL.GRADE-CODE` | AD | A | 6 | DE | steel grade ordered, per `options.narrative.lexicon` | verified | [[MILL-ORDER:8]] |
| `ORDER-DETAIL.ORDER-STATUS` | AE | A | 4 | NU | order lifecycle status — see "What it holds" above | inferred (values, not just format) | [[MILL-ORDER:9]] |
| `ORDER-DETAIL.ORDER-WEIGHT` | AF | P | 9.3 | — | ordered weight, compared against available stock at release time [[MMP0100:53]] | verified | [[MILL-ORDER:10]] |
| `DUE-DATE` | AG | D | 6 | DE | *(not established from supplied source)* | unresolved | [[MILL-ORDER:11]] |
| `ROUTE-STEP` (multiple, `MU`) | AH | A | 12 | NU | *(not established from supplied source)* | unresolved | [[MILL-ORDER:12]] |
| `DELIVERY` (periodic group, `PE`) | AI | — | — | — | one entry per delivery against the order | inferred (from `PE` — periodic group is a one-to-many marker, not itself evidence of business meaning) | [[MILL-ORDER:13]] |
| `DELIVERY.DEL-DATE` | AJ | D | 6 | — | *(not established from supplied source)* | unresolved | [[MILL-ORDER:14]] |
| `DELIVERY.DEL-QTY` | AK | P | 7.3 | — | quantity delivered against this delivery entry, by name only | inferred | [[MILL-ORDER:15]] |
| `ORDER-CUST-KEY` (superdescriptor) | — | — | — | SUPER (ORDER-NO + CUSTOMER-NO) | combined order/customer lookup key | verified (definition); unresolved (whether any supplied code actually uses it) | [[MILL-ORDER:16]] |

`ROUTE-STEP` is a multiple-value field (`MU`) and `DELIVERY` is a periodic group
(`PE`) — both encode a one-to-many relationship inside this single physical file
that a relational target would need to model as child tables, not extra columns.

## Relationships

No declared Adabas coupling to another entity was found for `MILL-ORDER` in the
supplied source; `TEST-COUPLE`'s own `CROSS-REF` field claims a relationship in
the opposite direction (see `TEST-COUPLE`'s own entity doc) but nothing on this
side of that pair confirms or denies it.

## Which modules use it

| Module | Operations | Access path | Purpose | Citation |
|---|---|---|---|---|
| `MMP0100` | Read, Update | `FIND` via `WITH ORDER-NO = #ORDER-NO`, then update in place | look up the order to release, then write back its new status | [[MMP0100:33]], [[MMP0100:63]] |
| `MMP9200` | Read, Update | `FIND` via `WITH ORDER-STATUS = ...`, then `UPDATE (F1.)` | not documented beyond the access itself — see MMP9200's module doc | [[MMP9200:12]], [[MMP9200:13]] |
| `MMP9600` | Read | `FIND` via `WITH GRADE-CODE = ...` | not documented beyond the access itself — see MMP9600's module doc | [[MMP9600:8]] |
| `MMP9700` | Read | `FIND` via `WITH GRADE-CODE = ...` | not documented beyond the access itself — see MMP9700's module doc | [[MMP9700:7]] |

`MMP9200`, `MMP9600` and `MMP9700` are all orphan modules per the derived call
graph — no supplied JCL step, CICS transaction or program call reaches any of
them, so their access to `MILL-ORDER` is real (the code does it) but their own
invocation is unconfirmed. See the gap register.

## Data quality and integrity rules

- `ORDER-STATUS` is only ever set to `CONF`, `RLSD` or `PART` by the supplied
  application code [[MMP0100:38]], [[MMP0100:54]], [[MMP0100:56]] — no other
  value is evidenced, and no separate status-code list was supplied to confirm
  whether others are valid.
- `MMP9200` writes to `MILL-ORDER` with no explicit `END TRANSACTION`/`COMMIT`
  in its own source [[MMP9200]] — whether that write is safe without a caller
  or TP-monitor-level commit is unresolved (see the gap register).

## Gaps and questions for review

1. `ORDER-CUST-KEY`'s definition is present [[MILL-ORDER:16]] but no supplied
   caller was found to use it — is it dead, or does an unsupplied module rely
   on it for a customer-scoped order lookup?
2. `MMLDA01` — the local data area `MMP0100` includes alongside this DDM
   [[MMP0100:7]] — was not supplied; whether it redeclares or extends any of
   these fields locally is unresolved.
3. Whether values other than `CONF`/`RLSD`/`PART` are valid for `ORDER-STATUS`
   cannot be confirmed from the supplied source alone.
