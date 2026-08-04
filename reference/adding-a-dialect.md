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

## Registering it

1. Create `src/mfdoc/dialects/<name>.py`.
2. Add a signature to `normalise.DIALECT_SIGNATURES` — a regex that reliably fires
   on this dialect and rarely on others. Put it above `natural` and `mantis` if it
   is more specific, since ordering breaks ties.
3. Add the entry to `DIALECT_ROUTER` and `DIALECT_DEFAULT_TYPE` in `cli.py`.
4. Add a fixture under `examples/fixtures/<name>/` and a source set in the example
   config.
5. If members arrive concatenated, add a splitter to `normalise.DEFAULT_SPLITTERS`
   with a named `name` group.

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

**Use `db.upsert_field`** so a field described twice at different levels of detail
is enriched rather than duplicated.

**Externalise keyword tables** as module-level constants. Calibration should be a
table edit, not a code change, because every real codebase needs some.

**Prefer a gap over a guess.** Every gap becomes a question a human can answer.
Every guess becomes a claim somebody has to catch in review, and reviewers do not
catch all of them.
