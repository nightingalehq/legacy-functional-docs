---
title: "{DIALECT} — language guide"
doc_type: language-guide
system: "{SYSTEM}"
dialect: "{natural|mantis|supra_dir}"
generated_by: legacy-functional-docs 0.1.0
generated_at: "{YYYY-MM-DD}"
review_status: draft
reviewers: []
confidence_summary:
  verified: 0
  inferred: 0
  unresolved: 0
sources: ["{DIALECT} source files"]
sme_questions: []
---

# {DIALECT} — language guide

What `{DIALECT}` actually looks like in this codebase — every recognised
construct, grouped by keyword with a frequency count and one cited example
each. This is the narrative tier: start from `mfdoc lang-guide --config
project.yml --dialect {DIALECT} --out <path>`'s output (the basic,
deterministic tier — same tables, same citations, no prose) and add a short
paragraph of connective prose under each table describing how these
constructs combine into idioms in *this* codebase. Every added sentence
must either point at a citation already present in the basic-tier table or
be explicitly hedged (`inferred`, `unresolved`) — same discipline as
`system-overview.md`. Never invent a claim the table doesn't already
support; if a pattern looks true but isn't backed by a cited example, note
it as a question for `gap-register.md` instead of asserting it here.

## Structure / declarations

Copy the basic tier's table here (keyword = `variable.format`, i.e.
declaration type). Add prose noting any consistent declaration idiom (e.g.
a shared naming convention or format choice across most variables of a
kind) only if the cited examples actually show it repeating.

## Control flow

Copy the basic tier's table here (keyword = `rule_candidate.construct`).
Add prose on how branching idioms are actually built in this codebase —
e.g. whether validation typically nests inside one `IF` or chains several,
whether escape/loop constructs are common — grounded in the cited rows.

## Data access (DML)

Copy the basic tier's table here (keyword = `data_access.verb`). Add prose
on the read/write idiom actually used (e.g. a consistent find-then-update
sequence) only where the cited examples show it.

### Entity relationships

Copy the basic tier's subsection table here (keyword =
`entity_link.link_kind`, restricted to links established in this dialect's
own source — see the design spec for why data-definition-only links are
excluded). Add prose only where a pattern is cited.

## Screen interaction

Copy the basic tier's table here (keyword = `interaction.kind`). Add prose
on how user-facing input/output is actually sequenced, grounded in cited
examples.

## Transactions

Copy the basic tier's table here (keyword = `transaction_marker.marker`).
Add prose on the unit-of-work idiom actually used (e.g. where commits
typically land relative to a validation block), grounded in cited
examples.

## Calling conventions

Copy the basic tier's table here (keyword = `call_edge.call_kind`). Add
prose on how modules actually invoke one another (e.g. a consistent
static-vs-dynamic call pattern), grounded in cited examples.

## Not yet recognized

Copy the basic tier's appendix table here verbatim (from
`graph.unparsed_line_shapes`, shared with `mfdoc calibrate`). No narrative
addition expected in this section — it exists to name a gap, not to
describe a pattern that isn't actually understood yet.
