---
title: "ORDENQ — Order enquiry and status-driven order maintenance"
doc_type: module
system: "OE"
module: "ORDENQ"
dialect: "mantis"
library: "STEELLIB"
object_type: "program"
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 20
  inferred: 3
  unresolved: 6
sources: ["ORDENQ"]
sme_questions:
  - "Neither ORDENQ's MAIN entry nor its VALIDATE_CREDIT_LIMIT entry is called from any JCL step, CICS transaction, or program in the supplied source — is either invoked dynamically or from a menu/scheduler definition not included in this extract?"
  - "PRICECALC is declared EXTERNAL in library STEELLIB but its source was not supplied — what does it do, and in what argument order does this site call it?"
  - "ORDSCR1 and ORDSCR2 map source was not supplied — what fields do they display/collect, and do they include anything beyond ORDER_NO and order status?"
  - "The header comment names this program 'ORDER ENQUIRY', but it also writes to ORDERMST and calls PRICECALC — is order maintenance (status/price update) an intended part of this program's job, or has it grown beyond its original enquiry-only scope?"
  - "VALIDATE_CREDIT_LIMIT (line 36) is never called from anywhere in the supplied source — what is meant to invoke it, and is it dead code in this extract or called from a module not supplied?"
  - "Is MSG's value actually rendered to the user as an error/warning on the re-shown ORDSCR1, or is it a general-purpose status line the screen always displays regardless?"
  - "What does a non-zero STATUS after the ORDERMST read (line 16) mean beyond 'the read did not succeed'?"
---

# ORDENQ — Order enquiry and status-driven order maintenance

## Purpose

The header comment describes ORDENQ as an "ORDER ENQUIRY" program *(unverified — header comments are treated as claims, not facts)* [[ORDENQ:1]]. Consistent with an enquiry function, the module retrieves an order and its order lines by order number [[ORDENQ:15]][[ORDENQ:20]][[ORDENQ:23]] and displays the result to the user via screen output [[ORDENQ:10]][[ORDENQ:13]][[ORDENQ:18]][[ORDENQ:33]]. However, the module also updates `ORDERMST` [[ORDENQ:31]] and calls an external pricing routine, `PRICECALC` [[ORDENQ:27]], when the order status is confirmed (`CONF` — confirmed) [[ORDENQ:26]]; this suggests the program's actual scope extends beyond a pure enquiry into order maintenance *(inferred)*. A second entry, `VALIDATE_CREDIT_LIMIT`, adds a credit-check rule on top of this (see Business rules) but is never called from anywhere in the supplied source. Whether the enquiry/maintenance mix is intentional or scope creep beyond the original "enquiry" purpose is a question for SME review (see Gaps).

## How it is invoked

Two entries exist in this member: `MAIN` [[ORDENQ:9]], the enquiry/maintenance flow described throughout this document, and `VALIDATE_CREDIT_LIMIT` [[ORDENQ:36]], a separate credit-check routine (see Business rules). Neither is referenced by any JCL step, CICS transaction, or program call in the supplied source [[ORDENQ]]. Either may be invoked dynamically, or started from a menu or scheduler definition that was not included in this extract; this cannot be determined from the inputs *(unresolved — needs SME confirmation of how and from where each entry is started)*.

## Inputs

| Name | Format | Source | Notes | Citation |
|---|---|---|---|---|
| Program level | `MAIN` | Program definition | ORDENQ's primary entry executes at top level (`MAIN`), not as a called subroutine | [[ORDENQ:9]] |
| `ORDER_NO` | User-entered | `ORDSCR1` screen (`CONVERSE`) | Order number the user wants to look up; the screen's other fields are not known because `ORDSCR1`'s source was not supplied *(unresolved)* | [[ORDENQ:10]] |
| `ORDER_WT`, `CUST_NO` | Program variables | Populated during `MAIN`'s processing / by the caller before `VALIDATE_CREDIT_LIMIT` runs | Read by `VALIDATE_CREDIT_LIMIT`'s credit-check rule (ORDENQ:BR-011); how `CUST_NO` is populated before that entry runs is not shown in the supplied source *(unresolved)* | [[ORDENQ:37]] |

## Data used

