# Natural and Adabas

Reference for the `natural`, `ddm` and `adabas_fdt` dialects.

## Contents

- [Object types](#object-types)
- [Getting source off the mainframe](#getting-source-off-the-mainframe)
- [Structured versus reporting mode](#structured-versus-reporting-mode)
- [Statement forms that matter](#statement-forms-that-matter)
- [DDM versus FDT — the distinction that matters most](#ddm-versus-fdt--the-distinction-that-matters-most)
- [DDM listing layout](#ddm-listing-layout)
- [FDT report layout](#fdt-report-layout)
- [Traps](#traps)

## Object types

| Type | Conventional extension | Invoked by | Notes |
|---|---|---|---|
| Program | `.nsp` | job stack, menu, `FETCH` | can be started standalone |
| Subprogram | `.nsn` | `CALLNAT` | has `DEFINE DATA PARAMETER` |
| Subroutine | `.nss` | `PERFORM` | external subroutine |
| Copycode | `.nsc` | `INCLUDE` | textual inclusion at compile time |
| Helproutine | `.nsh` | help key on a map field | |
| Map | `.nsm` | `INPUT USING MAP` | screen layout; field/text extraction is best-effort and unverified against a real client export -- see `map_body_unverified` gap |
| LDA / GDA / PDA | `.nsl` `.nsg` `.nsa` | `DEFINE DATA ... USING` | data areas |
| DDM | `.nsd` | `VIEW OF` | logical file definition |

The presence of a `DEFINE DATA PARAMETER` block is the reliable signal of a
subprogram, and the scanner uses it in preference to the file extension, because
exported members get renamed.

Copycode is the one to watch. It is textually included, so business rules can live
in a copycode member that appears in dozens of programs. A rule documented once per
including program will be documented dozens of times inconsistently; document the
copycode as its own module and reference it.

## Getting source off the mainframe

Natural source lives in the FUSER/FNAT system files, not on a filesystem. Somebody
has to export it. Which utility they used determines what the export looks like:

- **SYSOBJH (Object Handler)** — the modern route. Unload to a work file in
  transfer format, which is text and can be moved off-platform. Multiple members
  per file, separated by utility banners.
- **SYSMAIN** — moves objects between libraries; not an export route.
- **`LIST` output captured to a dataset** — per-member listings, often with line
  numbers already in the text.

If members arrive concatenated, set `options.splitters.natural` in the config to
match the banner. The shipped defaults cover several common shapes but not all.
Unload-utility banners are excluded from member line numbering so citations match
what a mainframe `LIST` shows; see `UTILITY_BANNER_DIALECTS` in `normalise.py`.

**Check for truncation at column 72.** Some export routes preserve only the first
72 columns. Natural source is usually written within 72 columns, but not always,
and silent truncation mid-statement produces parse failures that look like scanner
bugs.

## Structured versus reporting mode

Structured mode closes every block explicitly (`END-IF`, `END-READ`,
`END-DEFINE`). Reporting mode does not: block scope is implicit, `LOOP` closes
loops, and statement extent depends on context.

The scanner detects the mode and records a high-severity gap for reporting-mode
members, because nesting depth reported for them is unreliable. Reporting-mode
members are usually the oldest code in the system, which unfortunately means they
are usually also the most business-critical. Budget SME time for them
specifically rather than trusting the extracted structure.

## Statement forms that matter

**Data access.** `FIND` (search on a descriptor), `READ` (sequential in logical or
physical order), `HISTOGRAM` (descriptor values only, no record read), `GET` (by
ISN), `STORE`, `UPDATE`, `DELETE`, `END TRANSACTION`, `BACKOUT TRANSACTION`.

`FIND NUMBER` returns a count without reading records — it is a check, not a read,
and describing it as a read misleads.

`UPDATE`/`DELETE` can take a processing-loop label instead of a view name, in
which case the target file is whatever the labelled loop was reading. The
scanner resolves this for the conventional `R#`/`F#`/`H#` label naming (a
`READ`/`FIND`/`HISTOGRAM` labelled e.g. `F1.`), tracking which entity that
loop opened; any other label naming stays flagged `unresolved` rather than
guessed.

**Control flow.** `IF`/`END-IF`, `IF NO RECORDS FOUND` (attached to the preceding
database loop, and easy to miss — it is a business rule about absence),
`DECIDE ON FIRST/EVERY VALUE`, `DECIDE FOR FIRST/EVERY CONDITION`, `FOR`,
`REPEAT`, `ESCAPE ROUTINE|TOP|BOTTOM`, `AT BREAK OF`, `AT END OF DATA`, `ON ERROR`.

`AT BREAK OF` fires on a control-break in a sorted read and is where subtotal and
grouping logic lives. It is genuine business logic that reads like formatting.

**Invocation.** `CALLNAT 'NAME'` (subprogram, static), `CALLNAT #VAR` (dynamic,
unresolvable), `PERFORM` (subroutine — internal if a `DEFINE SUBROUTINE` with that
name exists in the same member, otherwise external), `FETCH`/`FETCH RETURN`
(transfers to another program), `CALL` (a 3GL module, outside Natural entirely),
`INCLUDE` (copycode).

`CALL` is worth flagging loudly. It reaches assembler, COBOL or PL/I that no
Natural-aware tool will see, and it frequently contains the gnarliest business
logic in the system.

**User interaction.** `INPUT`, `INPUT USING MAP`, `REINPUT` (redisplay with an
error message — the message text is user-visible business validation and worth
extracting), `WRITE`, `DISPLAY`, `PRINT`.

**Map body (`.nsm`).** After the map's own `DEFINE DATA`/`END-DEFINE` (parsed the
same as any other member), the body is a sequence of tagged lines: a level, a `T`
(constant/text, e.g. a screen label) or `F` (field, a variable reference) tag, the
content, optional parenthesised attributes (edit mask, colour/intensity, etc.),
and a row/column position. This is the documented Natural map-source convention —
**no shipped fixture or public sample was available to verify it against a real
client export**, unlike the FDT/DDM formats. Every map member raises a
`map_body_unverified` gap for exactly this reason; treat extracted field names,
prompt text and edit masks as needing SME/screen confirmation, not as settled
fact, until calibrated against a real export.

## DDM versus FDT — the distinction that matters most

The **FDT** is the physical Adabas Field Definition Table: what the file actually
contains, by two-character short name.

The **DDM** is a Natural Data Definition Module: a logical view over a file, giving
long names to short names. It is the contract application code is written against.

They diverge, and the divergences are informative:

- A DDM can omit fields. Fields in the FDT but in no DDM are invisible to Natural
  code — often obsolete, sometimes maintained by a 3GL program or a utility.
- Several DDMs can exist over one file, with different names for the same field
  and different subsets. Two programs can appear to use different data when they
  do not.
- A DDM long name can be misleading where the field's use changed and only the
  short name stayed put.

Documenting only the DDM gives a logical model that does not match the database.
Documenting only the FDT gives two-character names nobody recognises. Ingest both;
`graph.reconcile_adabas_files` merges the DDM's `DBID`/`FNR` placeholder with the
named file from the FDT so they appear as one store.

## DDM listing layout

```
DDM NAME: MILL-ORDER                      DEFAULT SEQUENCE: AA
DB: 012  FILE: 045  - MILL-ORDER
T L DB Name                             F Leng  S D Remark
- - -- -------------------------------- - ---- -- - -----------------
  1 AA ORDER-NO                         A   10    D primary order key
  1 AB CUSTOMER-NO                      A    8  N D
G 1 AC ORDER-DETAIL
  2 AD GRADE-CODE                       A    6    D
M 1 AH ROUTE-STEP                       A   12  N
P 1 AI DELIVERY
S ORDER-CUST-KEY = AA(1-10),AB(1-8)
```

- `T` — `G` group, `M` multiple-value field, `P` periodic group, blank elementary
- `L` — level, `DB` — Adabas short name, `F` — format, `Leng` — length
- `S` — suppression: `N` null suppression, `F` fixed storage
- `D` — descriptor (indexed and searchable)
- `S ... = ...` — superdescriptor built from component field ranges

`MU` and `PE` matter functionally: a multiple-value field holds repeating values in
one record, and a periodic group holds repeating groups. Both usually encode a
one-to-many business relationship that a relational model would put in a child
table, so they are exactly what a migration needs to know about.

**Coupling.** Adabas coupling (a cross-file relationship) has no single
standard listing format — it shows up as free text in the Remark column, e.g.
`coupled to file 045`. The extractor looks for an explicit `COUPL...` mention
followed by a file/FNR number nearby in the same remark and records an
`entity_link` with `link_kind='coupled'`, marked `inferred` rather than
`verified` — unlike the DBID/FNR-based `implements` link, this comes from
parsing free text, not a structurally-defined field. A `COUPL...` mention with
no identifiable target file becomes a gap instead of a guess.

## FDT report layout

ADAREP output, pipe-delimited or whitespace-aligned:

```
ADAREP  UTILITY REPORT                        DATABASE 012
FILE NUMBER: 045   FILE NAME: MILL-ORDER

Field Definition Table:
 Level I Name I Length I Format I Options
-------I------I--------I--------I----------------
   1   I  AA  I   10   I   A    I  DE,UQ
   1   I  AH  I   12   I   A    I  MU,NU

Super Descriptor Definitions:
  S1 = AA(1-10),AB(1-8)
```

Options: `DE` descriptor, `UQ` unique, `NU` null-suppressed, `FI` fixed, `MU`
multiple-value, `PE` periodic, `NC`/`NN` SQL null handling, `LA`/`LB` large
alpha/binary.

`UQ` is a uniqueness constraint — a business rule expressed in the physical
schema, and worth documenting as one.

## Traps

**Sequence numbers in columns 73–80.** Detected and stripped automatically, but
only when consistent across the file. Force with `sequence_columns: '73:80'`.

**EBCDIC.** cp037 and cp500 are the common ones. Auto-detected; force with
`encoding:` when the sniffer gets it wrong. A file that decodes to plausible text
in the wrong code page corrupts only the accented and special characters, which is
subtle — check `£`, `@`, `#` and `!` if something looks off, since these move
between EBCDIC code pages and `#` is a legal character in Natural variable names.

**System variables look like comments.** `*ERROR-NR`, `*DATX`, `*USER`, `*PROGRAM`
begin with `*`, and so do comment lines. The scanner treats `*` as a comment only
when followed by whitespace or another `*`.

**Literals containing keywords.** `MOVE 'UPDATE THE RECORD' TO #MSG` must not
register as an update. All keyword matching runs against a copy with literals
masked to equal-length placeholders, and captured groups are sliced back out of the
original so business values like `'CONF'` survive.

**Double extensions from file transfer.** Members frequently arrive as
`MMP0100.NSP.TXT` after an FTP through a text-mode gateway, or as `MEMBER.LST` from
captured `LIST` output. `normalise.derive_member_name` strips the whole chain of
recognised extensions, keeping the innermost dialect extension as an object-type
hint. This matters more than it sounds: a member indexed as `MMP0100.NSP` will not
match a `CALLNAT 'MMP0100'`, so every call edge to it goes unresolved and the module
appears to be dead code. If member names in the index carry extensions, add the
offending suffix to `STRIPPABLE_EXTENSIONS`.
