---
title: "MMB0100 — Nightly mill order release"
doc_type: process
system: MOM
process_kind: batch
entry_point: MMB0100
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-05"
review_status: draft
reviewers: []
confidence_summary:
  verified: 7
  inferred: 1
  unresolved: 3
sources:
  - MMB0100
  - MMP0100
sme_questions:
  - "IDCAMS's SYSIN control cards were not supplied — what does STEEL.PROD.ORDER.BACKUP feed downstream, and who consumes it?"
  - "MMU0300 was not supplied — what does 'RELEASE' processing over the extract do?"
  - "STEP020 only runs if STEP010's condition code is < 4 (COND=(4,LT)) — what does STEP010 returning >= 4 mean for the business, and is skipping the backup/extract acceptable in that case?"
---

# MMB0100 — Nightly mill order release

## Business outcome

Mill orders confirmed during the day are released to production overnight, an
extract of the released orders is backed up, and a further "RELEASE" step runs
against that extract [[MMB0100:5-19]].

## Trigger

A job named `MMB0100`, submitted by a scheduler that was not supplied
[[MMB0100:1]] *(unresolved — scheduler-level dependencies, e.g. what upstream job
or time window triggers this, are not evidenced in the JCL itself)*.

## Steps

| # | Step | Module | What it does | Data affected | Citation |
|---|---|---|---|---|---|
| 1 | STEP010 | `MMP0100` | Runs the Natural batch program `MMP0100`, stacked on the `CMSYNIN` input after `LOGON MILLPROD` — the JCL names `NATBATCH` as the executed program, but that is the Natural batch driver, not the business logic; the program actually run is `MMP0100` | `MILL-ORDER`, `STOCK-BALANCE`, `ORDER-AUDIT` (per `MMP0100`'s own data access) | [[MMB0100:5]], [[MMB0100:10]] |
| 2 | STEP020 | `IDCAMS` | Copies `STEEL.PROD.ORDER.EXTRACT` to `STEEL.PROD.ORDER.BACKUP` via `REPRO`, but only runs if STEP010's condition code is less than 4 (`COND=(4,LT)`) — a business-relevant conditional skip, not incidental JCL plumbing | `STEEL.PROD.ORDER.EXTRACT` (read), `STEEL.PROD.ORDER.BACKUP` (created) | [[MMB0100:14]] |
| 3 | STEP030 | `MMU0300` | Runs `MMU0300` with `PARM='RELEASE'` against the extract; source not supplied, so what "RELEASE" processing does to the extract's contents is unresolved | `STEEL.PROD.ORDER.EXTRACT` (per `DISP=(NEW,CATLG,DELETE)` on `ORDEXT`, this step also creates/owns the extract's cataloguing) | [[MMB0100:19]] |

`REPRO`, `INDATASET` and `OUTDATASET` are IDCAMS control-statement keywords, not
program calls — they describe what STEP020 copies, not another module to document
[[MMB0100:15-16]].

## Flow

```mermaid
flowchart TD
    A[STEP010: MMP0100 via NATBATCH/CMSYNIN] -->|COND=(4,LT)| B{STEP010 return code < 4?}
    B -->|yes| C[STEP020: IDCAMS REPRO extract to backup]
    B -->|no, per COND| D[STEP020 skipped]
    C --> E[STEP030: MMU0300 PARM=RELEASE]
    D -.->|unresolved: does STEP030 still run?| E
```

The dotted branch is unresolved: `COND=(4,LT)` on STEP020 is evidenced
[[MMB0100:14]], but nothing in the supplied JCL states whether STEP030 has its own
condition tied to STEP010 or STEP020's outcome, so whether STEP030 still runs when
STEP020 is skipped is not determinable from this source.

## Data flow

- `STEEL.PROD.ORDER.EXTRACT` is read by STEP020 and referenced again by STEP030 via
  DD `ORDEXT` [[MMB0100:14]], [[MMB0100:20]] — the same dataset name links the two
  steps, though STEP030's `DISP=(NEW,CATLG,DELETE)` for that DD is inferred to mean
  something more specific about ownership/lifecycle that isn't determinable from
  the JCL alone *(inferred)*.
- `STEEL.PROD.ORDER.BACKUP` is created by STEP020 only [[MMB0100:14]]; no later step
  in this job reads it, so its consumer is outside this job *(unresolved)*.
- `MMP0100`'s own data effects (`MILL-ORDER` update, `ORDER-AUDIT` create) are
  documented separately in the MMP0100 module doc; this process doc does not repeat
  them beyond noting that STEP010 is where they happen.

## Failure and restart

Only STEP020's conditional logic is evidenced (`COND=(4,LT)`) [[MMB0100:14]]; no
other step-level restart or failure handling appears in the supplied JCL. What
happens on a failure within STEP010 (a Natural program abend, for example) is not
determinable from JCL alone and is not addressed here *(unresolved — see the
MMP0100 module doc's own "Transaction boundaries" section for what commits inside
that step)*.

## Gaps and questions for review

1. The scheduler-level trigger for this job (what submits it, and when) was not
   supplied [[MMB0100:1]].
2. `IDCAMS`'s `SYSIN` control cards define what STEP020 copies but not why, or who
   consumes `STEEL.PROD.ORDER.BACKUP` afterwards [[MMB0100:14-16]].
3. `MMU0300` (STEP030) was not supplied, so "RELEASE" processing against the extract
   is undocumented [[MMB0100:19]].
4. Whether STEP030 runs, and with what effect, when STEP020 is skipped by its
   `COND` is not determinable from this JCL alone.
