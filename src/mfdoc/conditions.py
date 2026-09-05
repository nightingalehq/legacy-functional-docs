"""Deterministic comparison-direction cross-check.

Extraction already records a condition's exact operator, verbatim from source
(`rule_candidate.condition`), but nothing checked that a narrative sentence
citing that condition describes the same direction. Citation resolution only
confirms a citation points at a real, in-range line — a model can cite a
perfectly real line and still narrate the logical inverse of what it says (a
reversed equality check on an "outcome" field silently swaps a documented
pass/fail interpretation). This is exactly the kind of check that needs no
model call: the operator is already sitting in the fact store as plain text.

This module only extracts the deterministic facts (`comparisons_in`) and reads
a narrative sentence's *claimed* direction back out (`prose_polarity`);
`validate.py` does the citation-to-condition matching and raises the actual
finding.
"""

from __future__ import annotations

import re

# Field names this check applies to. Deliberately narrow and conservative --
# these are "outcome" fields (return/response/status codes, flags) where a
# reversed equality check silently swaps a pass/fail interpretation. A false
# positive here (flagging an unrelated field whose name happens to contain
# one of these words) costs more trust in the check than a missed outcome
# field elsewhere would, so keep the list to common 4GL/COBOL naming shapes
# rather than trying to be exhaustive.
OUTCOME_FIELD = re.compile(
    r"(RETURN[-_]?CODE|RESP(?:ONSE)?[-_]?CODE|RET[-_]?CODE|ERROR[-_]?CODE|"
    r"\bRC\b|\bSTATUS\b|\bSTAT\b|\bFLAG\b)",
    re.IGNORECASE,
)

_IDENT = r"[#@$&A-Za-z][\w\-.]*"
_LITERAL = r"'[^']*'|\"[^\"]*\""

# One `<side> <op> <side>` comparison, either side an identifier or a quoted
# literal. Deliberately not a general condition parser: no AND/OR, no
# GT/LT/GE/LE, no field-to-field comparisons -- only the shape the
# reversed-direction failure mode needs (a single field compared to a single
# literal for equality or inequality).
COMPARISON = re.compile(
    rf"(?P<lhs>{_LITERAL}|{_IDENT})\s*(?P<op>=|<>|!=|\bNE\b|\bEQ\b)\s*(?P<rhs>{_LITERAL}|{_IDENT})",
    re.IGNORECASE,
)

_EQ_OPS = {"=", "EQ"}


def comparisons_in(condition: str | None) -> list[dict]:
    """Every `<outcome-field> <op> <literal>` comparison in a raw condition string.

    Returns `{"field", "literal", "polarity"}` rows, `polarity` one of
    `"eq"`/`"ne"` for "field equals literal" / "field does not equal literal".
    Only comparisons where exactly one side is a quoted literal and the other
    matches `OUTCOME_FIELD` are returned.
    """
    if not condition:
        return []
    out: list[dict] = []
    for m in COMPARISON.finditer(condition):
        lhs, op, rhs = m.group("lhs"), m.group("op"), m.group("rhs")
        lhs_is_literal = lhs[0] in "'\""
        rhs_is_literal = rhs[0] in "'\""
        if lhs_is_literal == rhs_is_literal:
            continue  # need exactly one literal side
        field, literal = (rhs, lhs) if lhs_is_literal else (lhs, rhs)
        if not OUTCOME_FIELD.search(field):
            continue
        polarity = "eq" if op.upper() in _EQ_OPS else "ne"
        out.append({"field": field, "literal": literal.strip("'\""), "polarity": polarity})
    return out


def invert(polarity: str) -> str:
    return "ne" if polarity == "eq" else "eq"


# Words a narrative sentence uses to say which branch of an if/else it is
# characterising -- used only to pick whether a sentence's claim should be
# checked against an IF's own condition or against its paired ELSE's
# (logically inverted) condition, when a citation range spans both. No hint
# found means "compare against the IF's own condition only" rather than
# guessing which branch is meant.
SUCCESS_WORDS = re.compile(r"\b(success(?:ful(?:ly)?)?|succeed(?:s|ed)?)\b", re.IGNORECASE)
FAILURE_WORDS = re.compile(
    r"\b(fail(?:ure|ed|s)?|error|reject(?:ed|s)?|backout|abort(?:ed|s|ion)?|unsuccessful)\b",
    re.IGNORECASE,
)

# Words (and, since a sentence commonly quotes the raw condition verbatim
# right next to its citation -- "IF #RETURN-CODE NE '****' [[...]]" -- the
# raw inequality operators too) appearing near a literal value in narrative
# prose that negate an otherwise-implied equality reading of it. Missing the
# operator forms here isn't a near-miss: the *first* occurrence of a literal
# in a sentence is very often that verbatim quotation, so without them this
# always reads such a sentence as claiming equality regardless of which
# operator the quoted condition actually uses, even when a later, plainer
# English clause in the same sentence gets it right. Deliberately a
# denylist, not an allowlist of positive phrasing: most equality claims read
# as plain juxtaposition ("is", "equals", "= "), so there's no single
# reliable positive marker to require, but the negative ones are a closed,
# checkable set.
_NEGATION_NEAR_LITERAL = re.compile(
    r"\b(not|isn't|is\s+not|other\s+than|differs?\s+from|unless|except|"
    r"does(?:n't|\s+not)\s+equal|no\s+longer|NE)\b"
    r"|<>|!=",
    re.IGNORECASE,
)


def prose_polarity(sentence: str, literal: str, window: int = 40) -> str | None:
    """The equality direction `sentence` reads as claiming about `literal`,
    or `None` if `literal` doesn't appear in `sentence` at all.

    Looks at the `window` characters on *either* side of the literal's first
    appearance for a negation marker. Both directions matter: "is not
    '****'" negates from before, but a guard/check phrasing just as commonly
    negates from after ("status is 'CONF'; if not, ..."), and missing the
    latter is a false positive against exactly that phrasing -- and this
    check would rather under-fire than train reviewers to ignore it. A wider
    window than that risks picking up a negation that belongs to an earlier
    or later, unrelated clause instead.
    """
    # Bounded against alphanumerics on both sides -- not `\b`, which only
    # anchors between a word char and a non-word char and so fails to bound
    # a punctuation-only literal like '****' (see validate.py's own
    # BR_REF/CITATION comments for the same lesson learned the hard way).
    # An unbounded substring search would match a literal like "CONF" inside
    # an unrelated word ("confirmed") long before its real, quoted
    # appearance later in the sentence, reading the wrong window entirely.
    m = re.search(
        rf"(?<![A-Za-z0-9]){re.escape(literal)}(?![A-Za-z0-9])", sentence, re.IGNORECASE
    )
    if not m:
        return None
    nearby = sentence[max(0, m.start() - window):min(len(sentence), m.end() + window)]
    return "ne" if _NEGATION_NEAR_LITERAL.search(nearby) else "eq"
