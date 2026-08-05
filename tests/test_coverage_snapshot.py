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
"""

from __future__ import annotations

from mfdoc import graph

EXPECTED_COVERAGE = {
    "members": 16,
    "code_members": 8,
    "source_lines": 332,
    "unparsed_lines": 1,
    "line_recognition_rate": 0.997,
    "entities": 13,
    "entities_with_definition": 9,
    "entity_definition_rate": 0.6923,
    "entity_fields": 46,
    "data_accesses": 12,
    "rule_candidates": 31,
    "invocation_edges": 12,
    "invocations_resolved": 2,
    "call_resolution_rate": 0.1667,
    "dynamic_call_edges": 1,
    "include_edges": 7,
    "includes_resolved": 1,
    "include_resolution_rate": 0.1429,
    "gaps_high": 18,
    "gaps_total": 27,
}


def test_coverage_matches_snapshot(indexed_db, derive_result):
    cov = graph.coverage(indexed_db)
    assert cov == EXPECTED_COVERAGE, (
        "coverage output changed -- if this is an intended improvement, update "
        "EXPECTED_COVERAGE in this test and say why in the PR; if not, it's a "
        "regression in extraction correctness"
    )


def test_run_all_summary_matches_snapshot(derive_result):
    assert derive_result["unresolved_calls"] == 12
    assert derive_result["undefined_entities"] == 3
    assert derive_result["adabas_entities_merged"] == 2
    assert derive_result["orphans"] == 4
    assert derive_result["transaction_scopes"] == 3
