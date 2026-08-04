# Environment: JCL, CICS, copybooks, DDL

Reference for the `jcl`, `cics_csd`, `cobol_copybook` and `sql_ddl` dialects.

These are not the 4GL codebase, but functional documentation without them is
unusable. JCL is the batch process model. CICS CSD is the online process model.
Without either, you have a module catalogue nobody can trace to a business
process.

## JCL

Extracted: job name, steps (`EXEC PGM=` / `PROC=`), `COND`/`IF`, `PARM`, DD
statements with `DSN` and `DISP`, and inline `SYSIN` bodies.

**Step order and `COND` are the process flow.** `COND=(4,LT)` means the step is
skipped when a prior step returned 4 or more — that is business-relevant
conditional processing, not plumbing, and it usually encodes "only do this if the
extract succeeded".

**`DISP` reveals data flow.** `DISP=(NEW,CATLG,DELETE)` creates a dataset;
`DISP=SHR` reads one. Matching creators to consumers across steps and jobs gives
the batch data lineage, which is often the single most requested artefact in a
modernisation project.

**Infrastructure DDs are excluded from the data model.** `STEPLIB`, `DDCARD`,
`SYSPRINT`, `CMPRINT` and similar carry load libraries and print files. Registering
their datasets as business data stores buries the real gaps under load-library
names. The exclusion list is `INFRASTRUCTURE_DDS` in `environment.py`; extend it
where a site uses non-standard names.

**Natural batch is driven from the stacked input.** `PGM=NATBATCH` with the program
name stacked on `CMSYNIN`:

```
//CMSYNIN  DD *
LOGON MILLPROD
MMP0100
FIN
/*
```

The EXEC card says `NATBATCH`; the program that actually runs is `MMP0100`. Without
reading the stack, every batch Natural program looks like unreferenced dead code —
so the parser mines `CMSYNIN` and `CMOBJIN`, and creates a call edge with the
`LOGON` library recorded.

`SYSIN` is only mined when the step's program is a Natural driver. An IDCAMS or
DFSORT control-card stream on `SYSIN` would otherwise manufacture call edges to
things like `REPRO` and `OUTDATASET`.

**Scheduler definitions are not JCL.** Job dependencies usually live in the
scheduler, not in the JCL. If they were not supplied, the batch process flow is
per-job only; say so rather than implying the job sequence is known.

## CICS CSD

Extracted from a CSD extract listing: `DEFINE TRANSACTION`, `PROGRAM`, `FILE`,
`MAPSET`, `TDQUEUE`, `TSMODEL`, with attributes.

The `TRANSACTION` to `PROGRAM` mapping is the online entry-point list — the
four-character code a user types, and what it starts. That is the beginning of
every online process document.

For Natural under CICS the transaction usually points at a Natural driver
(`NATCICS` or similar) rather than at the business program, with the actual program
selected from a menu or the terminal input. Where that happens, the transaction
gives you the door but not the room, and which program runs is an SME question.

`FILE` definitions map a CICS file name to a physical dataset, which connects
application file references to datasets seen in JCL.

## COBOL copybooks

Level numbers, `PIC`, `USAGE`, `OCCURS` and `REDEFINES` are extracted, and `PIC`
plus `USAGE` are converted to a format and length so copybook records sit alongside
Adabas and Supra definitions in one data dictionary.

`REDEFINES` is recorded but not interpreted. A redefined area means the same bytes
carry different meanings in different circumstances, and which meaning applies when
is a business rule that lives in the code, not the copybook. Flag those for SME
review — they are a common source of migration defects.

`OCCURS DEPENDING ON` is not fully handled. Variable-length records need
confirmation of the controlling field.

## SQL DDL

`CREATE TABLE` with columns, `PRIMARY KEY`, `FOREIGN KEY`, `NOT NULL`, and
`CREATE INDEX` / `CREATE UNIQUE INDEX`.

Foreign keys become entity links, giving a relational model that can be compared
against the Adabas or Supra model. Where a system has been partially migrated,
that comparison is the most valuable artefact available: it shows which business
entities exist in both worlds and where they disagree.

A `UNIQUE INDEX` without a declared `PRIMARY KEY` is still a uniqueness business
rule. Document it as one.
