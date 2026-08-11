---
title: "Mill Order Management — gap register"
doc_type: gap-register
system: "MOM"
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 0
  inferred: 0
  unresolved: 41
sources:
  - MMP0100
  - MMP0200
  - MMB0100
  - MMC0100
  - MMM9000
  - MMP9000
  - MMP9100
  - MMP9200
  - MMP9300
  - MMP9400
  - MMP9500
  - MMP9600
  - MMP9700
  - ORDENQ
  - TEST-COUPLE
  - STEEL
sme_questions: []
---

# Mill Order Management — gap register

Everything the deterministic pass could not resolve, as questions for domain
experts, plus one cross-source discrepancy found while writing the entity docs
that the automated gap machinery has no way to detect on its own. Ordered by
what it unblocks, not by severity label alone. 41 gaps total from the automated
pass (`mfdoc coverage`: 20 high, 20 medium, 1 low\* — \*7 low-severity
`unparsed_line` gaps are grouped under "Possible dead code"/omitted below where
they're pure scanner noise, not a documentation blocker; see each module's own
doc for the exact unparsed line if it matters to that module).

## How to use this

Each item names what is unknown, what it blocks, and who is likely to know.
Answers go in the Answer column; when an item is closed, the affected documents
are regenerated and their `review_status` advances.

## Blocking: documentation cannot proceed without these

| # | Question | Blocks | Evidence | Likely owner | Answer |
|---|---|---|---|---|---|
| 1 | Is `MMP9200` writing to `MILL-ORDER` safe with no explicit commit in its own source — handled by a caller, by the TP monitor at task end, or not at all? | Whether `MMP9200`'s module doc can describe this as intentional design vs. an actual defect | [[MMP9200]] performs 2 write operations with no `END TRANSACTION`/`COMMIT` | Natural/TP architecture owner | |
| 2 | Are the nine orphan modules (`MMP9000`, `MMP9100`, `MMP9200`, `MMP9300`, `MMP9400`, `MMP9500`, `MMP9600`, `MMP9700`, `ORDENQ`) genuinely live, invoked by something outside the supplied source, or dead code? | Whether their module docs should be presented as current functionality at all | See "Possible dead code" below | Application owner / scheduler admin | |

## Missing source

| # | Target | Referenced from | Impact | Answer |
|---|---|---|---|---|
| 1 | `MMLDA01` (local data area) | [[MMP0100:7]] | Fields it declares are undocumented; `MMP0100`'s own parameter/local-variable picture is incomplete without it | |
| 2 | `MMN0250` | [[MMP0100:57]] | Called on partial release; effect on `MILL-ORDER`/downstream is unknown | |
| 3 | `MMN0900` | [[MMP0100:67]] | Called after every successful update; effect is unknown | |
| 4 | `MMM0200` (screen map) | [[MMP0200:11]] | Fields/prompts shown to the certificate-enquiry user are undocumented | |
| 5 | `PDFGEN` | [[MMP0200:23]] | Non-Natural (3GL) module; behaviour with the certificate number is unknown, and it may be unreachable (see "Discrepancies found") | |
| 6 | `PROGA` | [[MMP9400:10]] | Effect of this call is unknown; `MMP9400` is itself an orphan module | |
| 7 | `IDCAMS`'s `SYSIN` control cards | [[MMB0100:14]] | What STEP020 copies is known (`REPRO`), but not who consumes `STEEL.PROD.ORDER.BACKUP` downstream | |
| 8 | `MMU0300` | [[MMB0100:19]] | "RELEASE" processing against the extract is undocumented | |
| 9 | `NATBATCH` | [[MMB0100:5]] | Natural batch driver; not business logic, but its own error/restart behaviour is undocumented | |
| 10 | `NATCICS` | [[STEEL:1]] | Natural-under-CICS driver for transaction `MO01`; which Natural program it starts was not established | |
| 11 | `ORDSCR1` | [[ORDENQ:10]] | Screen definition `ORDENQ` converses with (3 sites); fields/prompts undocumented | |
| 12 | `ORDSCR2` | [[ORDENQ:33]] | Screen definition `ORDENQ` converses with; fields/prompts undocumented | |
| 13 | `PRICECALC` | [[ORDENQ:8]] | External price calculation; argument order also disputed — see "Discrepancies found" | |

Modules called but not supplied. Each one is a hole in the process flow.

## Missing data definitions

| # | Data store | Accessed from | Impact | Answer |
|---|---|---|---|---|
| 1 | `MILL-CERT` | [[MMP0200:15]] | No DDM, FDT, Supra directory entry or DDL resolves to this exact name — field-level meaning is undocumented (but see "Discrepancies found": `MILL_CERT` may be the same store under a different naming convention) | |
| 2 | `ORDER-AUDIT` | [[MMP0100:71]] | Written on every successful release; fields recorded in the audit trail are undocumented | |
| 3 | `STOCK-BALANCE` | [[MMP0100:43]] | Read to accumulate available stock; field-level structure beyond `GRADE-CODE`/`PLANT-CODE`/weight (used in code) is undocumented | |

## Dynamic behaviour

