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
- [Backward key-source trace](#backward-key-source-trace)
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

For Supra, the label patterns live in `LABELS` in `src/mfdoc/dialects/supra.py`.
Two ways to calibrate them:

- Per project, override just the labels that disagree with this site's report
  wording via `options.dialects.supra.labels` in `project.yml` -- a mapping of
  label name (`schema`, `dataset`, `dataset_type`, `control_key`, `element`,
  `linkpath`, `link_from`, `link_to`) to a regex string. Supplied keys replace
  the built-in pattern for that key only; unset keys keep the shipped default
  (`supra.labels_from_options` does the merge). Every pattern, built-in or
  overridden, **must** include a named capture group `(?P<v>...)` around the
  value to extract -- `supra._find` reads `m.group("v")` unconditionally, and
  `mfdoc`'s config validation rejects an override missing it. This is the
  right place for a one-off site quirk (e.g. the report prints `FILE-ID:`
  instead of `DATA-SET:`) without forking the module:

  ```yaml
  options:
    dialects:
      supra:
        labels:
          dataset: '^\s*(?:FILE-ID|DATA\s*-?\s*SET)\s*[:=]\s*(?P<v>[A-Z0-9\-_#$]{1,32})'
  ```

- If the shipped defaults are wrong for every codebase this project will ever
  touch (not just this one site), edit `LABELS` in the module directly and
  re-ingest.

If `datasets` comes back as zero, the report layout differs from the shipped
patterns (and any project override) entirely -- check the gap register
(`unparsed_line`) for a sample of the unmatched lines before assuming which
label is at fault.

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

**Structure.** `PROGRAM "name"`, `ENTRY name(params)` … `EXIT`, `DO name` (or
`PERFORM name`) to invoke an entry point, `EXTERNAL "library","program"` to
declare a module elsewhere.

A `DO`/`PERFORM` target matching one of the *same member's own* `ENTRY` points
resolves specially, the same way `natural-adabas.md` describes for `PERFORM` to
a local `DEFINE SUBROUTINE`: the scanner pre-scans every `ENTRY name` in the
member (`_scan_routines`), and a `DO`/`PERFORM` whose target is one of those
names is recorded as `call_kind='PERFORM_INTERNAL'` with `resolved=1` and
`callee_id` pointing at the member itself, instead of being left as an
ordinary `PERFORM`/missing-module gap. Without this, every call to a local
paragraph looks like a call to a missing external module in the gap register.
For example:

```
PROGRAM "TESTMOD"
ENTRY MAIN
  DO BUILD_KEY
EXIT

ENTRY BUILD_KEY
  X=1
EXIT
```

`DO BUILD_KEY` resolves to `call_kind='PERFORM_INTERNAL'`, `resolved=1`,
`callee_id` = this member's own id — not a gap.

Argument order in `EXTERNAL` is assumed library-first. Some installations reverse
it. The scanner records the first token as the library and raises a gap saying to
confirm, because recording a library as a callee invents a missing module that an
SME then has to chase and dismiss.

**Dot-prefixed block-depth markers.** Some Mantis exports render block nesting
as a literal run of leading dots instead of whitespace indentation — `.IF ...`,
`..GET ...`, `...END` — with a bare `|` right after the dots (or at column 0)
marking a remark or commented-out statement: `.|`, `..|RFC-START`,
`|C00306 START`. `mantis.py`'s `_split_depth_marker` strips the leading dots
before any keyword pattern is matched and reports whether what follows is a
`|`-remark, so the existing IF/WHILE/DO block-depth tracking is unaffected — it
derives depth from the matched constructs themselves, not from counting dots.
For example, a raw source line

```
..GET WIDGETFILE01(LOOKUP_KEY)FIRST
```

normalises to `GET WIDGETFILE01(LOOKUP_KEY)FIRST` before matching — the two
leading dots carry no meaning beyond nesting depth and are discarded, not part
of the recorded statement text or citation.

The same export style also has an inline-remark marker distinct from the
depth-dot `|` above: a trailing `:|remark` on an otherwise ordinary statement,
e.g. `DO DELETE_TTTL:|TEST HOUSE` or `PREV_PACK=...:|RFC-7943`.
`_strip_trailing_remark` drops everything from the `:|` onward (checked
against the masked form, so a literal `:|` never triggers a false trim) before
any verb pattern sees the line — so `DO DELETE_TTTL:|TEST HOUSE` is recorded as
a plain `DO DELETE_TTTL` call, with `TEST HOUSE` discarded as commentary, not
as part of the target or an argument.

A long condition or quoted string can wrap across physical lines, marked with a
`'` in either of two shapes: **leading** (every continuation line starts with
`'`, e.g. an unclosed `IF(...` followed by `'OR ...)`) or **trailing** (the
line being wrapped itself ends with a bare `'` and the next line carries no
marker of its own, e.g. `DESC="text so far '` / `more text"`). Both are folded
into one statement before keyword matching — see `mantis.py`'s module
docstring for the exact rule that tells a genuine trailing marker apart from
an ordinary line that just happens to end with a closed literal.

`INTERFACE handle("LITERAL",...)` binds `handle` to a known callee. A later
bare `CALL handle` is not left as an indeterminate `dynamic_target` gap —
`graph.resolve_interface_literal_calls` reclassifies it to the bound literal,
since the target was knowable from source all along, just not at the line
that calls it.

**Declarations.** `TEXT`, `SMALLTEXT`, `BIGTEXT`, `NUMERIC`, `BIGNUMERIC`,
`SMALLNUMERIC`, `ARRAY`, `KANJI`, `BIG`, `SMALL`, `PICTURE`, `LEVEL`, and
`VIEW name OF dataset` (the full `DECL_TYPES` tuple in `mantis.py`).

**Control flow.** `IF … THEN` … `ELSE` … `END`, `WHILE` … `END`, `FOR` … `END`,
`CASE` / `WHEN` … `END`, `DO` … `UNTIL`. `ON ERROR`, `WHEN ERROR`, and `SIGNAL`
are not three separate constructs — the scanner treats all three as one and the
same (`RE_ONERR` matches any of them), recorded as a single `ON ERROR`
rule_candidate regardless of which spelling the source uses.

Also recognised, as non-business-rule statements that stop them from showing up
as unparsed-line gaps (the same role `RESET`/`IGNORE` play for Natural — see
`reference/natural-adabas.md`): `PAD`/`UNPAD` (field padding/trimming), `CLEAR`
(resets one or more screen fields), and `RELEASE` (releases a Supra record lock
or a called program's memory — source alone doesn't disambiguate which, so this
stays a bare recognised statement rather than a guessed CRUD access).

`CLEAR`'s screen-field target can itself be followed by further `:`-chained
clauses, same convention as `ASSIGN` (see `_assignment_pairs` below) — e.g.
`CLEAR SCREEN:ATTRIBUTE(SCREEN)="RESET":FLAG=""`. A trailing clause shaped
like a screen-attribute assignment (`ATTRIBUTE(...)=...`) stays untracked
along with the bare `CLEAR` target, but a clause that's a plain field
assignment is split out and recorded as its own `ASSIGN` `rule_candidate`
— it's real business content (a state reset), not screen formatting, so it
doesn't get silently discarded under `CLEAR`'s greedy match.

Blocks close with a bare `END`, which means a missing or extra `END` shifts the
apparent nesting of everything after it. The scanner reports unclosed blocks as
gaps; take them seriously rather than assuming a scanner fault, because they are
often real and indicate the export lost lines.

**Screen interaction.** `CONVERSE screen` (display and wait for input — the main
online interaction point), `SHOW screen` (display only), `PROMPT "text"`
(displays a literal prompt), and the dynamic invocation forms `PERFORM"target"`,
`CALL target`, `CHAIN target`, `LINK target`, `TRANSFER target`.

`CONVERSE` is where a transaction's user-visible behaviour lives. Each one is a
step in the online process flow and should appear in the process document.

`PERFORM"..."` (quoted, no space, distinct from the internal-entry-point
`PERFORM name` form above) is a site convention for a dynamic chain/menu
navigation string: a `;`-separated path where only the text *after the last*
`;` is the real target program — everything before it is chain/menu-path
context, not a callee. For example:

```
PERFORM"/BACK,APPTT,APPTT,TTGP185P;TTPLP211"
```

resolves to a `call_edge` with `call_kind='TRANSFER'` and `callee_name='TTPLP211'`
— not `/BACK,APPTT,APPTT,TTGP185P` and not the whole string. Missing this and
taking the first token (or the whole string) as the target silently invents a
call to something that isn't a program at all.

**Transactions.** `COMMIT`, `ROLLBACK`, `ENDTR`, `CTRL-BEGIN`, `CTRL-END`,
`SINON`, `SINOF`.

**Screen/map exports (`mantis_screen` dialect).** A separate dialect from
`mantis` source itself — pin it on its own source set (`dialect:
mantis_screen`) for whatever a site's screen painter exports (field name,
type, row/col position, length, occurrence count). This is what a
`CONVERSE`/`SHOW screen` target resolves against once the screen's own
export is ingested, and what lets `mfdoc derive` report a field the
screen defines but no program ever references
(`gap_kind='unused_field'`) — see `graph.unused_entity_fields` and
`dialects/screen.py`. Like `mantis`/`supra_dir`, treat this as a starting
point calibrated against one real export, not a validated format: check
`FIELD_TYPES` in `dialects/screen.py` against the export's own TYPE
column values, and expect a `high`-severity gap (not a silent empty
result) if nothing matches at all.

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
| `READX` | read, indexed variant | R |
| `RDNXTX` | read next along a linkpath, indexed variant | R |
| `ADD-M` / `ADDM` | add master record | C |
| `ADD-D` / `ADDD` | add related record | C |
| `ADDVA` / `ADDVB` | add variable-entry, after/before | C |
| `WRITM` | rewrite master | U |
| `WRITD` / `WRITV` | rewrite related / variable-entry | U |
| `WRITX` | rewrite, indexed variant | U |
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

