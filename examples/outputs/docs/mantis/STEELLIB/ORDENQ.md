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
  verified: 13
  inferred: 5
  unresolved: 6
sources: ["ORDENQ"]
sme_questions:
  - "ORDENQ is not called from any JCL step, CICS transaction, or program in the supplied source — is it invoked dynamically or from a menu/scheduler definition not included in this extract?"
  - "PRICECALC is declared EXTERNAL in library STEELLIB but its source was not supplied — what does it do, and in what argument order does this site call it?"
  - "ORDSCR1 and ORDSCR2 map source was not supplied — what fields do they display/collect, and do they include anything beyond ORDER_NO and order status?"
  - "The header comment names this program 'ORDER ENQUIRY', but it also writes to ORDERMST and calls PRICECALC — is order maintenance (status/price update) an intended part of this program's job, or has it grown beyond its original enquiry-only scope?"
  - "What does the module do when ORDER_NO is left blank at entry (line 11), and what does a non-zero STATUS after the read (line 16) cause the program to do next?"
  - "What happens on the 'HELD' order status branch (line 28) — is any user message, hold-reason display, or further action expected?"
---

# ORDENQ — Order enquiry and status-driven order maintenance

## Purpose

The header comment describes ORDENQ as an "ORDER ENQUIRY" program *(unverified — header comments are treated as claims, not facts)* [[ORDENQ:1]]. Consistent with an enquiry function, the module retrieves an order and its order lines by order number [[ORDENQ:15]][[ORDENQ:20]][[ORDENQ:23]] and displays the result to the user via screen output [[ORDENQ:10]][[ORDENQ:13]][[ORDENQ:18]][[ORDENQ:33]]. However, the module also updates `ORDERMST` [[ORDENQ:31]] and calls an external pricing routine, `PRICECALC` [[ORDENQ:27]], when the order status is confirmed (`CONF` — confirmed) [[ORDENQ:26]]; this suggests the program's actual scope extends beyond a pure enquiry into order maintenance *(inferred)*. Whether this is intentional or scope creep beyond the original "enquiry" purpose is a question for SME review (see Gaps).

## How it is invoked

No JCL step, CICS transaction, or program call in the supplied source refers to ORDENQ [[ORDENQ]]. It may be invoked dynamically, or started from a menu or scheduler definition that was not included in this extract; this cannot be determined from the inputs *(unresolved — needs SME confirmation of how and from where ORDENQ is started)*.

## Inputs

| Name | Format | Source | Notes | Citation |
|---|---|---|---|---|
| Program level | `MAIN` | Program definition | ORDENQ executes as a top-level (`MAIN`) program, not a called subroutine | [[ORDENQ:9]] |
| `ORDER_NO` | User-entered | `ORDSCR1` screen (`CONVERSE`) | Order number the user wants to look up; the screen's other fields are not known because `ORDSCR1`'s source was not supplied *(unresolved)* | [[ORDENQ:10]] |

## Data used

| Data store | Operations | Key / access path | Purpose | Citation |
|---|---|---|---|---|
| `ORDERMST` | Read (`READM`) | `ORDERMST, ORDER_NO` | Locates the order record for the entered order number | [[ORDENQ:15]] |
| `ORDERMST` (via `ORDVIEW`) | Read (`OBTAIN`) | `ORDVIEW WHERE ORDER_NO = ORDER_NO` | Retrieves the full order view, including `ORDVIEW.STATUS`, for display and status-based processing | [[ORDENQ:20]] |
| `ORDLINE` | Read (`RDNXT`) | `ORDLINE, ORDER_NO` | Reads the order's line items in sequence, one per loop iteration, for display | [[ORDENQ:23]] |
| `ORDERMST` | Update (`WRITM`) | `ORDERMST, ORDER_NO` | Writes back to the order record; which fields are changed and under which status branch this occurs is not evidenced in the supplied source *(unresolved)* | [[ORDENQ:31]] |

## Business rules

1. **Order number required** (ORDENQ:BR-001) — Processing checks whether the entered order number is blank [[ORDENQ:11]]. What the program does when it is blank (e.g. re-prompt, error message) is not shown in the supplied source *(unresolved — see Gaps)*.
2. **Read status check** (ORDENQ:BR-002) — After reading `ORDERMST`, the program checks whether the read status is non-zero [[ORDENQ:16]]. A non-zero status conventionally indicates the read did not succeed (e.g. order not found) *(inferred — the specific meaning of each status value and the resulting action are not shown in the supplied source)*.
3. **Order line retrieval loop** (ORDENQ:BR-003) — Order lines are read from `ORDLINE` in a loop that continues while the read status remains zero [[ORDENQ:21]], i.e. for as long as further lines are successfully read *(inferred)*, using `RDNXT` to advance through the lines [[ORDENQ:23]].
4. **Status-driven branching** (ORDENQ:BR-004) — Once the order is retrieved, processing branches on `ORDVIEW.STATUS` [[ORDENQ:25]].
   - **Confirmed orders** (ORDENQ:BR-005) — When the status is `"CONF"` (confirmed) [[ORDENQ:26]], the program calls `PRICECALC` with `ORDER_NO` and `ORDER_WT` [[ORDENQ:27]]. `PRICECALC`'s source was not supplied, so what it computes or changes cannot be confirmed from this module alone *(unresolved — see Gaps)*.
   - **Held orders** (ORDENQ:BR-006) — When the status is `"HELD"` [[ORDENQ:28]], no further action for this branch is evidenced in the supplied source *(unresolved — see Gaps)*.

