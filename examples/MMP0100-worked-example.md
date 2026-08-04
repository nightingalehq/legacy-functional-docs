---
title: "MMP0100 — Mill order release to production"
doc_type: module
system: MOM
module: MMP0100
dialect: natural
library: MILLPROD
object_type: subprogram
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-04"
review_status: draft
reviewers: []
confidence_summary:
  verified: 11
  inferred: 4
  unresolved: 3
sources:
  - MMP0100
  - MILL-ORDER
  - MMB0100
sme_questions:
  - "Is the 2.5% release tolerance still current business policy, and who owns it?"
  - "What do return codes 10, 20 and 30 mean to the calling program?"
  - "MMN0250 and MMN0900 were not supplied — what do they do?"
---

# MMP0100 — Mill order release to production

## Purpose

MMP0100 decides whether a single confirmed mill order can be released to production
at a given plant, based on the stock available for the order's steel grade. It
updates the order status to reflect a full release, a partial release, or no release,
and returns a numeric code to its caller [[MMP0100:9-11]].

The module header describes it as "mill order release to production" and records an
amendment in 2003 adding a grade substitution check [[MMP0100:2-4]]. No grade
substitution logic is present in the supplied source, so either the amendment was
later removed or it lives in one of the unsupplied called modules *(unresolved — see
gap register)*.

## How it is invoked

Invoked as a Natural subprogram, so it is always called rather than started
directly. The only invocation found in the supplied source is the nightly batch job
MMB0100, which stacks it on the Natural batch input after logging on to library
MILLPROD [[MMB0100:10]].

It takes an order number and a plant code and returns a code, which means one call
handles one order [[MMP0100:9-11]]. Batch release of many orders therefore requires a
driver that loops; no such driver was supplied *(unresolved)*.

## Inputs

| Name | Format | Source | Notes | Citation |
|---|---|---|---|---|
| `#ORDER-NO` | A10 | caller | the mill order to consider | [[MMP0100:9]] |
| `#PLANT` | A4 | caller | plant whose stock is assessed | [[MMP0100:10]] |
| `#RETURN-CODE` | N2 | returned | outcome code, set on every exit path | [[MMP0100:11]] |

## Data used

| Data store | Operations | Key / access path | Purpose | Citation |
|---|---|---|---|---|
| `MILL-ORDER` | Read, Update | `ORDER-NO` descriptor | retrieve the order, then write back its new status | [[MMP0100:33]], [[MMP0100:63]] |
| `STOCK-BALANCE` | Read | sequential by `GRADE-CODE`, starting at the order's grade | accumulate available stock for the grade at this plant | [[MMP0100:43]] |
| `ORDER-AUDIT` | Create | — | write an audit record after processing | [[MMP0100:71]] |

The stock read walks the file in grade order from the order's grade and stops at the
first record for a different grade [[MMP0100:44-45]], which is a range read over one
grade rather than a scan of the whole file.

## Business rules

1. **Only confirmed orders can be released.** An order whose status is not `CONF` is
   rejected with return code 20 and no changes are made [[MMP0100:38-40]].
2. **A missing order is rejected distinctly.** If no order matches the supplied
   number, return code 10 is set [[MMP0100:34-36]]. Codes 10 and 20 therefore
   distinguish "not found" from "found but not confirmed" *(inferred from the
   distinct codes — the caller's interpretation is unconfirmed)*.
3. **Only stock at the requested plant counts.** Stock records are accumulated only
   where the plant code matches `#PLANT` [[MMP0100:47-48]]. Stock of the same grade
   at other plants is ignored, so this is a plant-local release decision
   *(inferred)*.
4. **Full release when stock covers the order.** Where accumulated available weight
   is at least the ordered weight, status becomes `RLSD` [[MMP0100:53-54]].
5. **Partial release within a 2.5% tolerance.** Otherwise, where available weight is
   at least the ordered weight less a tolerance percentage, status becomes `PART` and
   `MMN0250` is called with the order number and the available total
   [[MMP0100:55-57]]. The tolerance is initialised to 2.50 and the expression divides
   by 100, so it reads as 2.5% [[MMP0100:29]] *(inferred — confirm the intended
   unit)*.
6. **No release otherwise.** Where neither condition holds, return code 30 is set and
   the order is left unchanged [[MMP0100:58-60]].
7. **The conditions are evaluated in order and only the first applies.** The
   construct is `DECIDE FOR FIRST CONDITION` [[MMP0100:52]], so an order that
   satisfies the full-release test never reaches the tolerance test.

## Processing sequence

1. Clear the return code [[MMP0100:31]].
2. Read the order by order number; exit with code 10 if absent [[MMP0100:33-36]].
3. Exit with code 20 unless the status is `CONF` [[MMP0100:38-40]].
4. Accumulate available stock for the order's grade at the requested plant
   [[MMP0100:43-50]].
5. Choose full release, partial release, or rejection [[MMP0100:52-61]].
6. Update the order and commit [[MMP0100:63-64]].
7. Call `MMN0900` [[MMP0100:67]], then write the audit record via the internal
   subroutine `WRITE-AUDIT` [[MMP0100:68]], [[MMP0100:70-73]].

## Transaction boundaries

Two units of work. The first commits the order status change, with the order number
recorded as restart data [[MMP0100:64]]. The second commits the audit record written
by `WRITE-AUDIT` [[MMP0100:72]].

Because these are separate commits, a failure between them leaves the order updated
with no audit record [[MMP0100:64]], [[MMP0100:72]] *(inferred from the two
boundaries — whether this is intended needs confirmation)*.

## Outputs and effects

- `MILL-ORDER` status updated to `RLSD` or `PART` [[MMP0100:63]]
- An `ORDER-AUDIT` record created [[MMP0100:71]]
- `#RETURN-CODE` set to 0, 10, 20 or 30 [[MMP0100:11]]
- `MMN0250` called on partial release [[MMP0100:57]] and `MMN0900` called after a
  successful update [[MMP0100:67]]; neither was supplied, so their effects are
  unknown *(unresolved)*

## Error handling

No `ON ERROR` block is present. Database or runtime errors are therefore handled by
the caller or the Natural runtime, not here [[MMP0100]]. The three rejection paths
are business outcomes rather than errors, and each returns a code rather than raising
one [[MMP0100:35]], [[MMP0100:39]], [[MMP0100:59]].

## Gaps and questions for review

1. Is the 2.5% release tolerance current business policy? It is compiled into the
   module as an initial value [[MMP0100:29]], so changing it requires a code change.
2. What do return codes 10, 20 and 30 mean to callers, and does any caller
   distinguish them?
3. What do `MMN0250` and `MMN0900` do? Source was not supplied
   [[MMP0100:57]], [[MMP0100:67]].
4. `MMLDA01` (a local data area) was not supplied [[MMP0100:7]], so fields it
   declares are undocumented.
5. `STOCK-BALANCE` and `ORDER-AUDIT` have no DDM or FDT in the supplied inputs, so
   their field-level meaning is undocumented.
6. The 2003 header amendment mentions a grade substitution check that is not present
   in this source [[MMP0100:4]]. Was it removed, or is it in an unsupplied module?
7. Is the split into two transactions intentional?
