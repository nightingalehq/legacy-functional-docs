"""Guards on the fact-brief generator (brief.py)."""

from __future__ import annotations

from mfdoc.brief import module_brief
from mfdoc.redact import NULL_REDACTOR


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
