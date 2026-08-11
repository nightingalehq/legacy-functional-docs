---
title: "Mill Order Management — functional overview"
doc_type: system-overview
system: "MOM"
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 9
  inferred: 2
  unresolved: 4
sources:
  - MMP0100
  - MMP0200
  - MMB0100
  - STEEL
  - ORDENQ
  - STEELDB
sme_questions:
  - "Nine of twelve batchable modules (MMP9000/9100/9200/9300/9400/9500/9600/9700, ORDENQ) have no supplied JCL step, CICS transaction or scheduler entry pointing at them at all — are these genuinely live, invoked by something outside the supplied source (a menu system, a scheduler, dynamic dispatch), or dead code left over from an earlier design?"
  - "MILL-CERT (referenced by MMP0200) and MILL_CERT (defined by the CERTS DDL) look like the same physical entity under two different naming conventions -- is that actually the case, and if so, should the application code's naming or the DDL's naming be treated as authoritative going forward?"
  - "Is the Mill Order Management system (MOM, Natural/Adabas/JCL/CICS) meant to be one system with Order Enquiry (OE, Mantis/Supra), or are these two genuinely separate applications that happen to share this codebase export?"
---

# Mill Order Management — functional overview

## What the system does

Two related but structurally distinct applications are present in the ingested
source. The **Mill Order Management (MOM)** side, in Natural/Adabas plus batch
JCL and a CICS front end, confirms and releases mill orders against available
steel stock [[MMP0100:52-61]], issues mill certificates on enquiry
[[MMP0200:15-19]], and runs that release process as a nightly batch job
[[MMB0100:5-19]]. The **Order Enquiry (OE)** side, in Mantis/Supra, looks up an
order and its line items and displays a price via an external calculation
[[ORDENQ:15-27]]. Nothing in the supplied source establishes whether these are
one system or two — see the gap register.

## Scope of this documentation

