# Design: `language-guide` document type

Date: 2026-09-06
Status: approved
Issue: nightingalehq/legacy-functional-docs#64

## Problem

Mantis/Supra have no public documentation, and the dialect packs are
explicitly "calibrated, not rewritten" (`reference/mantis-supra.md`). There
is no output that documents the constructs/keywords actually in use in a
given codebase and how they're built — only the diagnostic `mfdoc
calibrate` command, which ranks *unrecognized* lines but says nothing about
what *is* recognized. A team onboarding onto an unfamiliar dialect (or a
Mantis/Supra shop with no vendor manual at all) has to reverse-engineer the
dialect's own shape from `mantis.py`/`supra.py` source or from reading
enough member docs to notice the pattern — this should instead fall out of
facts already sitting in the fact store.

## Non-goals

- No dialect-scanner changes. Every column this reads is already populated
  identically by every dialect's `extract()` — this is a read-only rollup,
  not a new extraction capability.
- No change to `mfdoc calibrate`'s behavior or output — its unparsed-line
  ranking logic is reused (via a shared helper), not replaced or altered.
- Not a tutorial or vendor-manual replacement: it reports frequency and one
  cited example per construct, nothing about *why* a construct exists or
  how Natural/Mantis/Supra differ conceptually — `reference/mantis-supra.md`
  still owns that.

## Data: `graph.language_profile(conn, dialect)`

A pure function over the fact store, no model calls, following the existing
`crud_matrix`/`orphans`/`transaction_scopes` shape in `graph.py`. Signature:

```python
def language_profile(conn, dialect: str) -> dict[str, list[dict]]
```

Returns one list per section key, each entry
`{"keyword", "count", "example_member", "example_line", "example_text"}`,
sorted by `count` descending then `keyword` ascending (deterministic,
byte-identical on unchanged source — the same guarantee every
`structural.py` renderer already makes).

### Section-to-column mapping

| Section (`## ` heading) | Fact-store column | Notes |
|---|---|---|
| Structure / declarations | `variable.format` | Declaration type (Natural `A10`/`N7`/`P3` style formats, Mantis declaration types) — one row per distinct format value across all `variable` rows for the dialect. |
| Control flow | `rule_candidate.construct` | `IF`, `DECIDE ON`, `WHILE`, `FOR`, etc. — already the exact vocabulary `mfdoc complexity`'s `rule_depth` metric walks. |
| Data access (DML) | `data_access.verb` | `READ`, `FIND`, `GET`, `STORE`, `SELECT`, ... |
| Data access (DML) → **Entity relationships** subsection | `entity_link.link_kind` | `coupled`, `linkpath`, `foreign_key`, `joined_in_code`, etc. Grouped under Data access rather than its own top-level section — the issue's suggested section list names six headings, not seven, and entity relationships are a data-access concept (which entities connect to which), not a distinct language feature the way control flow or transactions are. See "entity_link's dialect boundary" below for how this section is filtered. |
| Screen interaction | `interaction.kind` | `INPUT`, `CONVERSE`, `SHOW`, `WRITE`, `DISPLAY`, ... |
| Transactions | `transaction_marker.marker` | `END TRANSACTION`, `COMMIT`, `BACKOUT TRANSACTION`, ... |
| Calling conventions | `call_edge.call_kind` | `CALLNAT`, `PERFORM`, `FETCH`, `CALL`, `XCTL`, ... |

Six `graph.language_profile()` dict keys back these seven rows:
`structure`, `control_flow`, `data_access`, `entity_relationships`,
`screen_interaction`, `transactions`, `calling_conventions` —
`entity_relationships` is a distinct dict key (so it stays independently
testable and independently empty-safe) even though it renders as a
subsection of "Data access (DML)" rather than its own `##` heading.

### Citing an example line

Every section's per-member fact tables (`variable`, `rule_candidate`,
`data_access`, `interaction`, `transaction_marker`, `call_edge`) carry
`member_id` + `line_no`, so the example is fetched via a `LEFT JOIN` to
`source_line` on `(member_id, line_no)` rather than trusting each table's
own free-text column (`data_access.raw`/`rule_candidate.raw` exist, but
`call_edge`/`transaction_marker`/`interaction`/`variable` don't carry an
unmasked-text column of their own) — one uniform lookup mechanism for every
section instead of six different ones, and it reuses the exact recovery
path `natural.orig`/masking already guarantees is the unmasked source text.

`entity_link` doesn't have a `member_id`/`line_no` pair — it has
`via_member` (nullable) and `via_line` (nullable), because a link can be
declared purely at the data-definition layer (Adabas coupling, a Supra
linkpath) with no source line at all. **Design decision: `entity_relationships`
only includes rows where `via_member IS NOT NULL`, filtered by that
member's `dialect`** — a link established at the data-definition layer,
with no code reference, isn't part of "what this dialect's *source code*
looks like" (that's `glossary.md`'s domain: every `entity_link` row,
dialect-agnostic, already rendered there via `entity`/`entity_field`). A
per-dialect language guide for a codebase with no code-level entity
linkage simply renders an empty `entity_relationships` list, same as any
other empty section.

### `graph.unparsed_line_shapes(conn, dialect)` — shared with `mfdoc calibrate`

`cmd_calibrate` in `cli.py` already computes exactly the "seen in source,
not yet recognized" data the appendix needs (`gap` rows where
`gap_kind='unparsed_line'`, grouped by leading keyword, counted, one sample
line kept). That logic moves into `graph.py` as
`unparsed_line_shapes(conn, dialect) -> list[dict]`
(`{"keyword", "count", "sample"}`, sorted by count descending); `cmd_calibrate`
becomes a thin caller of it (identical printed output — this is a pure
refactor, not a behavior change, and `tests/test_cli.py`'s existing
calibrate assertions are the regression check for that). The language-guide
appendix calls the same function.

