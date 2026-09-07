"""Guards on issue #94: folding sme-notes.md content into module/entity/
executive/test-case briefs as an advisory-only section.

Uses the same real, fully-derived fixture (`indexed_db`) test_brief.py and
test_executive_brief.py already exercise for MMP0100/MILL-ORDER, plus a
minimal synthetic member (mirroring test_test_batch.py's FAKEMOD pattern)
for test_case_brief, which needs test_case rows the bundled fixture doesn't
derive by default.
"""

from __future__ import annotations

import re
import sqlite3

from mfdoc import testplan
from mfdoc.brief import entity_brief, executive_brief, module_brief
from mfdoc.db import SCHEMA, insert
from mfdoc.redact import NULL_REDACTOR, Redactor

# Imported via the module (not `from mfdoc.testplan import test_case_brief`)
# so pytest doesn't try to collect the function itself as a test case --
# its name happens to start with `test_`.
_test_case_brief = testplan.test_case_brief

SME_HEADING = "## SME notes (human-provided context, not verified against source)"


# --------------------------------------------------------------- module_brief

def test_module_brief_includes_sme_notes_when_matching(indexed_db):
    notes = {None: "General SME context for the whole project.",
              "mmp0100": "MMP0100-specific SME note about a known gotcha."}
    out = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR, sme_notes=notes)
    assert SME_HEADING in out
    assert "General SME context for the whole project." in out
    assert "MMP0100-specific SME note about a known gotcha." in out


def test_module_brief_omits_sme_notes_when_no_sme_notes_given(indexed_db):
    out = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR)
    assert SME_HEADING not in out


def test_module_brief_omits_sme_notes_when_none_match(indexed_db):
    notes = {"some-other-member": "Not relevant to MMP0100."}
    out = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR, sme_notes=notes)
    assert SME_HEADING not in out
    assert "Not relevant to MMP0100." not in out


def test_module_brief_redacts_sme_notes_text(indexed_db):
    redact = Redactor(patterns=[r"SECRET-VALUE"], enabled=True)
    notes = {"mmp0100": "Watch out for SECRET-VALUE in this module."}
    out = module_brief(indexed_db, "MMP0100", redact=redact, sme_notes=notes)
    assert SME_HEADING in out
    assert "SECRET-VALUE" not in out
    assert "[REDACTED]" in out


def test_module_brief_sme_notes_section_never_carries_a_citation_marker(indexed_db):
    """The section must read as advisory prose, never as a cited claim --
    guard against a future edit accidentally wrapping the note text in
    citation syntax."""
    notes = {"mmp0100": "A plain SME observation with no citation of its own."}
    out = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR, sme_notes=notes)
    section = out.split(SME_HEADING, 1)[1]
    assert not re.search(r"\[\[[A-Za-z0-9_-]+:[^\]]*\]\]", section)


# --------------------------------------------------------------- entity_brief

def test_entity_brief_includes_sme_notes_when_matching(indexed_db):
    notes = {"mill-order": "SME note about the MILL-ORDER store."}
    out = entity_brief(indexed_db, "MILL-ORDER", redact=NULL_REDACTOR, sme_notes=notes)
    assert SME_HEADING in out
    assert "SME note about the MILL-ORDER store." in out


def test_entity_brief_omits_sme_notes_when_no_match(indexed_db):
    out = entity_brief(indexed_db, "MILL-ORDER", redact=NULL_REDACTOR)
    assert SME_HEADING not in out
    notes = {"unrelated-entity": "Irrelevant."}
    out2 = entity_brief(indexed_db, "MILL-ORDER", redact=NULL_REDACTOR, sme_notes=notes)
    assert SME_HEADING not in out2


def test_entity_brief_redacts_sme_notes_text(indexed_db):
    redact = Redactor(patterns=[r"HIDE-ME"], enabled=True)
    notes = {"mill-order": "Contains HIDE-ME token."}
    out = entity_brief(indexed_db, "MILL-ORDER", redact=redact, sme_notes=notes)
    assert "HIDE-ME" not in out
    assert "[REDACTED]" in out


# ------------------------------------------------------------ executive_brief

def _member_with_rule_candidates(conn) -> str:
    row = conn.execute(
        """
        SELECT m.name AS name FROM member m
         WHERE EXISTS (SELECT 1 FROM rule_candidate rc WHERE rc.member_id = m.id)
         ORDER BY m.name LIMIT 1
        """
    ).fetchone()
    assert row is not None, "fixture has no member with rule candidates to exercise"
    return row["name"]