| Input | Supplied | Coverage impact |
|---|---|---|
| Natural source (11 programs, 1 copycode, 1 map) | Yes | 96.9% line recognition; the module docs under `docs/natural/` cover all 11 batchable programs plus the copycode |
| Mantis source (1 program, `ORDENQ`) | Yes | Fully documented; two `INCLUDE` targets (`ORDSCR1`/`ORDSCR2`, screen definitions) and two called externals (`PRICECALC`) were not supplied |
| Adabas DDM (2 files: `MILL-ORDER`, `TEST-COUPLE`) | Yes | `MILL-ORDER` merges cleanly with its FDT; `TEST-COUPLE` has no application access found anywhere in the supplied source |
| Adabas FDT (2 files, same physical stores as the DDMs above) | Yes | Merged into the DDM-defined entities; no field-level disagreement found |
| Supra directory (`STEELDB`: `ORDERMST`, `ORDLINE`) | Yes | Fully documented via `ORDENQ`'s access |
| DB2/SQL DDL (`MILL_CERT`, `GRADE_MASTER`) | Yes | `MILL_CERT` does not resolve against the `MILL-CERT` entity name application code actually uses — see the gap register |
| JCL (`MMB0100`, 3 steps) | Yes | Fully documented as a process flow; the two called externals (`IDCAMS`'s actual work is understood, `MMU0300` is not) were not supplied |
| CICS CSD (`STEEL`, 2 transaction/resource definitions) | Yes | Both entry points recorded below; neither named program (`NATCICS`, and `MMP0200` itself) has its CICS-specific behaviour (map I/O, pseudo-conversational logic) further documented beyond what the module doc for `MMP0200` covers |
| Called externals: `MMN0250`, `MMN0900`, `MMLDA01`, `MMM0200`, `PDFGEN`, `IDCAMS`, `MMU0300`, `NATBATCH`, `NATCICS`, `ORDSCR1`, `ORDSCR2`, `PRICECALC`, `PROGA` | No | 13 unresolved call targets across the system (see "Known unknowns") |

## Data model

Business entities: `MILL-ORDER` (mill order header, with grade/status/weight and a
periodic delivery group) and its release-time counterpart `STOCK-BALANCE`
(unsupplied); `MILL-CERT` (mill certificate, unsupplied); `ORDERMST`/`ORDLINE`
(order header/lines on the OE side, linked by Supra linkpath `ORDLNK`
[[STEELDB:19]]); `TEST-COUPLE`, which declares a coupling to `MILL-ORDER`'s
physical file in a DDM remark but has no application code accessing it at all
[[TEST-COUPLE:6]]. Full field-level detail for each is in `docs/entities/`.

| Module | Data store | Operations | Citation |
|---|---|---|---|
| `MMP0100` | `MILL-ORDER` | Read, Update | [[MMP0100:33]] |
| `MMP0100` | `ORDER-AUDIT` | Create | [[MMP0100:71]] |
| `MMP0100` | `STOCK-BALANCE` | Read | [[MMP0100:43]] |
| `MMP0200` | `MILL-CERT` | Read | [[MMP0200:15]] |
| `MMP9200` | `MILL-ORDER` | Read, Update | [[MMP9200:12]] |
| `MMP9600` | `MILL-ORDER` | Read | [[MMP9600:8]] |
| `MMP9700` | `MILL-ORDER` | Read | [[MMP9700:7]] |
| `ORDENQ` | `ORDERMST` | Read, Update | [[ORDENQ:15]] |
| `ORDENQ` | `ORDLINE` | Read | [[ORDENQ:23]] |

`ORDER-AUDIT` and `STOCK-BALANCE` have no DDM, FDT, Supra directory entry or DDL
supplied for them — they are named only as targets of `data_access` statements in
application code, so no field-level entity doc exists for either. See the gap
register.

## Entry points

| Kind | Name | Starts | Notes | Citation |
|---|---|---|---|---|
| Batch job | `MMB0100` | `MMP0100` (via the `NATBATCH` Natural batch driver) | Nightly mill order release; see `docs/process-flows/MMB0100.md` | [[MMB0100:5]] |
| CICS transaction | `MC02` | `MMP0200` | Mill certificate enquiry, `TWASIZE=256` | [[STEEL:2]] |
| CICS transaction | `MO01` | `NATCICS` | `NATCICS` is the Natural-under-CICS driver, not a business program in its own right; which Natural program it starts was not established from the supplied source | [[STEEL:1]] |

No entry point was found for any of `MMC0100`, `MMM9000`, `MMP9000`, `MMP9100`,
`MMP9200`, `MMP9300`, `MMP9400`, `MMP9500`, `MMP9600`, `MMP9700` or `ORDENQ` — see
"Known unknowns".

## Module inventory

| Module | Type | Purpose | Doc | Citation |
|---|---|---|---|---|
| `MMP0100` | Natural program | Confirms and releases a mill order against available stock | `docs/natural/MILLPROD/MMP0100.md` | [[MMP0100:1]] |
| `MMP0200` | Natural program | Mill certificate enquiry | `docs/natural/MILLPROD/MMP0200.md` | [[MMP0200:1]] |
| `MMC0100` | Natural copycode | Shared grade-X9 validation, included by `MMP9100` | `docs/natural/MILLPROD/MMC0100.md` | [[MMC0100:1]] |
| `MMM9000` | Natural map | Screen map; not a batchable module doc target under `mfdoc batch` | — | [[MMM9000:1]] |
| `MMP9000`, `MMP9100`, `MMP9200`, `MMP9300`, `MMP9400`, `MMP9500`, `MMP9600`, `MMP9700` | Natural programs | Individually documented; each is an orphan module (no supplied caller) | `docs/natural/MILLPROD/*.md` | — |
| `ORDENQ` | Mantis program | Order enquiry with price calculation | `docs/mantis/STEELLIB/ORDENQ.md` | [[ORDENQ:1]] |

## Cross-cutting behaviour

`MMC0100` is the only shared copycode in the supplied source, included by
`MMP9100` alone [[MMP9100:11]] — no other module includes it, so "shared" is by
design intent (per its own header comment) rather than by evidenced reuse.
No global data areas, standard error-handling routine, or other cross-module
convention was found in the supplied source beyond this.

## Known unknowns

- **Nine orphan modules**, medium severity: `MMP9000`, `MMP9100`, `MMP9200`,
  `MMP9300`, `MMP9400`, `MMP9500`, `MMP9600`, `MMP9700`, `ORDENQ` have no supplied
  JCL step, CICS transaction or program call reaching them. Blocks confirming
  whether their module docs describe live functionality or dead/orphaned code.
- **13 unresolved call targets**, high severity: `MMN0250`, `MMN0900` (called from
  `MMP0100`), `MMLDA01` (included by `MMP0100`), `MMM0200` (included by `MMP0200`),
  `PDFGEN` (called from `MMP0200`), `PROGA` (called from `MMP9400`), `IDCAMS`,
  `MMU0300`, `NATBATCH` (JCL steps in `MMB0100`), `NATCICS` (CICS transaction
  `MO01`), `ORDSCR1`, `ORDSCR2` (included by `ORDENQ`), `PRICECALC` (called from
  `ORDENQ`). Blocks documenting what each does with the data it's handed.
- **3 entities with no supplied definition**, high severity: `STOCK-BALANCE`,
  `ORDER-AUDIT` (both accessed by `MMP0100`), and effectively `MILL-CERT` (see
  next item). Blocks field-level entity documentation for each.
- **`MILL-CERT`/`MILL_CERT` naming mismatch**, discovered while building the
  entity docs, not itself in the automated gap register: `MMP0200` accesses an
  entity named `MILL-CERT` [[MMP0200:15]], while the supplied SQL DDL defines a
  table named `MILL_CERT` [[CERTS:1]] with a plausible matching field set
  (`CERT-NO`/`CERT_NO`, `GRADE-CODE`/`GRADE_CODE`). The tool does not treat these
  as the same entity — different naming conventions are not evidence of identity
  — so this must be confirmed by an SME, not assumed.
- **One dynamic call target**, high severity: `MMP0200`'s `FETCH RETURN` transfers
  to a variable (`#PGM`) [[MMP0200:22]]; the full set of possible targets cannot be
  determined from source.
- **One uncommitted write**, high severity: `MMP9200` writes to `MILL-ORDER` with
  no explicit `END TRANSACTION`/`COMMIT` in its own source [[MMP9200]]; whether a
  caller or the TP monitor commits it is unresolved.

Full detail, evidence and suggested owners for every item above are in
`docs/gap-register.md`.
