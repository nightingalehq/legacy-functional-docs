# Adding a dialect

The extractor contract is deliberately small, so IDMS/ADSO, IMS/DL-I, RPG, or a
site-specific 4GL can be added without touching the pipeline.

## The contract

```python
def extract(conn, member_id: int,
            lines: list[tuple[int, str | None, str]],
            member_name: str = "?") -> dict:
    ...
```

`lines` is `(line_no, seq, text)` where `line_no` is the citation ordinal within
the member. Write rows to the fact tables and return a small stats dict.

Two obligations:

1. **Insert every line into `source_line`**, including comments and blanks, with
   `is_comment` set. The validator resolves citations against this table, so a line
   that is not there cannot be cited.
2. **Record a `gap` for anything not understood.** An extractor that silently skips
   what it cannot parse produces an index that looks complete and is not, and
   nothing downstream can detect the difference.

   `environment.py`'s `extract_sql_ddl`, `extract_copybook`, and
   `extract_cics_csd` follow this per-line/per-statement, the same as
   `natural.py`/`mantis.py`: an unrecognised `CREATE TABLE` column or top-level
   statement, copybook line, or CSD line with no `DEFINE` match each raise their
   own `unparsed_line` gap. `extract_jcl` remains only partially compliant: it
   does not gap individual unrecognised statements, but it does raise one
   member-level `unparsed_line` gap when a JCL member produces zero `EXEC`
   steps. See `reference/environment.md` for the detail on each.

## Registering it

1. Create `src/mfdoc/dialects/<name>.py`.
2. Add a signature to `normalise.DIALECT_SIGNATURES` — a regex that reliably fires
   on this dialect and rarely on others. Put it above `natural` and `mantis` if it
   is more specific, since ordering breaks ties.
3. Add the entry to `DIALECT_ROUTER`, `DIALECT_DEFAULT_TYPE`, and
   `DIALECT_PARSER_MODULES` in `cli.py` — the last of these lists every
   module whose source *is* this dialect's extraction logic (itself, plus
   any sibling dialect module it imports a helper from, the way
   `mantis.py` imports `natural.mask_literals`/`orig`). It backs the
   incremental-ingest cache-invalidation guard from issue #194: a dialect
   without an entry here would have any future parser fix to it silently
   invisible to `mfdoc ingest`'s change-detection, exactly the bug #194
   fixed. `cli.py` asserts at import time that every `DIALECT_ROUTER` key
   has a matching entry, so a missing one fails loudly rather than shipping
   silently.
4. Add a fixture under `examples/inputs/<name>/` and a source set in the example
   config.
5. If members arrive concatenated, add a splitter to `normalise.DEFAULT_SPLITTERS`
   with a named `name` group.
6. If the dialect needs a calibratable pattern or keyword table a project should
   be able to override from `project.yml` (rather than editing the module
   source), wire it into `config_validate.py`'s `OPTION_SPECS` — see
   `options.validate.outcome_field_pattern` and `options.overview.dispatch_field_pattern`
   in `conditions.py` for the convention: a dotted config path, a default built
   from the module-level constant when the key is unset, and a docstring on the
   `_from_options` accessor explaining that a supplied pattern *replaces* the
   built-in one rather than merging with it. `OPTION_SPECS` is a flat declarative
   list (path, accepted type(s), optional check) — adding a new key is one entry,
   not a new branch of validation logic.
7. Add a `tests/test_<name>_rules.py` guard file. Every shipped dialect has one
   (`test_natural_rules.py`, `test_mantis_rules.py`, `test_screen_dialect.py`) —
   it is what catches a keyword-table or regex change silently breaking
   recognition of a construct nobody thought to re-check by hand.

## What to extract, in priority order

Work down this list. The first three carry most of the documentation value; a
dialect pack that does only those is already useful.

1. **Data access** — `data_access` rows with `verb`, `crud`, `entity_name`, and the
   key or where-clause text. This drives the CRUD matrix and the data lineage.
2. **Invocation** — `call_edge` rows, with `dynamic=1` where the target is not a
   literal. This drives process flow.
3. **Conditionals** — `rule_candidate` rows with the *exact, unmasked* condition
   text. This is what business rules get written from.
4. **Transaction markers** — `transaction_marker`. Unit-of-work boundaries.
5. **Interaction points** — `interaction` and `message_ref`. User-visible behaviour
   and validation messages.
6. **Declarations** — `variable`, and `entity` / `entity_field` for data
   definitions.

## Conventions worth following

**Mask literals before keyword matching, then slice the original back out.**
`natural.mask_literals` replaces each quoted string with an equal-length run of
NULs, so `natural.orig(stmt, match, group)` can recover the real text by offset.
Storing the masked form loses exactly the business values the documentation is
about: `IF STATUS NE 'CONF'` becomes meaningless without `CONF`.

**Resolve entities through `db.resolve_entity`** rather than picking a kind.
Different inputs know different amounts about the same store, and whichever is
ingested first must not lock in a guess the other then duplicates.

This is the general convention, but it is not the only pattern in the shipped
codebase, and a new dialect facing the same problem `resolve_entity` solves
should not assume it is the only tool available. Adabas is the exception:
`natural.py`/`adabas.py` register entities with `db.upsert_entity` under a
guessed kind (`ddm` from a DDM listing, `adabas_file` from an FDT report, or a
`FILE-nnn` placeholder built from a DDM's DBID/FNR when no name is known yet),
and reconciliation happens afterwards, once, over the whole index —
`graph.reconcile_adabas_files` merges each `FILE-nnn` placeholder into the
correctly-named file from the FDT when the DBID/FNR match is unambiguous, and
raises an `ambiguous_adabas_file` gap when it is not (more than one named file
shares the FNR across differing DBIDs). The reason Adabas needs this instead of
`resolve_entity`'s per-call, name-based merge is that the DDM and the FDT
frequently *do not name the same physical file the same way at all* — a DDM
only ever gives you DBID+FNR, not the file's real name — so there is no shared
name for `resolve_entity` to match on at ingest time; the match has to happen
later, on DBID/FNR, once both sources are in. Reach for this
post-hoc-reconciliation pattern (register under a best guess, fix up
afterwards in a dedicated graph pass) when a new dialect's sources identify
the same store by *different, non-name keys* that only line up once every
source has been ingested — not when a shared name is available at
extraction time, which is what `resolve_entity` is for.

**Use `db.upsert_field`** so a field described twice at different levels of detail
is enriched rather than duplicated.

**Externalise keyword tables** as module-level constants. Calibration should be a
table edit, not a code change, because every real codebase needs some.

**Prefer a gap over a guess.** Every gap becomes a question a human can answer.
Every guess becomes a claim somebody has to catch in review, and reviewers do not
catch all of them.