## Document: `templates/language-guide.md` and the basic-tier renderer

Two artifacts, deliberately different shapes, matching the issue's
"two tiers":

1. **Basic tier** — `structural.language_guide(conn, dialect) -> str`,
   registered as `mfdoc lang-guide`. Front matter is the minimal
   `doc_type: register` shape (`title`, `doc_type` only —
   `REQUIRED_REGISTER_FRONTMATTER` in `validate.py`), matching every other
   purely-mechanical renderer in `structural.py` (`gap_summary`,
   `data_flow_diagram`, `glossary`, `complexity_heatmap`,
   `thematic_rules_register`). It is generated directly in Python — no
   template file is read at render time, same as those five. It is a
   complete, standalone document: a team that never runs the narrative
   step still gets a real deliverable (`mfdoc export`'s posture, per the
   issue).
2. **Full/narrative tier** — `templates/language-guide.md`, following
   `module.md`/`system-overview.md`'s full front-matter shape (`title`,
   `doc_type: language-guide`, `system`, `dialect`, `generated_by`,
   `generated_at`, `review_status`, `reviewers`, `confidence_summary`,
   `sources`, `sme_questions` — the `REQUIRED_FRONTMATTER` branch in
   `validate.py`, unchanged, covers this automatically). This is what an
   interactive Claude Code session writes, guided by `SKILL.md`, taking the
   basic-tier file's content (every keyword, count, and cited example
   already present) and adding connective prose about how constructs
   combine into idioms — same citation discipline as `system-overview.md`:
   every added sentence either reuses a citation already in the basic
   output or is hedged, never invents a new unfounded claim about the
   dialect.

Both tiers share one section order: Structure/declarations, Control flow,
Data access (DML) [with its Entity relationships subsection], Screen
interaction, Transactions, Calling conventions, then "Not yet recognized"
(the appendix). Per-section rendering: a markdown table
(`| keyword | count | example |`) with the example rendered as a
`[[MEMBER:LINE]]` citation (`citations._cite`) followed by the (redacted —
see below) source text in a code span. Sections with no rows print
"None recorded." rather than an empty table (matches `gap_summary`'s
empty-input handling).

### No `brief.py` function

`extending.md`'s checklist asks whether a new document type needs a
`brief.py` function "if the new document type needs its own fact summary
shape." It doesn't: the basic-tier output *is* the complete cited fact
summary — identical to why `glossary.md`/`data-flow.md`/`call-graph.md`
have no `brief.py` counterpart despite also feeding an interactive step
(`system-overview.md`'s narrative may reference `gap-summary.md`/
`call-graph.md` directly, per `SKILL.md`). The interactive session's
"brief plus a project brief" is simply: the basic-tier file itself, read
as-is. Adding a second, redundant summary function would give the
narrative tier a second source of truth to drift out of sync with the one
`mfdoc lang-guide` already produces.

### Redaction

`glossary`/`thematic_rules_register` both take a `Redactor` and apply it to
free-text fields before they reach the document (`entity.notes`,
`entity_field.remark`, rule condition text). `language_profile`'s
`example_text` is a raw source line — the same category of content
`redact.py` is built to scrub — so `structural.language_guide(conn, dialect,
redact=NULL_REDACTOR)` takes a `Redactor` too, applied to each
`example_text` before it's written into the table, and `cmd_lang_guide`
builds it from `cfg["options"]` the same way `cmd_glossary`/
`cmd_rules_theme_register` already do.

## CLI: `mfdoc lang-guide`

```
mfdoc lang-guide --config project.yml --dialect mantis --out <path>
```

Exactly the signature the issue specifies. `--dialect` is required (mirrors
`mfdoc calibrate`); `--out` is optional, defaulting to stdout via the
existing `_write_or_print` helper (mirrors `gap-summary`/`data-flow`/
`glossary`/`complexity`/`rules-theme-register`) rather than requiring an
`--out` the way `call-graph` does (that command's own `--out` is special
because it may write more than one file; this one never does).

## `SKILL.md`

Added to the "Suggested document set" as item 7 (after `coverage-report.md`,
before the pre-existing `executive-summary.md` entry, which shifts to 8) —
per the issue's "after `coverage-report.md`" placement — with a short note
to run `mfdoc lang-guide --config project.yml --dialect <dialect> --out <path>` first and then
write the narrative layer from `templates/language-guide.md`, same pattern
as the existing `executive-summary.md` entry's "run `mfdoc classify-rules`
first" note.

## Output location

Per-project, alongside module docs (e.g. `outputs/staca/reference/
language-guide.md`) — not client-sensitive (pure syntax/frequency counts
and short code snippets, no business data by itself, though `example_text`
can still carry a literal value from a `WHERE`/`IF` condition, which is why
it still goes through `Redactor` like every other example-bearing
register), but per `CLAUDE.md`, it stays out of this repo's own `examples/`
outputs unless a fully invented fixture is built specifically to exercise
it (see "Testing" in the implementation plan).

## Validate.py

No change required. `doc_type: register` (basic tier) already only checks
`REQUIRED_REGISTER_FRONTMATTER`; `doc_type: language-guide` (narrative tier)
falls into the existing "any other `doc_type`" branch and gets the full
`REQUIRED_FRONTMATTER` check already applied to every narrative doc type.
`_artifact_consistency_problems`'s filename-based consistency check
(`gap-summary.md`/`glossary.md`/`call-graph.md` only) is optional
per-artifact tooling, not a requirement of "covers it unchanged" — left as
a documented possible follow-up, not implemented here, to keep this change
additive-only in `validate.py` (zero lines changed).
