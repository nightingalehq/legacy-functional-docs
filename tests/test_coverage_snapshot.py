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
"""

from __future__ import annotations

from mfdoc import graph

EXPECTED_COVERAGE = {
    "members": 10,
    "code_members": 4,
    "source_lines": 260,
    "unparsed_lines": 2,
    "line_recognition_rate": 0.9923,
    "entities": 11,
    "entities_with_definition": 7,
    "entity_definition_rate": 0.6364,
    "entity_fields": 40,
    "data_accesses": 9,
    "rule_candidates": 29,
    "invocation_edges": 12,
    "invocations_resolved": 2,
    "call_resolution_rate": 0.1667,
    "dynamic_call_edges": 1,
    "include_edges": 6,
    "includes_resolved": 0,
    "include_resolution_rate": 0.0,
    "gaps_high": 17,
    "gaps_total": 22,
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
    assert derive_result["adabas_entities_merged"] == 1
    assert derive_result["orphans"] == 2
    assert derive_result["transaction_scopes"] == 3