| # | Location | What is indeterminate | Answer |
|---|---|---|---|
| 1 | [[MMP0200:22]] | `FETCH RETURN` target is a variable (`#PGM`); the full set of possible callees cannot be determined from source alone | |
| 2 | [[MMP9200:16]] | `DELETE` refers to a processing-loop label rather than a named view; the target file must be confirmed from the enclosing loop | |
| 3 | [[TEST-COUPLE:7]] | `AMBIGUOUS-NOTE`'s remark mentions coupling but names neither a target file nor field | |

Variable call targets and loop-label updates. Source cannot settle these.

## Business intent

| # | Question | Evidence | Answer |
|---|---|---|---|
| 1 | What do `MMP0100`'s return codes 10, 20 and 30 mean to the calling program, and does any caller distinguish them? | [[MMP0100:35]], [[MMP0100:39]], [[MMP0100:59]] | |
| 2 | Is the 2.5% release tolerance still current business policy? It's compiled into `MMP0100` as an initial value, so changing it requires a code change | [[MMP0100:29]] | |
| 3 | Why is steel grade `X9` specifically singled out for `MMC0100`'s shared validation, and what does a `#VALIDATION-RC` of 99 mean? | [[MMC0100:2-3]] | |
| 4 | What does the `*ERROR-NR` value written by `MMP0200` mean to an operator, and what should they do in response? | [[MMP0200:25]] | |
| 5 | Is `ORDENQ`'s Mantis/Supra side (Order Enquiry) meant to be part of the same system as the Natural/Adabas Mill Order Management side, or a separate application that happens to share this export? | Two distinct `system:` values (`MOM`/`OE`) in `project.yml`, no evidenced code-level link between the two | |

Magic values, status codes, thresholds and tolerances found as literals whose
business meaning is not evidenced in code.

## Possible dead code

| # | Module | Why it looks unreachable | Confirmed dead? | Answer |
|---|---|---|---|---|
| 1 | `MMP9000` | No JCL step, CICS transaction or program call refers to it | No | |
| 2 | `MMP9100` | No JCL step, CICS transaction or program call refers to it (it does `INCLUDE MMC0100`, but nothing calls it) | No | |
| 3 | `MMP9200` | No JCL step, CICS transaction or program call refers to it | No | |
| 4 | `MMP9300` | No JCL step, CICS transaction or program call refers to it | No | |
| 5 | `MMP9400` | No JCL step, CICS transaction or program call refers to it | No | |
| 6 | `MMP9500` | No JCL step, CICS transaction or program call refers to it | No | |
| 7 | `MMP9600` | No JCL step, CICS transaction or program call refers to it | No | |
| 8 | `MMP9700` | No JCL step, CICS transaction or program call refers to it | No | |
| 9 | `ORDENQ` | No CICS transaction, menu, or caller in the supplied source refers to it | No | |
| 10 | `TEST-COUPLE` | No application code anywhere in the supplied source reads or writes it | No | |

Nothing here is confirmed dead. Dynamic invocation and unsupplied schedulers/menu
systems both produce false positives, so each needs confirmation before anyone
acts on it. Seven low-severity `unparsed_line` scanner gaps also touch several of
these same orphan modules ([[MMP9000:15]], [[MMP9400:13]], [[MMP9500:14-15]],
[[MMP9600:12]], [[MMP9700:8]], [[MMP9700:10]]) — worth a second look once a
module's liveness is confirmed, since an unparsed line in dead code is much lower
priority than one in something actually running.

## Discrepancies found

| # | What disagrees | Where | Answer |
|---|---|---|---|
| 1 | Application code accesses an entity named `MILL-CERT` [[MMP0200:15]]; the supplied SQL DDL defines a table named `MILL_CERT` [[CERTS:1]] with a plausible matching field set (`CERT-NO`/`CERT_NO`, `HEAT-NO`/`HEAT_NO`, `GRADE-CODE`/`GRADE_CODE`). Different naming conventions across the Natural and DB2/SQL worlds, not evidence of identity — this tool does not merge them automatically, and did not flag it as a gap on its own since both names independently look "defined" from within their own dialect. Found only by cross-referencing the two by hand while writing the entity docs. Is this the same physical entity? | [[MMP0200:15]], [[CERTS:1]] | |
| 2 | `MMP0200`'s `FETCH RETURN` at [[MMP0200:22]] transfers to whatever program `#PGM` names, which was just set to `MMP0300` at [[MMP0200:21]] — does control ever return afterward to execute the `CALL PDFGEN` at [[MMP0200:23]], or is that call unreachable? | [[MMP0200:21-23]] | |
| 3 | `PRICECALC` is declared `EXTERNAL` at [[ORDENQ:8]] and called positionally at [[ORDENQ:27]]; some Mantis installations put the program name first in the argument list, which this source does not do — is the call's argument order actually correct for this site's convention? | [[ORDENQ:8]], [[ORDENQ:27]] | |

Comments contradicting code, DDMs disagreeing with FDTs, duplicated logic that has
diverged. Often the highest-value findings in the exercise — item 1 above is a
good example: nothing in the automated pipeline could have found it, since it
requires recognising that two independently-valid-looking facts describe the same
real-world thing.
