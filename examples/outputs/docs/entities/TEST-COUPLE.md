---
title: "TEST-COUPLE — Regression fixture for Adabas coupling"
doc_type: data-entity
system: "MOM"
entity: "TEST-COUPLE"
entity_kind: "ddm"
physical_ref: "DBID 012 FNR 090"
generated_by: legacy-functional-docs 0.1.0
generated_at: "2026-08-11"
review_status: draft
reviewers: []
confidence_summary:
  verified: 3
  inferred: 1
  unresolved: 2
sources:
  - TEST-COUPLE
sme_questions:
  - "Is TEST-COUPLE a real, in-use physical file, or purely a fixture/test artefact left in the exported DDM library? No application code in the supplied source accesses it at all."
  - "AMBIGUOUS-NOTE's remark mentions coupling to another file but names neither the file nor the FNR — what file is it actually coupled to, and by what field?"
---

# TEST-COUPLE — Regression fixture for Adabas coupling

## What it holds

One record per couple entry, keyed by `COUPLE-KEY` (format `A10`) [[TEST-COUPLE:5]].
No supplied application code reads or writes this entity, so its business purpose
cannot be established from usage — only from its own field definitions and their
descriptive remarks *(unresolved — see "Gaps and questions" below)*.

## Physical implementation

Defined in the Adabas DDM listing for DBID 012, FNR 090 [[TEST-COUPLE:1]]. No FDT
report was supplied for this physical file, so there is nothing to cross-check
these field definitions against.

## Fields

| Field | Short name | Format | Length | Key/index | Business meaning | Confidence | Citation |
|---|---|---|---|---|---|---|---|
| `COUPLE-KEY` | AA | A | 10 | DE | the couple entry this record describes | verified | [[TEST-COUPLE:5]] |
| `CROSS-REF` | AB | A | 8 | NU | a cross-reference value; the DDM's own remark on this field states it "couples to FNR 045" (`MILL-ORDER`'s physical file) [[TEST-COUPLE:6]] | inferred (from the DDM's own remark text, not from any code that follows this reference) | [[TEST-COUPLE:6]] |
| `AMBIGUOUS-NOTE` | AC | A | 8 | NU | a further note field; its own remark also mentions "coupling" but does not name a target file or field [[TEST-COUPLE:7]] | unresolved — remark text is present but too vague to establish which file/field it refers to | [[TEST-COUPLE:7]] |

## Relationships

| Related store | Relationship | Implemented by | Citation |
|---|---|---|---|
| `MILL-ORDER` | `CROSS-REF` couples to FNR 045, per the DDM's own remark | Adabas coupling (declared in the field remark, not a separate coupling definition) | [[TEST-COUPLE:6]] |

This relationship is evidenced only by the DDM's inline remark on `CROSS-REF`, not
by a separate Adabas coupling definition or by any application code that follows
it — treat it as a documented intent rather than a confirmed, enforced link until
an SME or a coupling definition confirms it. `AMBIGUOUS-NOTE`'s own remark
similarly claims a coupling but names no target, so no relationship row is listed
for it here — inventing one would misrepresent what the source actually states.

## Which modules use it

No application access to `TEST-COUPLE` was found anywhere in the supplied source
[[TEST-COUPLE]] — it is not read, written, or referenced by any of the 21
ingested members. See the gap register's "Possible dead code" section for the
full list of orphan modules/stores this index reports.

## Data quality and integrity rules

None evidenced — with no application code accessing this entity, there is nothing
to observe a validation rule or literal-value constraint against.

## Gaps and questions for review

1. No supplied application code accesses `TEST-COUPLE` at all — is it a real,
   in-use physical file, or a fixture/test artefact that happened to be included
   in the exported DDM library? This affects whether it belongs in the
   documentation set at all.
2. `AMBIGUOUS-NOTE`'s remark [[TEST-COUPLE:7]] mentions coupling but names
   neither a target file nor a target field — what is it actually coupled to?
3. `CROSS-REF`'s coupling to `MILL-ORDER` [[TEST-COUPLE:6]] is stated only in a
   DDM remark, not enforced by any Adabas coupling definition or application code
   found in the supplied source — is it still current, or a leftover comment from
   a design that changed?
