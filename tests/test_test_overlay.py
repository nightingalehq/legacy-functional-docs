"""Guards for the bug/spec curation overlay (testoverlay.py, mfdoc
test-overlay-draft)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from mfdoc import testoverlay
from mfdoc.batch import ModelResponse
from mfdoc.db import SCHEMA, insert


@pytest.fixture
def fakemod_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    insert(
        conn, "test_case", member_id=1, kind="unit", scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": ["MOVE 1 TO #X"]}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()
    return conn


def test_draft_only_review_status_never_overrides_default_status():
    """An overlay entry stuck at review_status: draft (never promoted by a
    human) must leave the scenario at 'characterization' -- exactly like no
    overlay entry existing at all."""
    overlay = {"FAKEMOD:BR-001": {"status": "bug-current", "review_status": "draft", "note": "x"}}
    assert testoverlay.overlay_status_for(overlay, "FAKEMOD:BR-001") == "characterization"


def test_promoted_review_status_applies_the_status():
    overlay = {"FAKEMOD:BR-001": {"status": "bug-current", "review_status": "sme_approved", "note": "x"}}
    assert testoverlay.overlay_status_for(overlay, "FAKEMOD:BR-001") == "bug-current"


def test_unknown_scenario_defaults_to_characterization():
    assert testoverlay.overlay_status_for({}, "NOPE:BR-999") == "characterization"


def test_parse_response_rejects_unknown_scenario_id():
    text = "UNKNOWN:BR-001:\n  status: bug-current\n  review_status: draft\n  note: x\n"
    entries, problems = testoverlay.parse_overlay_response(text, {"FAKEMOD:BR-001"})
    assert problems
    assert "not a known test_case scenario id" in problems[0]


def test_parse_response_forces_draft_even_if_model_claims_otherwise():
    """A model that outputs review_status: sme_approved for its own draft
    must be overridden back to draft -- promotion is a human action only."""
    text = "FAKEMOD:BR-001:\n  status: bug-current\n  review_status: sme_approved\n  note: divergence at line 1\n"
    entries, problems = testoverlay.parse_overlay_response(text, {"FAKEMOD:BR-001"})
    assert not problems
    assert entries["FAKEMOD:BR-001"]["review_status"] == "draft"


def test_draft_overlay_for_member_retries_then_gives_up_on_bad_yaml(fakemod_conn):
    def bad_caller(prompt):
        return ModelResponse(text="not: valid: yaml: at: all: :::", input_tokens=0, output_tokens=0)

    result = testoverlay.draft_overlay_for_member(fakemod_conn, "FAKEMOD", bad_caller)
    assert result["entries"] == {}
    assert result["problems"]


def test_draft_overlay_for_member_accepts_a_valid_divergence_proposal(fakemod_conn):
    def good_caller(prompt):
        return ModelResponse(
            text="FAKEMOD:BR-001:\n  status: bug-current\n  review_status: draft\n"
                 "  note: \"module doc says #X should be 0, code sets it to 1 [[FAKEMOD:1]]\"\n",
            input_tokens=0, output_tokens=0,
        )

    result = testoverlay.draft_overlay_for_member(fakemod_conn, "FAKEMOD", good_caller, module_doc="doc text")
    assert not result["problems"]
    assert "FAKEMOD:BR-001" in result["entries"]
    assert result["entries"]["FAKEMOD:BR-001"]["review_status"] == "draft"


def test_run_overlay_draft_never_overwrites_a_human_promoted_entry(fakemod_conn, tmp_path):
    out_path = tmp_path / "test-overlay.yml"
    testoverlay.save_overlay(out_path, {
        "FAKEMOD:BR-001": {"status": "spec", "review_status": "sme_approved", "note": "confirmed by SME"}
    })

    def caller(prompt):
        return ModelResponse(
            text="FAKEMOD:BR-001:\n  status: bug-current\n  review_status: draft\n  note: model guess\n",
            input_tokens=0, output_tokens=0,
        )

    summary = testoverlay.run_overlay_draft(fakemod_conn, ["FAKEMOD"], caller, out_path)
    assert summary["skipped_promoted"] == 1
    assert summary["drafted"] == 0
    overlay = testoverlay.load_overlay(out_path)
    assert overlay["FAKEMOD:BR-001"]["review_status"] == "sme_approved"
    assert overlay["FAKEMOD:BR-001"]["status"] == "spec"


def test_run_overlay_draft_merges_new_entries_alongside_existing(fakemod_conn, tmp_path):
    out_path = tmp_path / "test-overlay.yml"

    def caller(prompt):
        return ModelResponse(
            text="FAKEMOD:BR-001:\n  status: bug-current\n  review_status: draft\n  note: model guess\n",
            input_tokens=0, output_tokens=0,
        )

    summary = testoverlay.run_overlay_draft(fakemod_conn, ["FAKEMOD"], caller, out_path)
    assert summary["drafted"] == 1
    overlay = testoverlay.load_overlay(out_path)
    assert overlay["FAKEMOD:BR-001"]["status"] == "bug-current"
    assert overlay["FAKEMOD:BR-001"]["review_status"] == "draft"


def test_testplan_run_all_applies_a_promoted_overlay(indexed_db, tmp_path):
    """End-to-end: a promoted overlay entry must change the derived
    test_case row's status; an un-promoted one must not."""
    from mfdoc import testplan

    conn = indexed_db
    overlay_path = tmp_path / "test-overlay.yml"
    testoverlay.save_overlay(overlay_path, {
        "MMP0100:BR-004": {"status": "bug-current", "review_status": "sme_approved", "note": "x"},
    })
    testplan.run_all(conn, member_name="MMP0100", overlay_path=overlay_path)
    row = conn.execute("SELECT status FROM test_case WHERE scenario_name='MMP0100:BR-004'").fetchone()
    assert row["status"] == "bug-current"

    other = conn.execute(
        "SELECT status FROM test_case WHERE scenario_name='MMP0100:BR-007'"
    ).fetchone()
    assert other["status"] == "characterization"
