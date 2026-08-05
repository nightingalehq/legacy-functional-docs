"""Guards on the fact-brief generator (brief.py)."""

from __future__ import annotations

from mfdoc.brief import _rule_id, entity_brief, module_brief
from mfdoc.redact import NULL_REDACTOR


def test_module_brief_surfaces_only_lexicon_terms_actually_present(indexed_db, project_lexicon):
    """options.narrative.lexicon has 7 entries; MMP0100's own facts only
    mention some of them (CONF, GRADE-CODE, MILL-ORDER, PART, RLSD) -- the
    brief must show exactly those, not the whole glossary dumped in
    regardless of relevance (issue 4.9). HEAT-NO and CAST-DATE never appear
    in MMP0100's source, so they must be absent."""
    brief = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR, lexicon=project_lexicon)
    assert "## Business vocabulary" in brief
    for present in ("CONF", "GRADE-CODE", "MILL-ORDER", "PART", "RLSD"):
        assert f"`{present}` ->" in brief, f"expected lexicon entry for {present}"
    for absent in ("HEAT-NO", "CAST-DATE"):
        assert f"`{absent}` ->" not in brief, f"{absent} doesn't appear in MMP0100 and should be filtered out"


def test_module_brief_omits_vocabulary_section_when_no_lexicon_given(indexed_db):
    """Default behaviour (no lexicon passed) must be unchanged -- this is
    additive, not a required section."""
    brief = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR)
    assert "## Business vocabulary" not in brief


def test_entity_brief_also_surfaces_relevant_lexicon_terms(indexed_db, project_lexicon):
    """MILL-ORDER's own entity brief mentions GRADE-CODE among its fields --
    the same filtering applies to entity briefs, not just module briefs."""
    brief = entity_brief(indexed_db, "MILL-ORDER", redact=NULL_REDACTOR, lexicon=project_lexicon)
    assert "`GRADE-CODE` -> steel grade" in brief


def test_included_copycode_rules_surface_in_the_including_modules_brief(indexed_db):
    """MMP9100 INCLUDEs MMC0100, which has its own real business rule ('X9'
    grade check). Before this fix, that rule was attributed only to
    MMC0100's own brief -- a reader of MMP9100 never saw it, so a module doc
    could look complete and still miss a rule the module actually depends
    on (issue 4.2)."""
    brief = module_brief(indexed_db, "MMP9100", redact=NULL_REDACTOR)
    assert "MMC0100" in brief
    assert "X9" in brief, f"copycode's rule condition missing from MMP9100's brief:\n{brief}"
    assert "[[MMC0100:2]]" in brief, "copycode rule must cite the copycode's own line, not MMP9100's"


def test_copycode_briefed_directly_still_shows_its_own_rule(indexed_db):
    """Briefing the copycode member itself must be unaffected by the fix above."""
    brief = module_brief(indexed_db, "MMC0100", redact=NULL_REDACTOR)
    assert "X9" in brief
    assert "[[MMC0100:2]]" in brief


def test_included_copycode_rule_id_is_qualified_with_the_copycodes_own_name(indexed_db):
    """The rule lives in MMC0100, so its ID must be MMC0100:BR-001 in both
    MMP9100's brief (where it's inherited) and MMC0100's own brief -- the
    same rule must carry the same ID no matter which brief surfaces it."""
    including = module_brief(indexed_db, "MMP9100", redact=NULL_REDACTOR)
    direct = module_brief(indexed_db, "MMC0100", redact=NULL_REDACTOR)
    assert "MMC0100:BR-001" in including
    assert "MMC0100:BR-001" in direct
    assert "MMP9100:BR-001" not in including, "inherited rule must not be renumbered under the including module"


def test_rule_id_is_qualified_with_the_member_name():
    """A bare BR-003 would mean a different rule in every module that has
    one -- the ID must be unique system-wide, not just within one module's
    doc (issue 4.8)."""
    assert _rule_id("MMP0100", 3) == "MMP0100:BR-003"
    assert _rule_id("MMP0200", 3) == "MMP0200:BR-003"


def test_module_brief_assigns_stable_rule_ids_in_source_order(indexed_db):
    brief = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR)
    assert "MMP0100:BR-001" in brief
    # The IF NO RECORDS FOUND at line 34 is the first rule candidate by line
    # number, so it must carry BR-001, not some other position.
    idx = brief.index("MMP0100:BR-001")
    nearby = brief[idx:idx + 120]
    assert "[[MMP0100:34]]" in nearby, f"BR-001 did not land on the expected first rule:\n{nearby}"


def test_module_brief_rule_ids_are_stable_across_regeneration(indexed_db):
    """Re-briefing the same unchanged member must reproduce the same IDs --
    that stability is the entire point of the feature."""
    first = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR)
    second = module_brief(indexed_db, "MMP0100", redact=NULL_REDACTOR)
    assert first == second
