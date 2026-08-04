"""Snapshot of coverage() and run_all() against the shipped fixtures.

Any change to these numbers is either a real improvement (update the
snapshot deliberately, in the same PR that explains why) or an unintended
regression (the point of this test). Either way it must be visible in
review rather than discovered later, which a bare "pipeline still runs"
smoke test would not catch.
"""

from __future__ import annotations

from mfdoc import graph

EXPECTED_COVERAGE = {
    "members": 9,
    "code_members": 3,
    "source_lines": 241,
    "unparsed_lines": 1,
    "line_recognition_rate": 0.9959,
    "entities": 11,
    "entities_with_definition": 7,
    "entity_definition_rate": 0.6364,
    "entity_fields": 40,
    "data_accesses": 9,
    "rule_candidates": 21,
    "invocation_edges": 12,
    "invocations_resolved": 2,
    "call_resolution_rate": 0.1667,
    "dynamic_call_edges": 1,
    "include_edges": 6,
    "includes_resolved": 0,
    "include_resolution_rate": 0.0,
    "gaps_high": 17,
    "gaps_total": 20,
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
    assert derive_result["orphans"] == 1
    assert derive_result["transaction_scopes"] == 3
