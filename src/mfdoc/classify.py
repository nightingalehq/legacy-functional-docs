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