| Data store | Operations | Key / access path | Purpose | Citation |
|---|---|---|---|---|
| `ORDERMST` | Read (`READM`) | `ORDERMST, ORDER_NO` | Locates the order record for the entered order number | [[ORDENQ:15]] |
| `ORDERMST` (via `ORDVIEW`) | Read (`OBTAIN`) | `ORDVIEW WHERE ORDER_NO = ORDER_NO` | Retrieves the full order view, including `ORDVIEW.STATUS`, for display and status-based processing | [[ORDENQ:20]] |
| `ORDLINE` | Read (`RDNXT`) | `ORDLINE, ORDER_NO` | Reads the order's line items in sequence, one per loop iteration, accumulating their weight into `ORDER_WT` | [[ORDENQ:23]] |
| `ORDERMST` | Update (`WRITM`) | `ORDERMST, ORDER_NO` | Writes back to the order record; which fields are changed is not evidenced in the supplied source *(unresolved)* | [[ORDENQ:31]] |

## Business rules

1. **Order number required** (ORDENQ:BR-001, ORDENQ:BR-002) — Processing checks whether the entered order number is blank [[ORDENQ:11]]; when it is, the message `"Order number required"` is set and `ORDSCR1` is re-shown [[ORDENQ:12-13]]. No `EXIT`/`STOP` statement follows this check, so the `ORDERMST` read proceeds regardless of whether the order number was blank [[ORDENQ:11-15]].
2. **Read status check** (ORDENQ:BR-003, ORDENQ:BR-004) — After reading `ORDERMST`, the program checks whether the read status is non-zero [[ORDENQ:16]]; when it is, the message `"Order not found"` is set and `ORDSCR1` is re-shown [[ORDENQ:17-18]]. As with the blank-order-number check above, no `EXIT`/`STOP` follows, so processing continues into the `ORDVIEW` read regardless *(inferred — the specific meaning of each non-zero status value beyond "the read did not succeed" is not shown in the supplied source)*.
3. **Order line retrieval loop** (ORDENQ:BR-005, ORDENQ:BR-006) — Order lines are read from `ORDLINE` in a loop that continues while the read status remains zero [[ORDENQ:21]], i.e. for as long as further lines are successfully read *(inferred)*, using `RDNXT` to advance through the lines and accumulating each line's weight into `ORDER_WT` on every iteration [[ORDENQ:22-23]].
4. **Status-driven branching** (ORDENQ:BR-007) — Once the order is retrieved, processing branches on `ORDVIEW.STATUS` [[ORDENQ:25]].
   - **Confirmed orders** (ORDENQ:BR-008) — When the status is `"CONF"` (confirmed) [[ORDENQ:26]], the program calls `PRICECALC` with `ORDER_NO` and `ORDER_WT` [[ORDENQ:27]]. `PRICECALC`'s source was not supplied, so what it computes or changes cannot be confirmed from this module alone *(unresolved — see Gaps)*.
   - **Held orders** (ORDENQ:BR-009, ORDENQ:BR-010) — When the status is `"HELD"` [[ORDENQ:28]], the message `"Order is on credit hold"` is set [[ORDENQ:29]]; no further action for this branch (e.g. a `SHOW`) is evidenced in the supplied source *(unresolved — see Gaps)*.
5. **Credit-check threshold, in a separate entry point** (ORDENQ:BR-011, ORDENQ:BR-012) — `VALIDATE_CREDIT_LIMIT` [[ORDENQ:36-42]] checks whether the order weight exceeds 500 or the customer number is blank [[ORDENQ:37-38]]; when either is true, the message `"Credit check required"` is set and `ORDSCR1` is shown [[ORDENQ:39-40]]. This entry is never called from anywhere in the supplied source *(unresolved — see Gaps)* — whether it is invoked from another module, a screen-level validation hook, or is unreachable in this extract cannot be determined here.

## Processing sequence

`MAIN`:

1. The entry runs at `MAIN` level [[ORDENQ:9]].
2. `ORDSCR1` is displayed to the user in conversational mode to collect an order number [[ORDENQ:10]].
3. The program checks whether the entered order number is blank [[ORDENQ:11]]; if so, `"Order number required"` is set and `ORDSCR1` is re-shown, but processing continues regardless [[ORDENQ:12-14]].
4. `ORDERMST` is read by order number [[ORDENQ:15]].
5. The read status is checked for non-zero [[ORDENQ:16]]; if so, `"Order not found"` is set and `ORDSCR1` is re-shown, but processing continues regardless [[ORDENQ:17-19]].
6. The full order view is obtained via `ORDVIEW` for the order number [[ORDENQ:20]].
7. While the read status remains zero, the program loops [[ORDENQ:21]], accumulating each line's weight into `ORDER_WT` and reading successive `ORDLINE` records for the order [[ORDENQ:22-23]].
8. The program branches on `ORDVIEW.STATUS` [[ORDENQ:25]]: for `"CONF"` it calls `PRICECALC` [[ORDENQ:26-27]]; for `"HELD"` it sets `"Order is on credit hold"` [[ORDENQ:28-29]].
9. `ORDERMST` is updated [[ORDENQ:31]].
10. The unit of work is committed [[ORDENQ:32]].
11. `ORDSCR2` is displayed to the user in conversational mode [[ORDENQ:33]].

