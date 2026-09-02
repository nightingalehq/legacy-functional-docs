"""Snapshot of coverage() and run_all() against the shipped fixtures.

Any change to these numbers is either a real improvement (update the
snapshot deliberately, in the same PR that explains why) or an unintended
regression (the point of this test). Either way it must be visible in
review rather than discovered later, which a bare "pipeline still runs"
smoke test would not catch.

2026-08-05: numbers moved when MMP9000.nsp was added (issue 4.5 regression
fixture for continuation folding). It's a standalone, uncalled program by
design, so +1 member/orphan_module gap is expected; +2 rule_candidates
(the multi-line IF condition, plus the MOVE it guards) and +1 unparsed_line
are the fixture doing its job -- see its own comment for why the
"AND ..." continuation line is also visited (and not recognised) on its
own once folded into the preceding IF.

2026-08-05: numbers moved again when MMC0100.nsc + MMP9100.nsp were added
(issue 4.2 regression fixture for transitive copycode). MMC0100 is a real
copycode member (object_type='copycode') with its own rule_candidate;
MMP9100 INCLUDEs it and is itself uncalled by design. +2 members, +2
code_members, +1 resolved include edge, +2 rule_candidates (the copycode's
own IF and MOVE), +1 orphan_module (MMP9100).

2026-08-05: RESET is now recognised (issue 4.11, found via a smoke test
against SoftwareAG/adabas-natural-code-samples) -- MMP0100:31's
`RESET #RETURN-CODE` was the one pre-existing unparsed_line gap in this
fixture set since before any of the above, just never named. -1
unparsed_line, -1 gaps_total (the remaining unparsed_line is MMP9000's
"AND ..." continuation artifact, described above).

2026-08-05: numbers moved again when TEST-COUPLE.ddm + TEST-COUPLE.fdt were
added (issue 4.7 regression fixture for Adabas coupling). Same DDM+FDT
reconciliation shape as MILL-ORDER, so +1 adabas_entities_merged; +2
members, +6 entity_fields, +2 entities_with_definition (TEST-COUPLE itself,
plus its reconciled placeholder collapsing back to 0 net new distinct
adabas_file names), +1 gaps_total (the deliberately-ambiguous
AMBIGUOUS-NOTE field, which correctly produces a gap rather than a guess).

2026-08-05: numbers moved again when MMP9200.nsp was added (issue 4.3
regression fixture for loop-label resolution). +1 member/code_member; +3
data_accesses (FIND, the resolved UPDATE (F1.), the unresolved DELETE
(X9.)); +1 gaps_high (an sme_question this fixture legitimately triggers:
2 write operations with no explicit END TRANSACTION -- not something this
fixture was built to test, just a true side effect of not adding one) and
+2 gaps_total (that plus the DELETE (X9.) dynamic_target gap, which is the
point of the fixture); +1 orphan_module (uncalled by design, as usual for
these regression-only fixtures).

2026-08-05: numbers moved again when MMM9000.nsm was added (issue 4.4
Natural map parser). Maps are excluded from the orphan check by design
(object_type='map' is in orphans()'s exclusion list already), so no new
orphan_module gap; +1 member/code_member, +1 gaps_total (the
map_body_unverified gap this fixture is meant to raise -- map body
recognition is unverified against a real client export, flagged
accordingly on every map member).

2026-08-05: numbers moved again when MMP9300.nsp was added (issue 4.11c/#26
regression fixture for leading numeric sequence-column prefixes). Every
line in this fixture carries a 4-digit leading sequence number instead of
the trailing 73-80 field `detect_seq_columns` already handled, with both
shapes from the issue (no separator before the statement, and a
space-padded one); `detect_leading_seq_prefix` finds and strips it before
dialect content matching runs. +1 member/code_member, +16 source_lines,
+1 rule_candidate (its IF condition), +1 orphan_module gap/gaps_total
(uncalled by design, as usual for these regression-only fixtures) --
line_recognition_rate improves slightly since every line but one now
parses.

2026-08-05: numbers moved again when MMP9400.nsp was added (issue 4.11b/#25
regression fixture for generic statement labels, e.g. "SETA. MOVE ...").
Every RE_* verb pattern anchors on a leading "^\\s*", so a leading label defeats all of
them except the R#/F#/H# loop-label groups already inline in
RE_READ/RE_FIND/RE_HISTOGRAM (a different, narrower thing -- issue 4.3);
a generic strip_generic_label() fallback, tried only after the unstripped
statement has already failed every matcher, recovers the labelled MOVE,
CALLNAT and IF in this fixture. Its SETD. line precedes a genuinely
unrecognised verb and correctly stays an unparsed_line gap -- a label must
never make an unrecognised statement match. +1 member/code_member, +15
source_lines, +1 unparsed_line (the SETD. gap), +2 rule_candidates (the
labelled MOVE and IF), +1 invocation_edge (the labelled CALLNAT, to a
target -- PROGA -- nothing in this fixture set defines, so also +1
unresolved_call/gaps_high), +1 orphan_module (uncalled by design, as usual
for these regression-only fixtures) -- +3 gaps_total in total.

2026-08-05: numbers moved again when MMP9500.nsp was added (issue 4.11a/#24
regression fixture for report-writer column-spec continuations, e.g. "5T").
Its WRITE statement wraps across three physical lines using Natural's
"nT"/"nX" column-position tokens on their own continuation lines --
CONTINUATION_LEAD_COLSPEC (scoped to WRITE/DISPLAY/PRINT via the fold
loop's RE_WRITE check) folds them into one `interaction` row, the same way
4.5/MMP9000 folds a multi-line IF condition. Per that same accepted
behaviour, each folded continuation line is still independently visited by
the main loop afterwards and correctly doesn't stand alone as a statement,
so it raises its own unparsed_line gap too -- not a new defect, the
identical quirk MMP9000's own comment already documents. +1
member/code_member, +17 source_lines, +2 unparsed_lines (the two folded
continuation lines, revisited), +1 orphan_module/gaps_total (uncalled by
design, as usual) -- rule_candidates/invocation_edges unchanged, since a
WRITE produces neither.

2026-08-06: numbers moved again when MMP9600.nsp and MMP9700.nsp were added
(issue 4.6/#5, reporting-mode LOOP/depth inference -- the largest item in
Phase 4). MMP9600 has no structured-mode terminators anywhere (mode is
detected purely from the LOOP tell) and its LOOP's body is consistently
more indented than the LOOP line itself, so `reporting_loop_plan` finds it
unambiguous: LOOP is now recorded as a `rule_candidate` (construct='LOOP',
confidence='inferred') instead of falling through unrecognised, and the
member's `reporting_mode` gap drops from high to medium severity to
reflect that nesting was inferred, not left unstructured. MMP9700's LOOP
body is *not* more indented than the LOOP line -- deliberately ambiguous,
to prove the conservative fallback: no inferred rule_candidate, LOOP still
falls through as `unparsed_line` exactly as before this feature existed,
and the gap stays high-severity. Both members' `FIND (10) MILL-ORDER ...`
resolves against the entity MMP9200 already defined (no DEFINE DATA in
either fixture, deliberately, so `_STRUCTURED_TELLS`'s `END-DEFINE` can't
misclassify them as structured). +2 members/code_members, +26
source_lines, +1 rule_candidate (MMP9600's inferred LOOP), +2
data_accesses (the two FINDs), +3 unparsed_lines (MMP9600's DOEND;
MMP9700's LOOP -- unrecognised since ambiguous -- and DOEND), +1
gaps_high (MMP9700's reporting_mode gap; MMP9600's is medium and doesn't
count here), +7 gaps_total (2 reporting_mode + 3 unparsed_line + 2
orphan_module, uncalled by design as usual).

2026-09-01: mantis.py calibrated against a real client Mantis export
(kept out of examples/ -- client source, not a fixture) that uses
a leading-dot block-depth notation (`.IF`, `..GET`, `...END`) and a `|`
remark marker instead of whitespace indentation and `*` comments; see
`_split_depth_marker`. Plain assignment statements (`STATUS="HOLD"`,
possibly `:`-chained) are now recognised as `rule_candidate` rows
(construct='ASSIGN') instead of being silently counted unparsed-but-ungapped
by a length/shape check that suppressed the gap without recording the fact.
ORDENQ.mantis (the only checked-in mantis fixture) has 4 such assignment
lines that were previously dropped entirely; they now produce real
rule_candidate rows. line_recognition_rate/gaps are unchanged, since these
lines never raised a gap either before or after -- only rule_candidates
moves, from 35 to 39 for this change alone (later paragraphs below push
the final EXPECTED_COVERAGE total further, to 41).

2026-09-01: mantis.py also folds a run of `'`-marked continuation lines onto
the statement they continue (the same real client export's style, same
session as the depth-dot calibration above), instead of leaving a multi-line
condition or call argument list truncated at the first physical line.
ORDENQ.mantis gets a new `VALIDATE_CREDIT_LIMIT` entry appended after the
file's original EXIT to exercise this (flat, no-dot style -- the fold
doesn't require the depth-dot convention to also be present): `IF
ORDER_WT > 500` wraps an
`'OR CUST_NO = " "` continuation onto a second line. Appended rather than
inserted into MAIN deliberately -- inserting mid-file renumbers every later
line and silently stales the line citations already baked into the
checked-in generated docs under examples/outputs/ (mfdoc validate's citation
check only range-checks a line number against the member's line count, so
a stale-but-in-range citation like that passes silently instead of erroring,
which is exactly the "invisible until it matters" citation risk
reference/writing-rules.md warns about -- discovered by making the mistake
once and having to revert it). Per the same accepted double-visit convention
as Natural's own continuation fold, the continuation line is still
independently visited afterwards and correctly doesn't stand alone as a
statement, so it raises its own unparsed_line gap same as before this
feature existed. +8 source_lines (the new entry, its END and both EXITs
becoming source_lines while blank-line/no-op edges add 0), +1
unparsed_line/gaps_total (the continuation line, revisited), +2
rule_candidates (the folded IF and its MSG= ASSIGN), +1 include_edges (the
new SHOW ORDSCR1) -- include_resolution_rate drops to 0.125 since the
denominator grew with no new resolved include.

2026-09-01: MMP9800.nsp added -- statement-recognition regression fixture
covering four constructs found missing while calibrating against a real
client corpus: a bare short-form assignment with no ASSIGN keyword (now
RE_BARE_ASSIGN, wired into `_match_arithmetic` the same as any other
literal-bearing assignment); SET CONTROL (recognised structurally, like
RESET/IGNORE -- no rule_candidate); an INPUT WINDOW block whose column-spec
continuation lines are prefixed with Natural's own "/"/"//" next-line
marker (CONTINUATION_LEAD_COLSPEC now allows the prefix, and the fold
loop's verb check now covers INPUT/REINPUT as well as WRITE/DISPLAY/PRINT,
not just WRITE/DISPLAY/PRINT as issue #24 originally scoped it); and a
COMPRESS wrapping its INTO clause onto a continuation line (INTO added to
CONTINUATION_LEAD, alongside AND/OR/etc). +1 member/code_member, +21
source_lines, +2 rule_candidates (the bare ASSIGN, the folded COMPRESS...
INTO), +1 orphan_module (uncalled by design, as usual for these
regression-only fixtures). Per the same accepted double-visit convention
as every other continuation-fold fixture (see MMP9000/MMP9500 above): the
three folded continuation lines (the two colspec lines, the INTO line) are
each still independently visited afterwards and correctly don't stand
alone as a statement, so +3 unparsed_lines/gaps_total on top of the
orphan_module gap, +4 gaps_total in total; SET CONTROL and the bare
assignment raise no gap at all, matching RESET/IGNORE.

2026-09-01: SCRNENT.mantis added -- a real client Mantis corpus
included an online/screen program whose only self-identifying statement is
`ENTRY SCRNENT(...)`, with no `PROGRAM "name"` self-declaration at all.
mantis.py previously only set a member's object_type on RE_PROGRAM, so such
a member never became "program"/"subprogram"/"subroutine"/"copycode" and
silently fell out of every batchable-member query (module docs, test-plan,
test-gen) with no error at all -- ENTRY now sets object_type the same way
PROGRAM does (COALESCE leaves an already-declared member untouched). +1
member/code_member, +11 source_lines (comment, ENTRY, 2 TEXT decls, SCREEN,
CONVERSE, IF, SHOW, END, EXIT), +1 rule_candidate (the IF), +2 include_edges
(SCREEN's map binding, SHOW's screen reference) -- include_resolution_rate
drops to 0.1 since the denominator grew with no new resolved include; +1
orphan_module/gaps_total (uncalled by design, as usual for these
regression-only fixtures) and +1 unresolved_call/gaps_high/gaps_total (SHOW
MAP's screen reference, unresolved same as any other screen-only target).
"""

