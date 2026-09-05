# Design: per-statement completeness check for cited ranges

Date: 2026-09-05
Status: approved (pending final spec review)
Issue: nightingalehq/legacy-functional-docs#59

## Problem

Every existing validation check operates at the rule/citation level: does a
citation resolve to a real line (`validate_doc`'s citation loop), does every
`rule_candidate` BR-id get cited somewhere (`module_completeness_problems`),
does a cited comparison's direction match the source (`_reversed_condition_problems`).
None of them check a finer-grained thing: within a single, already-correctly-cited
line range, does the narrative actually mention *every* statement inside it, or
can one get silently dropped while the surrounding prose still reads as
complete and cites real lines?

Found via a real case: a `DECIDE ON FIRST VALUE OF *PF-KEY` branch citing
`[[MEMBER:691-693]]` covers three real statements (a `VALUE` label, a `FETCH`
call, and an `ESCAPE ROUTINE`), but the generated prose only narrated the
`ESCAPE ROUTINE` and described the branch as a plain exit — the `FETCH`
call's target was completely omitted, changing the branch's actual meaning
(it transfers to another program first, it isn't a bare exit) without any
citation being wrong or any `rule_candidate` going uncited elsewhere.

`call_edge`, `interaction`, and `data_access` already record line-level facts
independent of `rule_candidate` (a call, a screen interaction, a data access
each get their own row keyed to `member_id`/`line_no`, regardless of whether
that line is also a `rule_candidate`). This is enough to build a targeted
completeness check: for a citation range, collect every such row whose line
falls inside it, and flag any whose target name never appears anywhere in
the paragraph that cites the range.

## Non-goals

- Not a citation-correctness check — it never disputes that a citation
  points at a real line; it only asks whether everything on that line range
  got named.
- Not scoped to `rule_candidate` rows at all — deliberately orthogonal to
  `module_completeness_problems`, which already covers "does every BR-id get
  cited somewhere". This checks statements that never become `rule_candidate`
  rows in the first place (a `FETCH`, a `CONVERSE`, a `READ`) but still change
  what a cited range means.
- Not blocking `mfdoc validate`/`mfdoc gate` on day one. False-positive rate
  against real, already-shipped docs is unknown (indirect reference by
  synonym, or a target named in a preceding paragraph rather than the citing
  one, are both plausible undercounts this check can't see). It ships as an
  advisory, non-blocking signal; promoting it to a hard failure is a
  follow-up decision once it's been run against real output.
- No change to how `call_edge`/`interaction`/`data_access` rows are
  populated (`ingest`/`derive`) — this only reads them.

## Design

### Which facts are checked

Three tables, each contributing one "target name" per row inside a cited
range:

| table | target field | skip when |
|---|---|---|
| `call_edge` | `callee_name` | `dynamic=1` (target is a variable, not a literal name — nothing to search prose for) or blank |
| `interaction` | `target` | blank (no map/view name recorded) |
| `data_access` | `entity_name` | blank (unresolved access) |

All three are queried the same way `_reversed_condition_problems` already
queries `rule_candidate`: `member_id=? AND line_no BETWEEN ? AND ?`, using
the same `lf`/`hi` the existing check computes (`hi = lt or lf`).

### Matching a target against prose

Reuses the paragraph window `_containing_sentence` already computes, but the
paragraph itself, not the sentence — chosen over sentence-level matching
because a branch's narration legitimately spans multiple sentences in one
paragraph (setup sentence, then per-statement sentences), and a target named
two sentences after the citation inside the same paragraph is still a real
mention, not an omission. `_containing_sentence` will be split into two
pieces: `_containing_paragraph(body, start, end) -> (para, rel_start)` doing
the boundary-finding it already does, and `_containing_sentence` becomes a
thin wrapper that also applies `SENTENCE_SPLIT` within that paragraph. This
avoids duplicating the paragraph-boundary logic for the new check.

Token matching reuses the same boundary trick `BR_REF` already uses instead
of `\b`: these names share `CITATION`'s charset (`#@$&-_.` are valid
characters in a Natural/Mantis member, program, map, or file name, and are
all non-word characters that `\b` would treat as a boundary even mid-name).
A small helper:

```python
def _name_mentioned(text: str, name: str) -> bool:
    pattern = re.compile(
        rf"(?<![A-Z0-9#@$&.\-_]){re.escape(name)}(?![A-Z0-9#@$&.\-_])", re.I
    )
    return bool(pattern.search(text))
```

### New check function

```python
_STATEMENT_SOURCES = [
    ("call_edge", "callee_name", "call_kind",
     "SELECT line_no, call_kind, callee_name FROM call_edge "
     "WHERE caller_id=? AND line_no BETWEEN ? AND ? AND dynamic=0 AND callee_name IS NOT NULL AND callee_name != ''"),
    ("interaction", "target", "kind",
     "SELECT line_no, kind, target FROM interaction "
     "WHERE member_id=? AND line_no BETWEEN ? AND ? AND target IS NOT NULL AND target != ''"),
    ("data_access", "entity_name", "verb",
     "SELECT line_no, verb, entity_name FROM data_access "
     "WHERE member_id=? AND line_no BETWEEN ? AND ? AND entity_name IS NOT NULL AND entity_name != ''"),
]


def _statement_completeness_problems(
    conn, member: str, member_id: int, lf: int, lt: int | None, body: str, cite_start: int, cite_end: int
) -> list[str]:
    hi = lt or lf
    para, _ = _containing_paragraph(body, cite_start, cite_end)

    problems = []
    for table, target_col, kind_col, sql in _STATEMENT_SOURCES:
        for row in conn.execute(sql, (member_id, lf, hi)).fetchall():
            target = row[target_col]
            if _name_mentioned(para, target):
                continue
            problems.append(
                f"statement inside [[{member}:{lf}{'-' + str(lt) if lt and lt != lf else ''}]] "
                f"targets '{target}' ({row[kind_col]} at line {row['line_no']}) but '{target}' "
                f"is not named anywhere in the citing paragraph"
            )
    return problems
```

(`call_edge`'s query filters `caller_id`, the others filter `member_id` —
matching each table's actual column name; both mean "the member this
citation resolved to".)

### Wiring into `validate_doc`

Same call site as `_reversed_condition_problems`, same scoping variable
(renamed from `check_reversed_conditions` to `module_doc_checks` since it
now gates two checks — narrative module docs are sentence-per-claim prose
where both make sense; a generated-test doc or flat register echoes source
syntax verbatim and would make either check noise rather than signal, per
the existing reasoning already in this file for the reversed-condition
check). Unlike `_reversed_condition_problems`, results append to a **new**
list, not `problems`:

```python
elif module_doc_checks:
    problems.extend(_reversed_condition_problems(...))
    omitted_targets.extend(
        _statement_completeness_problems(conn, member, row["id"], lf, lt, body, m.start(), m.end())
    )
```

`validate_doc`'s return dict gains `"omitted_statement_targets": omitted_targets`.
`ok`/`problems` are unaffected — this key is purely additive, exactly like
`uncited_assertions` is informational until it's added to `problems` (it
already is; this one deliberately is not, per the non-goals above).

### Reporting

- `validate_tree` sums `omitted_statement_targets` counts across `results`
  into a new `omitted_statement_targets` total, alongside the existing
  `completeness_problems` aggregation — but does **not** fold it into the
  `documents_ok`/exit-code calculation.
- `cmd_validate` prints it as its own advisory section, after the
  `completeness_problems` block, clearly labelled non-blocking, e.g.:

  ```
  N statement(s) referenced in cited ranges but not named in surrounding prose (advisory, does not fail validation):
    - <problem text>
  ```
- Exit code logic in `cmd_validate` is unchanged.

## Testing

New tests in `tests/test_validate.py`, mirroring the existing
reversed-condition test shapes (`_member_with_return_code_if_else` /
`COND_FRONTMATTER` pattern):

1. A `call_edge` row inside a cited range whose `callee_name` never appears
   in the citing paragraph → flagged, message names the target and line.
2. Same, but the name appears elsewhere in the same paragraph (a different
   sentence) → not flagged.
3. A `dynamic=1` call_edge row with an omitted target → never flagged,
   regardless of prose (no literal name to check).
4. One `interaction` row and one `data_access` row, each omitted → both
   flagged, proving all three tables are wired in independently.
5. The same omission shape in a non-`module` doc (e.g. `doc_type: register`
   or a generated-test doc) → not flagged (scope check).
6. `_name_mentioned` unit-level cases for the boundary trick: a name that is
   a substring of a longer identifier in prose must not match (e.g. target
   `PGMX02` inside prose word `PGMX023`), and a name containing `#`/`-`/`.`
   must still match as a whole token.

## As built

Corrections against what actually shipped (`src/mfdoc/validate.py`), plus
data gathered since the non-goals section above was written:

- **`_name_mentioned`**: the shipped regex differs from the sample above —
  it special-cases a bare trailing `.` so ordinary sentence-ending
  punctuation right after a name doesn't block a match (`"...calls
  PGMX02. It then..."` still counts as a mention), while `PGMX02.EXT` still
  correctly doesn't. The lookbehind is written symmetrically for the same
  reasoning on the leading side, even though no realistic prose shape
  motivates it. Current shipped form:

  ```python
  def _name_mentioned(text: str, name: str) -> bool:
      pattern = re.compile(
          rf"(?<![A-Z0-9#@$&\-_])(?<![A-Z0-9#@$&\-_]\.)"
          rf"{re.escape(name)}"
          rf"(?![A-Z0-9#@$&\-_]|\.[A-Z0-9#@$&\-_])",
          re.I,
      )
      return bool(pattern.search(text))
  ```

- **`_STATEMENT_SOURCES`**: the sample above shows 4-tuples with a
  table-name string as the first element; the shipped list is 3-tuples —
  `(target_col, kind_col, sql)`, since the caller never needed the table
  name itself (each `sql` string already names its own table, and the
  problem message is built from `target_col`/`kind_col`/`row["line_no"]`
  alone):

  ```python
  _STATEMENT_SOURCES = [
      ("callee_name", "call_kind",
       "SELECT line_no, call_kind, callee_name FROM call_edge "
       "WHERE caller_id=? AND line_no BETWEEN ? AND ? AND dynamic=0 "
       "AND callee_name IS NOT NULL AND callee_name != ''"),
      ("target", "kind",
       "SELECT line_no, kind, target FROM interaction "
       "WHERE member_id=? AND line_no BETWEEN ? AND ? "
       "AND target IS NOT NULL AND target != ''"),
      ("entity_name", "verb",
       "SELECT line_no, verb, entity_name FROM data_access "
       "WHERE member_id=? AND line_no BETWEEN ? AND ? "
       "AND entity_name IS NOT NULL AND entity_name != ''"),
  ]
  ```

- **False-positive baseline (was "unknown")**: running `mfdoc validate`
  against this repo's own bundled fixtures (`examples/outputs/docs/`) found
  24 raw findings, 20 distinct after a dedup fix (the same message could be
  produced by more than one citation of the same range; see below). All 20
  are of the "paraphrase" class — a citation to a member whose statement
  target is narrated only by description, never by name (e.g. a citation
  covering an `ORDLINE` access narrated only as "order lines" in prose).
  These are genuine omissions the check is designed to catch, not false
  positives from this sample. This is the first data point for the still-open
  promote-to-blocking decision the original non-goals section deferred; it
  is not itself a decision to promote.

- **Dedup**: `_statement_completeness_problems` runs once per citation with
  no dedup of its own, so a statement covered by more than one citation
  whose ranges overlap (or are identical) is reported once per covering
  citation. `validate_tree` now dedups the final flattened
  `omitted_statement_targets` list by the message string itself
  (`list(dict.fromkeys(...))`, preserving first-seen order) — the message
  already encodes member/line/target uniquely, so this is sufficient
  without changing where in the pipeline the list is assembled.

## Follow-up (not in this change)

- Once run against a real engagement's generated docs, revisit whether the
  false-positive rate is low enough to promote some or all of these into
  `problems` (blocking), possibly gated per-table (e.g. `call_edge` omissions
  are more likely to change a branch's actual meaning than a `data_access`
  omission, per the motivating case) — that's a data-driven decision issue
  #59 itself leaves open, not one to guess at here. The bundled-fixture
  baseline above is a first data point, not a resolution.
- `interaction` has no index on `member_id`, so the new per-citation query
  (`WHERE member_id=? AND line_no BETWEEN ? AND ?`) does an unindexed scan.
  Invisible on these small fixtures; worth revisiting if this check runs
  against an engagement with many more members. No schema change made here
  — out of scope for this change.
- `interaction` has no `dynamic` column (unlike `call_edge`), so a
  variable-driven `CONVERSE`/`SHOW` target (e.g. a map name held in a
  variable rather than written as a literal) is always flagged as omitted,
  even though its target isn't a fixed name to search prose for in the
  first place — the same false-positive class `call_edge`'s `dynamic=1`
  filter exists to avoid, just with no equivalent column on `interaction`
  to filter on. No schema change made here — out of scope for this change.