`VALIDATE_CREDIT_LIMIT` [[ORDENQ:36-42]] is a separate entry, not part of the sequence above — see Business rules (ORDENQ:BR-011, ORDENQ:BR-012) and Gaps for whether/how it is reached.

## Transaction boundaries

The program commits at [[ORDENQ:32]] (`ENDTR`). This is the only commit point found in the module, and it follows the `ORDERMST` update at [[ORDENQ:31]], so that write is included in the committed unit of work [[ORDENQ:31-32]]. `VALIDATE_CREDIT_LIMIT` [[ORDENQ:36-42]] contains no data-store writes and no commit of its own. Whether any other module invoked from here (e.g. `PRICECALC`) makes its own uncommitted changes cannot be determined, since its source was not supplied *(unresolved)*.

## Outputs and effects

- `ORDSCR1` is displayed to the user: once conversationally to collect the order number [[ORDENQ:10]], and as a non-conversational update after the blank-order-number check [[ORDENQ:13]] and after the non-zero read-status check [[ORDENQ:18]].
- `ORDSCR2` is displayed to the user conversationally at the end of `MAIN`'s processing [[ORDENQ:33]]. Its content was not supplied, so what it shows the user cannot be confirmed *(unresolved)*.
- `ORDERMST` is updated [[ORDENQ:31]].
- `PRICECALC`, an external program in library `STEELLIB` [[ORDENQ:8]], is called with `ORDER_NO` and `ORDER_WT` when the order is confirmed [[ORDENQ:27]]. Its effects are unknown because its source was not supplied *(unresolved)*.
- In `VALIDATE_CREDIT_LIMIT`, `MSG` is set to `"Credit check required"` and `ORDSCR1` is shown when the order weight exceeds 500 or the customer number is blank [[ORDENQ:37-40]]; since this entry is never called elsewhere in the supplied source, whether this effect is ever actually reached cannot be confirmed *(unresolved — see Gaps)*.

## Error handling

No `REINPUT` statement appears anywhere in the supplied source, so none of the four message-setting points below are hard validation stops — each just sets `MSG` and (except the `HELD` branch) re-shows `ORDSCR1`, with processing continuing afterward regardless of the outcome:

- Blank order number: `MSG = "Order number required"` [[ORDENQ:12]].
- Non-zero read status: `MSG = "Order not found"` [[ORDENQ:17]].
- `"HELD"` order status: `MSG = "Order is on credit hold"` [[ORDENQ:29]].
- In `VALIDATE_CREDIT_LIMIT`: order weight over 500 or blank customer number: `MSG = "Credit check required"` [[ORDENQ:39]].

Whether `MSG`'s value is actually rendered to the user as an error/warning on the re-shown `ORDSCR1`, or is a general-purpose status line the screen always displays, cannot be confirmed without `ORDSCR1`'s own source *(unresolved — see Gaps)*.

## Gaps and questions for review

- Neither `MAIN` nor `VALIDATE_CREDIT_LIMIT` is referenced by any JCL step, CICS transaction, or program call in the supplied source. Is either invoked dynamically, or started from a menu or scheduler definition not included in this extract? [[ORDENQ]]
- `PRICECALC` is declared `EXTERNAL` in library `STEELLIB` and called once, but its source was not supplied. What does it do, and in what argument order does this site call it? [[ORDENQ:8]]
- `ORDSCR1` (used three times) and `ORDSCR2` (used once) have no supplied source. What fields do these screens display or collect? [[ORDENQ:10]][[ORDENQ:33]]
- The header comment calls this an "order enquiry" program, but it also writes to `ORDERMST`, calls `PRICECALC`, and (via `VALIDATE_CREDIT_LIMIT`) enforces a credit-check rule. Is this maintenance/validation scope an intended part of ORDENQ's job? [[ORDENQ:1]][[ORDENQ:31]]
- `VALIDATE_CREDIT_LIMIT` [[ORDENQ:36]] is never called from anywhere in the supplied source. What is meant to invoke it, and is it dead code in this extract or called from a module not supplied?
- Is `MSG`'s value actually rendered to the user as an error/warning on the re-shown `ORDSCR1`, or is it a general-purpose status line the screen always displays regardless? [[ORDENQ:12]][[ORDENQ:17]][[ORDENQ:29]][[ORDENQ:39]]
- What does a non-zero `STATUS` after the `ORDERMST` read (line 16) mean beyond "the read did not succeed"? [[ORDENQ:16]]
