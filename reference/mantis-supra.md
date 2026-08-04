# Mantis and Supra

Reference for the `mantis` and `supra_dir` dialects.

**Read the calibration section first.** These two packs are a defensible starting
point, not a validated grammar. Mantis and Supra are far less publicly documented
than Natural and Adabas, export formats vary by site and release, and the scanners
are built to be tuned against real source rather than trusted out of the box.

## Contents

- [Calibration — do this first](#calibration--do-this-first)
- [Getting Mantis source out](#getting-mantis-source-out)
- [Mantis constructs](#mantis-constructs)
- [Supra data model](#supra-data-model)
- [Supra DML function codes](#supra-dml-function-codes)
- [Supra directory report](#supra-directory-report)
- [Traps](#traps)

## Calibration — do this first

Run `ingest` on a representative sample, then:

```bash
mfdoc coverage --config project.yml
```

Look at `line_recognition_rate` and at the `unparsed_line` gaps. Below roughly 85%
means the keyword tables do not match this codebase, and any narrative built on the
index will be confidently incomplete.

To calibrate, group the unparsed lines by shape and work down by frequency:

```bash
mfdoc calibrate --config project.yml --dialect mantis
```

This ranks unparsed `mantis` (or `supra_dir`, or any other dialect) statements by
leading keyword, shows a sample line for each, and names the file and constants a
fix would likely go in.

The leading keyword of each unrecognised statement is almost always the thing to
add. Most are one of:

- a declaration type not in `DECL_TYPES` (`mantis.py`)
- a DML function code not in `SUPRA_DML`
- a screen or call verb the site uses that is not in the pattern list
- a comment convention not in `COMMENT_PREFIXES` — check this first, because a
  wrong comment marker makes every comment line look like an unparsed statement
  and drags the rate down for a trivial reason

Add them to the tables in `src/mfdoc/dialects/mantis.py`, re-ingest, and check
the rate moved. Two or three iterations is normal. Record what was changed and why
in the project repo, because the next person to run this will need to know that the
scanner was tuned for this codebase.

For Supra, override `LABELS` in `src/mfdoc/dialects/supra.py` — or better,
`dialects.supra.labels` in project config — to match the site's directory report
wording. If `datasets` comes back as zero, the report layout differs from the
shipped patterns entirely.

## Getting Mantis source out

Mantis programs are stored inside the Mantis library, which is itself held in the
DBMS. There is no filesystem copy to fetch, so an export utility has been run,
and its output format is the variable everything else depends on. Establish which
route was used before assuming anything about the text.

Ask specifically: were screen definitions (views) exported alongside program
source, or only programs? Screens carry field-level validation and prompts that are
business rules, and their absence is a coverage gap worth stating up front.

## Mantis constructs

The scanner recognises these; check each against the actual codebase during
calibration.

**Structure.** `PROGRAM "name"`, `ENTRY name(params)` … `EXIT`, `DO name` to invoke
an entry point, `EXTERNAL "library","program"` to declare a module elsewhere.

Argument order in `EXTERNAL` is assumed library-first. Some installations reverse
it. The scanner records the first token as the library and raises a gap saying to
confirm, because recording a library as a callee invents a missing module that an
SME then has to chase and dismiss.

**Declarations.** `TEXT`, `SMALLTEXT`, `BIGTEXT`, `NUMERIC`, `BIGNUMERIC`,
`SMALLNUMERIC`, `ARRAY`, `PICTURE`, `LEVEL`, and `VIEW name OF dataset`.

**Control flow.** `IF … THEN` … `ELSE` … `END`, `WHILE` … `END`, `FOR` … `END`,
`CASE` / `WHEN` … `END`, `DO` … `UNTIL`, `ON ERROR`, `SIGNAL`.

Blocks close with a bare `END`, which means a missing or extra `END` shifts the
apparent nesting of everything after it. The scanner reports unclosed blocks as
gaps; take them seriously rather than assuming a scanner fault, because they are
often real and indicate the export lost lines.

**Screen interaction.** `CONVERSE screen` (display and wait for input — the main
online interaction point), `SHOW screen` (display only).

`CONVERSE` is where a transaction's user-visible behaviour lives. Each one is a
step in the online process flow and should appear in the process document.

**Transactions.** `COMMIT`, `ROLLBACK`, `ENDTR`, `CTRL-BEGIN`, `CTRL-END`,
`SINON`, `SINOF`.

## Supra data model

Supra is a network-model DBMS, not relational:

- **Master dataset** — records keyed by a control key, typically hashed. One
  occurrence per key.
- **Related / variable-entry dataset (VED)** — dependent records, reached from a
  master rather than by their own key.
- **Linkpath** — the named connection from a master to a related dataset, and the
  path application code walks to get from one to the other.

Linkpaths are the highest-value thing in the directory, because they *are* the
business relationships. A data model documented without them is a list of files.

Where a schema has multiple datasets but no linkpaths in the export, either the
export omitted them or the relationships are implemented in application code only.
Those are very different situations and the scanner raises a high-severity gap
asking which, because it changes how the whole data model should be documented.

## Supra DML function codes

Called directly from Mantis or from 3GL programs. The scanner maps them to CRUD
intent:

| Code | Meaning | CRUD |
|---|---|---|
| `READM` | read master by control key | R |
| `READD` | read related record | R |
| `READV` | read variable-entry record | R |
| `READR` | read by relative position | R |
| `RDNXT` | read next along a linkpath | R |
| `ADD-M` / `ADDM` | add master record | C |
| `ADD-D` / `ADDD` | add related record | C |
| `ADDVA` / `ADDVB` | add variable-entry, after/before | C |
| `WRITM` | rewrite master | U |
| `WRITD` / `WRITV` | rewrite related / variable-entry | U |
| `DEL-M` / `DELM` | delete master | D |
| `DEL-D` / `DELD` / `DELVD` | delete related / variable-entry | D |
| `SINON` / `SINOF` | sign on / off | — |
| `ENDTR` | end transaction | commit |
| `CTRL-BEGIN` / `CTRL-END` | control interval | commit scope |

`RDNXT` in a loop is the standard way to walk a linkpath, so a `RDNXT` loop is
almost always "for each child record of this parent" — a one-to-many traversal
worth naming as such in the documentation.

Deleting a master in a network model has implications for its dependent records
that depend on the schema. Where a `DEL-M` is found, note what happens to the
related datasets as an SME question unless the directory makes it explicit.

## Supra directory report

The parser is label-driven rather than column-positional, because layouts vary.
The shipped labels expect something along these lines:

```
SUPRA DIRECTORY REPORT                    SCHEMA: STEELDB

DATA-SET NAME: ORDERMST      TYPE: MASTER
CONTROL KEY: ORDER-NO
ELEMENT NAME    TYPE   LEN DEC OCC
ORDER-NO        CHAR    10   0   0
ORDER-WT        PACKED   9   3   0

LINKPATH NAME: ORDLNK
PRIMARY DATA-SET: ORDERMST
RELATED DATA-SET: ORDLINE
```

Dataset labels are anchored to the start of the line. An unanchored pattern also
matches the `PRIMARY DATA-SET:` and `RELATED DATA-SET:` lines inside a linkpath
block, which silently invents a duplicate dataset of the wrong type for every
relationship in the schema — and a data model with phantom entities loses reviewer
confidence in the whole document set on first read.

Where a dataset has no explicit `TYPE`, the parser assumes master and raises an SME
question, because master versus variable-entry determines how occurrences are keyed.

## Traps

**Mantis and Natural both look like generic 4GL.** Pin `dialect:` per source set in
the config rather than relying on sniffing. Ambiguous files raise a gap.

**Element names repeat across datasets.** `ORDER-NO` in both master and related
dataset is normal and is how the linkpath works. Do not treat the repetition as
redundancy in the documentation — it is the join.

**A control key declared before the element list.** The directory names the key,
then describes it in the element rows. Both are folded into one field via
`upsert_field`, otherwise the generated data dictionary shows the column twice.

**Kind assumptions across ingest order.** A Mantis DML call knows only a dataset
name; the directory knows whether it is a master or a VED. Whichever is ingested
first must not lock in a guess, so both go through `resolve_entity`, which matches
on name within the `supra` kind family.