from __future__ import annotations

from mfdoc import graph

EXPECTED_COVERAGE = {
    "members": 23,
    "code_members": 15,
    "source_lines": 446,
    "unparsed_lines": 11,
    "line_recognition_rate": 0.9753,
    "entities": 13,
    "entities_with_definition": 9,
    "entity_definition_rate": 0.6923,
    "entity_fields": 46,
    "data_accesses": 14,
    "rule_candidates": 44,
    "invocation_edges": 13,
    "invocations_resolved": 2,
    "call_resolution_rate": 0.1538,
    "dynamic_call_edges": 1,
    "include_edges": 10,
    "includes_resolved": 1,
    "include_resolution_rate": 0.1,
    "gaps_high": 21,
    "gaps_total": 48,
}


def test_coverage_matches_snapshot(indexed_db, derive_result):
    cov = graph.coverage(indexed_db)
    assert cov == EXPECTED_COVERAGE, (
        "coverage output changed -- if this is an intended improvement, update "
        "EXPECTED_COVERAGE in this test and say why in the PR; if not, it's a "
        "regression in extraction correctness"
    )


def test_run_all_summary_matches_snapshot(derive_result):
    assert derive_result["unresolved_calls"] == 14
    assert derive_result["undefined_entities"] == 3
    assert derive_result["adabas_entities_merged"] == 2
    assert derive_result["orphans"] == 11
    assert derive_result["transaction_scopes"] == 3