def test_executive_brief_includes_sme_notes_when_matching(indexed_db):
    member = _member_with_rule_candidates(indexed_db)
    notes = {member.lower(): "Executive-level SME context for this member."}
    out = executive_brief(indexed_db, member, redact=NULL_REDACTOR, sme_notes=notes)
    assert SME_HEADING in out
    assert "Executive-level SME context for this member." in out


def test_executive_brief_omits_sme_notes_when_no_match(indexed_db):
    member = _member_with_rule_candidates(indexed_db)
    out = executive_brief(indexed_db, member, redact=NULL_REDACTOR)
    assert SME_HEADING not in out


def test_executive_brief_redacts_sme_notes_text(indexed_db):
    member = _member_with_rule_candidates(indexed_db)
    redact = Redactor(patterns=[r"TOPSECRET"], enabled=True)
    notes = {member.lower(): "Mentions TOPSECRET here."}
    out = executive_brief(indexed_db, member, redact=redact, sme_notes=notes)
    assert "TOPSECRET" not in out
    assert "[REDACTED]" in out


# ------------------------------------------------------------- test_case_brief

def _seed_one_test_case(conn) -> None:
    conn.execute("INSERT INTO member (id, name, dialect) VALUES (1, 'FAKEMOD', 'natural')")
    conn.execute("INSERT INTO source_line (member_id, line_no, text) VALUES (1, 1, 'irrelevant')")
    rc_id = insert(
        conn, "rule_candidate", member_id=1, line_no=1, construct="IF",
        condition="COND-1", raw="IF COND-1",
    )
    insert(
        conn, "test_case", member_id=1, kind="unit", rule_candidate_id=rc_id,
        scenario_name="FAKEMOD:BR-001",
        given_json='{"parameters": [], "mocks": {"entities": [], "callees": []}}',
        when_json='{"construct": "IF", "condition": "X", "citation": "[[FAKEMOD:1]]"}',
        then_json='{"citation": "[[FAKEMOD:1]]", "source_excerpt": []}',
        status="characterization", citation="FAKEMOD:1", confidence="verified",
    )
    conn.commit()


def _fakemod_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _seed_one_test_case(conn)
    return conn


def test_test_case_brief_includes_sme_notes_when_matching():
    conn = _fakemod_conn()
    notes = {None: "General note.", "fakemod": "FAKEMOD-specific note."}
    out = _test_case_brief(conn, "FAKEMOD", sme_notes=notes)
    assert SME_HEADING in out
    assert "General note." in out
    assert "FAKEMOD-specific note." in out


def test_test_case_brief_omits_sme_notes_when_no_match():
    conn = _fakemod_conn()
    out = _test_case_brief(conn, "FAKEMOD")
    assert SME_HEADING not in out
    out2 = _test_case_brief(conn, "FAKEMOD", sme_notes={"other": "irrelevant"})
    assert SME_HEADING not in out2


def test_test_case_brief_redacts_sme_notes_text():
    conn = _fakemod_conn()
    redact = Redactor(patterns=[r"SEKRIT"], enabled=True)
    notes = {"fakemod": "Contains SEKRIT value."}
    out = _test_case_brief(conn, "FAKEMOD", redact=redact, sme_notes=notes)
    assert "SEKRIT" not in out
    assert "[REDACTED]" in out


# ------------------------------------------------------- build_prompt (e2e-ish)

def test_build_prompt_carries_the_sme_notes_section_from_the_brief():
    """batch.build_prompt never re-parses or drops any part of the brief it
    is given -- it just wraps it under a `# Fact brief` heading -- so a
    brief that includes the SME notes section must still show it, in the
    same relative position (after the brief's own content), once wrapped
    into a full prompt."""
    from mfdoc.batch import build_prompt

    conn = _fakemod_conn()
    notes = {"fakemod": "Prompt-visible SME note."}
    fact_brief = _test_case_brief(conn, "FAKEMOD", sme_notes=notes)
    prompt = build_prompt(fact_brief, "writing rules text", "template text")
    assert SME_HEADING in prompt
    assert "Prompt-visible SME note." in prompt
    # The SME section must land inside the "# Fact brief" part of the
    # prompt, not spliced in elsewhere (e.g. before the writing rules).
    fact_brief_section = prompt.split("# Fact brief", 1)[1]
    assert SME_HEADING in fact_brief_section
