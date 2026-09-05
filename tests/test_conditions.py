"""Unit tests for the deterministic comparison-direction helpers
(`mfdoc.conditions`) that back validate.py's reversed-condition check."""

from __future__ import annotations

import re

from mfdoc.conditions import OUTCOME_FIELD, comparisons_in, invert, outcome_field_from_options, prose_polarity


def test_comparisons_in_finds_outcome_field_equality():
    assert comparisons_in("IF #RETURN-CODE = '0000'") == [
        {"field": "#RETURN-CODE", "other_field": None, "literal": "0000", "polarity": "eq"}
    ]


def test_comparisons_in_finds_outcome_field_inequality_ne_and_symbolic():
    assert comparisons_in("IF #RETURN-CODE NE '0000'") == [
        {"field": "#RETURN-CODE", "other_field": None, "literal": "0000", "polarity": "ne"}
    ]
    assert comparisons_in("IF #RETURN-CODE <> '0000'") == [
        {"field": "#RETURN-CODE", "other_field": None, "literal": "0000", "polarity": "ne"}
    ]


def test_comparisons_in_finds_relational_operators_symbolic_and_word():
    assert comparisons_in("IF #RETURN-CODE > '4'") == [
        {"field": "#RETURN-CODE", "other_field": None, "literal": "4", "polarity": "gt"}
    ]
    assert comparisons_in("IF #RETURN-CODE GE '4'") == [
        {"field": "#RETURN-CODE", "other_field": None, "literal": "4", "polarity": "ge"}
    ]
    assert comparisons_in("IF #RETURN-CODE < '4'") == [
        {"field": "#RETURN-CODE", "other_field": None, "literal": "4", "polarity": "lt"}
    ]
    assert comparisons_in("IF #RETURN-CODE LE '4'") == [
        {"field": "#RETURN-CODE", "other_field": None, "literal": "4", "polarity": "le"}
    ]


def test_comparisons_in_finds_every_comparison_in_a_compound_condition():
    """AND/OR compounds were never actually unhandled -- comparisons_in scans
    the whole condition string rather than parsing boolean structure, so it
    already finds every outcome-field comparison in a compound. This pins
    that down explicitly instead of leaving it as an unproven assumption."""
    assert comparisons_in("#RETURN-CODE = '0000' AND #STATUS = 'OK'") == [
        {"field": "#RETURN-CODE", "other_field": None, "literal": "0000", "polarity": "eq"},
        {"field": "#STATUS", "other_field": None, "literal": "OK", "polarity": "eq"},
    ]
    assert comparisons_in("#RETURN-CODE = '0000' OR #RETURN-CODE = '0004'") == [
        {"field": "#RETURN-CODE", "other_field": None, "literal": "0000", "polarity": "eq"},
        {"field": "#RETURN-CODE", "other_field": None, "literal": "0004", "polarity": "eq"},
    ]


def test_comparisons_in_ignores_non_outcome_fields():
    """A reversed check on an unrelated field (a coil/order id, say) isn't the
    failure mode this exists for -- flagging it would just be noise."""
    assert comparisons_in("IF #COIL-ID = '12345'") == []


def test_comparisons_in_finds_field_to_field_comparison_on_outcome_field():
    assert comparisons_in("IF #RETURN-CODE = #EXPECTED-CODE") == [
        {"field": "#RETURN-CODE", "other_field": "#EXPECTED-CODE", "literal": None, "polarity": "eq"}
    ]
    # outcome-field name matching regardless of which side it's on
    assert comparisons_in("IF #EXPECTED-CODE = #RETURN-CODE") == [
        {"field": "#RETURN-CODE", "other_field": "#EXPECTED-CODE", "literal": None, "polarity": "eq"}
    ]


def test_comparisons_in_ignores_field_to_field_comparison_with_no_outcome_field():
    assert comparisons_in("IF #COIL-ID = #ORDER-ID") == []


def test_comparisons_in_ignores_no_comparison_and_none():
    assert comparisons_in("no comparison here at all") == []
    assert comparisons_in(None) == []


def test_comparisons_in_supports_custom_outcome_field_pattern():
    """A project with different outcome-field naming conventions can supply
    its own pattern via options.validate.outcome_field_pattern instead of
    getting no coverage at all."""
    custom = re.compile(r"\bRESULT\b", re.IGNORECASE)
    assert comparisons_in("IF #RESULT = '1'", outcome_field=custom) == [
        {"field": "#RESULT", "other_field": None, "literal": "1", "polarity": "eq"}
    ]
    assert comparisons_in("IF #RESULT = '1'") == []


