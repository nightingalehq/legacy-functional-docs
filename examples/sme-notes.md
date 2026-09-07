<!--
  Worked example of the optional sme-notes.md file (see README.md's "SME
  notes" section, and the parser in src/mfdoc/sme_notes.py). It's not wired
  into the checked-in project.yml -- options.sme_notes there is left
  commented out -- this file just shows the shape. Everything below
  describes this repo's own invented fixture project (Mill Order
  Management / MOM), not a real client system.

  Note on the "General" section below: `_parse_text` treats content before
  the first `##` heading identically to an explicit `## General` heading --
  a project can equally well skip the heading and just start the file with
  plain project-wide prose, e.g.:

      This project's source was exported from a decommissioned mainframe LPAR
      in 2025; some copybooks reference a test region that no longer exists,
      so treat any environment name that isn't PROD as unverifiable.

      ## MODULE-ALPHA
      ...

  Both forms reach the same general (unscoped) bucket in Notes -- this file
  uses the explicit `## General` heading for the fixture below only because
  it reads more clearly next to the member-scoped headings that follow it.
-->

## General

The mill floor still runs a nightly batch reconciliation between the order
system and the scale-house feed; several modules read stale weight data
between the last scale tick and that reconciliation. Flag this wherever a
weight or tonnage field is used in a same-day calculation, so the doc reader
knows to treat it as provisional until the nightly job has run.

## MMP0100

`MMP0100`'s status transitions look stricter in the source than they are in
practice: operators can and do re-open a `CONF` (confirmed) order back to
`RLSD` (released) when a cast gets rescheduled, via a supervisor override
that isn't represented as a distinct code path in this module -- it's a
data-entry convention enforced by training, not by `MMP0100` itself. Worth a
gap-register question rather than a stated business rule, since the
override isn't visible in this module's own source.

## MILL-ORDER

The `GRADE-CODE` field on `MILL-ORDER` predates the current grade catalog:
codes below `100` are legacy carbon-steel grades no longer sold, but they
still appear on orders carried forward from before the catalog change and
nothing in this module rejects them. Don't read their continued presence as
evidence the old catalog is still active.
