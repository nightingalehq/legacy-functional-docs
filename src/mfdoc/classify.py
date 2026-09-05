"""Rule-theme classification: assigns each `rule_candidate` a business
theme for the thematic rules-register rollup (structural.py).

Three-layer fallback, run in this order, never as three independent
passes: (1) a project-defined keyword/regex taxonomy (deterministic,
always runs first, see classify_rules_deterministic); (2) an optional
LLM pass over whatever the taxonomy didn't match (classify_rules_llm,
Task 3); (3) a structural fallback -- the rule's own member's library
-- for anything still unclassified once the first two layers have run.
Every rule ends up classified; nothing is silently dropped.
"""

from __future__ import annotations

import re

from .batch import ModelCaller
from .redact import NULL_REDACTOR, Redactor


def classify_rules_deterministic(conn, taxonomy: dict[str, list[str]]) -> dict:
    """Classify every rule_candidate not already in rule_theme: keyword
    match against `taxonomy` first, else a structural fallback (the
    rule's member's library, or 'uncategorized' if library is NULL).

    Upserts on rule_candidate_id -- calling this again after a taxonomy
    edit reclassifies rows whose current source is 'structural' (never
    already keyword- or llm-classified) rather than duplicating them.
    """
    patterns = {
        theme: [re.compile(p, re.IGNORECASE) for p in patterns]
        for theme, patterns in taxonomy.items()
    }
    rows = conn.execute(
        """
        SELECT rc.id, rc.condition, rc.literals, m.library
          FROM rule_candidate rc
          JOIN member m ON m.id = rc.member_id
         WHERE NOT EXISTS (
             SELECT 1 FROM rule_theme rt
              WHERE rt.rule_candidate_id = rc.id AND rt.source IN ('keyword', 'llm')
         )
        """
    ).fetchall()

    counts = {"keyword": 0, "structural": 0}
    for row in rows:
        haystack = f"{row['condition'] or ''} {row['literals'] or ''}"
        theme = None
        for name, regexes in patterns.items():
            if any(rx.search(haystack) for rx in regexes):
                theme = name
                break
        if theme is not None:
            source = "keyword"
            counts["keyword"] += 1
        else:
            theme = row["library"] or "uncategorized"
            source = "structural"
            counts["structural"] += 1
        conn.execute(
            "INSERT INTO rule_theme (rule_candidate_id, theme, source) VALUES (?, ?, ?) "
            "ON CONFLICT(rule_candidate_id) DO UPDATE SET theme=excluded.theme, source=excluded.source",
            (row["id"], theme, source),
        )
    conn.commit()
    return counts


def classify_rules_llm(conn, caller: ModelCaller, redact: Redactor = NULL_REDACTOR) -> int:
    """Ask the model for a one-word theme for every rule_candidate still
    classified 'structural' (the keyword taxonomy didn't match it).
    Upserts source='llm' for each; a rule the model can't confidently
    theme is left at its existing structural label -- never guessed past
    what the model actually returned."""
    rows = conn.execute(
        """
        SELECT rc.id, rc.condition, rc.literals, m.name AS member_name
          FROM rule_candidate rc
          JOIN member m ON m.id = rc.member_id
          JOIN rule_theme rt ON rt.rule_candidate_id = rc.id
         WHERE rt.source = 'structural'
        """
    ).fetchall()

    reclassified = 0
    for row in rows:
        condition = redact(row["condition"]) or ""
        literals = redact(row["literals"]) or ""
        prompt = (
            "Reply with exactly one short lowercase business-theme word "
            f"(e.g. eligibility, posting, validation) for this rule from "
            f"module {row['member_name']}: condition={condition!r} literals={literals!r}"
        )
        response = caller(prompt)
        theme = response.text.strip().lower().splitlines()[0][:40]
        if not theme:
            continue
        conn.execute(
            "UPDATE rule_theme SET theme=?, source='llm' WHERE rule_candidate_id=?",
            (theme, row["id"]),
        )
        reclassified += 1
    conn.commit()
    return reclassified
