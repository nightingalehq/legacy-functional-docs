"""Deterministic comparison-direction cross-check.

Extraction already records a condition's exact operator, verbatim from source
(`rule_candidate.condition`), but nothing checked that a narrative sentence
citing that condition describes the same direction. Citation resolution only
confirms a citation points at a real, in-range line — a model can cite a
perfectly real line and still narrate the logical inverse of what it says (a
reversed comparison on an "outcome" field silently swaps a documented
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
# reversed comparison silently swaps a pass/fail interpretation. A false
# positive here (flagging an unrelated field whose name happens to contain
# one of these words) costs more trust in the check than a missed outcome
# field elsewhere would, so keep the list to common 4GL/COBOL naming shapes
# rather than trying to be exhaustive.
OUTCOME_FIELD = re.compile(
    r"(RETURN[-_]?CODE|RESP(?:ONSE)?[-_]?CODE|RET[-_]?CODE|ERROR[-_]?CODE|"
    r"\bRC\b|\bSTATUS\b|\bSTAT\b|\bFLAG\b)",
    re.IGNORECASE,
)


def outcome_field_from_options(options: dict | None) -> re.Pattern:
    """The outcome-field pattern to use, from `options.validate.outcome_field_pattern`
    in project.yml, or the built-in `OUTCOME_FIELD` denylist if unset.

    There is no attempt to merge a supplied pattern with the built-in one --
    a project that names its outcome fields differently supplies its own
    complete pattern, the same way `options.redact.patterns` replaces rather
    than extends.
    """
    pattern = ((options or {}).get("validate") or {}).get("outcome_field_pattern")
    if not pattern:
        return OUTCOME_FIELD
    return re.compile(pattern, re.IGNORECASE)


_IDENT = r"[#@$&A-Za-z][\w\-.]*"
_LITERAL = r"'[^']*'|\"[^\"]*\""

# One `<side> <op> <side>` comparison, either side an identifier or a quoted
# literal. Deliberately not a general condition parser -- no operator
# precedence, no parenthesisation -- but it does find every comparison in a
# compound AND/OR condition (a plain scan over the string, not a boolean-
# structure parse) and covers equality, inequality, and the four relational
# operators.
COMPARISON = re.compile(
    rf"(?P<lhs>{_LITERAL}|{_IDENT})\s*"
    rf"(?P<op>>=|<=|<>|!=|=|>|<|\bNE\b|\bEQ\b|\bGE\b|\bLE\b|\bGT\b|\bLT\b)\s*"
    rf"(?P<rhs>{_LITERAL}|{_IDENT})",
    re.IGNORECASE,
)

# operator text -> polarity. `invert` is this mapping's logical negation:
# NOT(eq)=ne, NOT(gt)=le, NOT(lt)=ge (and their reverses) -- what the ELSE
# branch of an IF means for each operator shape.
_POLARITY_BY_OP = {
    "=": "eq", "EQ": "eq",
    "<>": "ne", "!=": "ne", "NE": "ne",
    ">": "gt", "GT": "gt",
    "<": "lt", "LT": "lt",
    ">=": "ge", "GE": "ge",
    "<=": "le", "LE": "le",
}
_INVERSE = {"eq": "ne", "ne": "eq", "gt": "le", "le": "gt", "lt": "ge", "ge": "lt"}


def comparisons_in(condition: str | None, outcome_field: re.Pattern = OUTCOME_FIELD) -> list[dict]:
    """Every outcome-field comparison in a raw condition string.

    Returns `{"field", "other_field", "literal", "polarity"}` rows, `polarity`
    one of `"eq"`/`"ne"`/`"gt"`/`"lt"`/`"ge"`/`"le"`.

    Two comparison shapes are recognised, both requiring at least one side to
    match `outcome_field` (defaults to the module-level `OUTCOME_FIELD`, but a
    project with different naming conventions may supply its own pattern via
    `options.validate.outcome_field_pattern`):

    - `<outcome-field> <op> <literal>` -- `literal` set, `other_field` `None`.
    - `<outcome-field> <op> <other-field>` -- `literal` `None`, `other_field`
      set to the raw identifier text on the other side. There is no concrete
      value here to check narrative prose against directly; the caller
      matches the *other field's own name* appearing in the narrative
      instead (see validate.py).

    A compound condition (`... AND ...`/`... OR ...`) yields one row per
    comparison found -- this is a plain scan over the condition string, not a
    boolean-structure parse, so it makes no attempt to model how the
    comparisons combine.
    """
    if not condition:
        return []
    out: list[dict] = []
    for m in COMPARISON.finditer(condition):
        lhs, op, rhs = m.group("lhs"), m.group("op"), m.group("rhs")
        lhs_is_literal = lhs[0] in "'\""
        rhs_is_literal = rhs[0] in "'\""
        polarity = _POLARITY_BY_OP[op.upper()]
        if lhs_is_literal != rhs_is_literal:
            field, literal = (rhs, lhs) if lhs_is_literal else (lhs, rhs)
            if not outcome_field.search(field):
                continue
            out.append({
                "field": field, "other_field": None,
                "literal": literal.strip("'\""), "polarity": polarity,
            })
        elif not lhs_is_literal and not rhs_is_literal:
            if outcome_field.search(lhs):
                field, other = lhs, rhs
            elif outcome_field.search(rhs):
                field, other = rhs, lhs
            else:
                continue
            out.append({"field": field, "other_field": other, "literal": None, "polarity": polarity})
        # else: both sides literal -- two constants compared, nothing to check
    return out


def invert(polarity: str) -> str:
    return _INVERSE[polarity]


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

# Relational phrasing near a literal, checked in this order because the
# compound "no more/less than" phrases are a substring superset of the plain
# "more/less than" ones -- checking the compound forms first (LE/GE) means a
# "no more than" reads as at-most, not misread as the plain greater-than
# phrase it contains. Deliberately a closed, small set, same rationale as
# _NEGATION_NEAR_LITERAL: under-fire (decline to a plain eq/ne reading) is
# preferable to a false relational read.
_LE_WORDS = re.compile(r"\b(at\s+most|no\s+more\s+than|maximum\s+of)\b", re.IGNORECASE)
_GE_WORDS = re.compile(r"\b(at\s+least|no\s+less\s+than|minimum\s+of)\b", re.IGNORECASE)
# "above"/"below"/"under" are deliberately excluded -- all three are common
# in prose that has nothing to do with a comparison (a cross-reference like
# "rule 2 above", "the section below", "under the circumstances"), and a
# false positive from one of those costs more trust in the check than
# missing a same-sentence "above"/"below" comparison would.
_GT_WORDS = re.compile(r"\b(greater\s+than|more\s+than|exceeds?)\b", re.IGNORECASE)
_LT_WORDS = re.compile(r"\b(less\s+than|fewer\s+than)\b", re.IGNORECASE)


def prose_polarity(sentence: str, literal: str, window: int = 40) -> str | None:
    """The comparison direction `sentence` reads as claiming about `literal`,
    or `None` if `literal` doesn't appear in `sentence` at all.

    Returns one of `"eq"`/`"ne"`/`"gt"`/`"lt"`/`"ge"`/`"le"`. Looks at the
    `window` characters on *either* side of the literal's first appearance
    for a relational or negation marker. Both directions matter: "is not
    '****'" negates from before, but a guard/check phrasing just as commonly
    negates from after ("status is 'CONF'; if not, ..."), and missing the
    latter is a false positive against exactly that phrasing -- and this
    check would rather under-fire than train reviewers to ignore it. A wider
    window than that risks picking up a marker that belongs to an earlier
    or later, unrelated clause instead. No relational wording found falls
    back to the plain equality/negation reading (unchanged from before
    relational operators were supported), so an ordinary "equals"/"is not"
    narration is unaffected.
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
    if _LE_WORDS.search(nearby):
        return "le"
    if _GE_WORDS.search(nearby):
        return "ge"
    if _GT_WORDS.search(nearby):
        return "gt"
    if _LT_WORDS.search(nearby):
        return "lt"
    return "ne" if _NEGATION_NEAR_LITERAL.search(nearby) else "eq"