## Backward key-source trace

A `GET`/Supra-DML call very often keys off a bare variable that was built up on
a preceding line, rather than a literal — the source alone leaves that variable
looking like an opaque token unless something ties it back to the expression
that produced it. `mantis.py` does this automatically: `_assignment_pairs`
records the most recent assignment to every variable name it sees, and
`_key_var_candidates` picks out the bare-identifier key argument(s) of a
`GET`/`OBTAIN`/Supra-DML call; when a prior assignment to that variable exists
earlier in the same member, `data_access.key_source_line` and
`key_source_expr` are populated with where and what built it. Only a *prior*
assignment counts (later in the member is not a valid source), and only a bare
identifier is traced — a literal or an inline expression as the key argument
isn't a case this handles, so those are left alone rather than guessed at.

For example:

```
LOOKUP_KEY="H"+BUILD_PART(1,1,5)+BUILD_PART(1,7,8)+BUILD_PART(1,10,10)
GET WIDGETFILE01(LOOKUP_KEY)FIRST
```

The `GET`'s `data_access` row records `entity_name='WIDGETFILE01'`,
`key_expr='WIDGETFILE01(LOOKUP_KEY)FIRST'`, and — because of the trace —
`key_source_line` pointing at the `LOOKUP_KEY=...` line and
`key_source_expr='"H"+BUILD_PART(1,1,5)+BUILD_PART(1,7,8)+BUILD_PART(1,10,10)'`.
A narrator can then say what the key is actually composed of, instead of
describing `LOOKUP_KEY` as an unexplained token. The same trace applies to
Supra DML calls, where the key argument is a bare comma-separated token rather
than a parenthesised sub-expression, e.g. `ORDER_NO="A1"+"B2"` followed by
`READM(ORDERMST, ORDER_NO)` traces `key_source_expr` back to `"A1"+"B2"`.

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