def test_outcome_field_from_options_defaults_to_built_in_pattern():
    assert outcome_field_from_options(None) is OUTCOME_FIELD
    assert outcome_field_from_options({}) is OUTCOME_FIELD
    assert outcome_field_from_options({"validate": {}}) is OUTCOME_FIELD


def test_outcome_field_from_options_uses_configured_pattern():
    pattern = outcome_field_from_options({"validate": {"outcome_field_pattern": r"\bRESULT\b"}})
    assert pattern.search("#RESULT")
    assert not pattern.search("#RETURN-CODE")


def test_invert_round_trips_equality():
    assert invert("eq") == "ne"
    assert invert("ne") == "eq"


def test_invert_covers_relational_operators():
    assert invert("gt") == "le"
    assert invert("le") == "gt"
    assert invert("lt") == "ge"
    assert invert("ge") == "lt"


def test_prose_polarity_detects_negation_near_literal():
    assert prose_polarity("the code is not '0000' on failure", "0000") == "ne"


def test_prose_polarity_defaults_to_equality_with_no_negation():
    assert prose_polarity("the code equals '0000' on success", "0000") == "eq"


def test_prose_polarity_none_when_literal_absent():
    assert prose_polarity("nothing relevant here", "0000") is None


def test_prose_polarity_ignores_a_negation_outside_the_window():
    """A negation far earlier in the sentence, about something else
    entirely, must not bleed into the reading of this literal."""
    long_prefix = "not " + ("filler word " * 20)
    sentence = long_prefix + "the code equals '0000'"
    assert prose_polarity(sentence, "0000", window=40) == "eq"


def test_prose_polarity_reads_a_quoted_ne_operator_as_negation():
    """A sentence commonly quotes the raw condition verbatim right next to
    its citation -- "IF #RETURN-CODE NE '****'" -- before any plain-English
    explanation. The bare keyword operator must register as negation on its
    own, not rely on a later English clause to get the direction right."""
    assert prose_polarity("the condition reads `IF X NE '****'` here", "****") == "ne"


def test_prose_polarity_reads_symbolic_inequality_operators_as_negation():
    assert prose_polarity("quoted as `X <> '****'`", "****") == "ne"
    assert prose_polarity("quoted as `X != '****'`", "****") == "ne"


def test_prose_polarity_does_not_misread_ne_inside_an_unrelated_word():
    """`NE` must only fire as its own word -- not as a substring of an
    unrelated one that happens to end in it."""
    assert prose_polarity("drawn from the online '0000' queue", "0000") == "eq"


def test_prose_polarity_detects_greater_than_wording():
    assert prose_polarity("the code is greater than '4'", "4") == "gt"
    assert prose_polarity("the code exceeds '4'", "4") == "gt"


def test_prose_polarity_detects_less_than_wording():
    assert prose_polarity("the code is less than '4'", "4") == "lt"
    assert prose_polarity("the code is fewer than '4'", "4") == "lt"


def test_prose_polarity_ignores_above_below_as_cross_reference_words():
    """'above'/'below' are common in prose that cross-references other
    content ("rule 2 above") rather than describing a comparison -- treating
    them as greater-than/less-than would false-positive on exactly that
    phrasing."""
    assert prose_polarity("the code equals '4' (see rule 2 above)", "4") == "eq"
    assert prose_polarity("the code equals '4' (see the note below)", "4") == "eq"


def test_prose_polarity_detects_at_least_wording():
    assert prose_polarity("the code is at least '4'", "4") == "ge"


def test_prose_polarity_detects_at_most_wording():
    assert prose_polarity("the code is at most '4'", "4") == "le"


def test_prose_polarity_no_more_than_reads_as_at_most_not_greater_than():
    """'no more than' must read as at-most (le) -- it must not be caught by
    the plain 'more than' phrasing that alone would mean greater-than (gt)."""
    assert prose_polarity("the code is no more than '4'", "4") == "le"


def test_prose_polarity_no_less_than_reads_as_at_least_not_less_than():
    assert prose_polarity("the code is no less than '4'", "4") == "ge"