## Processing sequence

1. The program runs at `MAIN` level [[ORDENQ:9]].
2. `ORDSCR1` is displayed to the user in conversational mode to collect an order number [[ORDENQ:10]].
3. The program checks whether the entered order number is blank [[ORDENQ:11]].
4. `ORDERMST` is read by order number [[ORDENQ:15]].
5. The read status is checked for non-zero [[ORDENQ:16]].
6. `ORDSCR1` is shown (non-conversational update) [[ORDENQ:18]].
7. The full order view is obtained via `ORDVIEW` for the order number [[ORDENQ:20]].
8. While the read status remains zero, the program loops [[ORDENQ:21]], reading successive `ORDLINE` records for the order [[ORDENQ:23]].
9. The program branches on `ORDVIEW.STATUS` [[ORDENQ:25]]: for `"CONF"` it calls `PRICECALC` [[ORDENQ:26-27]]; for `"HELD"` no further evidenced action occurs [[ORDENQ:28]].
10. `ORDERMST` is updated [[ORDENQ:31]].
11. The unit of work is committed [[ORDENQ:32]].
12. `ORDSCR2` is displayed to the user in conversational mode [[ORDENQ:33]].

The exact conditional structure connecting steps 3–6 (what happens on each branch of the blank-order-number and read-status checks) is not fully evidenced in the supplied source *(unresolved)*.

## Transaction boundaries

The program commits at [[ORDENQ:32]] (`ENDTR`). This is the only commit point found in the module, and it follows the `ORDERMST` update at [[ORDENQ:31]], so that write is included in the committed unit of work [[ORDENQ:31-32]]. Whether any other module invoked from here (e.g. `PRICECALC`) makes its own uncommitted changes cannot be determined, since its source was not supplied *(unresolved)*.

## Outputs and effects

- `ORDSCR1` is displayed to the user, once conversationally to collect the order number [[ORDENQ:10]] and once as a non-conversational update [[ORDENQ:13]][[ORDENQ:18]].
- `ORDSCR2` is displayed to the user conversationally at the end of processing [[ORDENQ:33]]. Its content was not supplied, so what it shows the user cannot be confirmed *(unresolved)*.
- `ORDERMST` is updated [[ORDENQ:31]].
- `PRICECALC`, an external program in library `STEELLIB` [[ORDENQ:8]], is called with `ORDER_NO` and `ORDER_WT` when the order is confirmed [[ORDENQ:27]]. Its effects are unknown because its source was not supplied *(unresolved)*.

## Error handling

No `REINPUT` statement, error message text, or other user-visible validation message appears in the supplied source. The two checks that look like error/validation points — the blank order number check [[ORDENQ:11]] and the non-zero read status check [[ORDENQ:16]] — have no evidenced message or reject/retry action attached to them in this extract *(unresolved — see Gaps)*.

## Gaps and questions for review

- ORDENQ is not referenced by any JCL step, CICS transaction, or program call in the supplied source. Is it invoked dynamically, or started from a menu or scheduler definition not included in this extract? [[ORDENQ]]
- `PRICECALC` is declared `EXTERNAL` in library `STEELLIB` and called twice, but its source was not supplied. What does it do, and does the site call it with the program name first or last in the argument list? [[ORDENQ:8]]
- `ORDSCR1` (used three times) and `ORDSCR2` (used once) have no supplied source. What fields do these screens display or collect? [[ORDENQ:10]][[ORDENQ:33]]
- What action does the program take when the entered order number is blank? [[ORDENQ:11]]
- What does a non-zero status after the `ORDERMST` read (line 16) mean, and what does the program do in that case? [[ORDENQ:16]]
- What happens on the `"HELD"` order status branch — is there a message, a hold-reason display, or any other action? [[ORDENQ:28]]
- The header comment calls this an "order enquiry" program, but it also writes to `ORDERMST` and calls `PRICECALC`. Is order maintenance an intended part of ORDENQ's job? [[ORDENQ:1]][[ORDENQ:31]]