"""Unit tests for the deterministic comparison-direction helpers
(`mfdoc.conditions`) that back validate.py's reversed-condition check."""

from __future__ import annotations

from mfdoc.conditions import comparisons_in, invert, prose_polarity


def test_comparisons_in_finds_outcome_field_equality():
    assert comparisons_in("IF #RETURN-CODE = '0000'") == [
        {"field": "#RETURN-CODE", "literal": "0000", "polarity": "eq"}
    ]


def test_comparisons_in_finds_outcome_field_inequality_ne_and_symbolic():
    assert comparisons_in("IF #RETURN-CODE NE '0000'") == [
        {"field": "#RETURN-CODE", "literal": "0000", "polarity": "ne"}
    ]
    assert comparisons_in("IF #RETURN-CODE <> '0000'") == [
        {"field": "#RETURN-CODE", "literal": "0000", "polarity": "ne"}
    ]


def test_comparisons_in_ignores_non_outcome_fields():
    """A reversed check on an unrelated field (a coil/order id, say) isn't the
    failure mode this exists for -- flagging it would just be noise."""
    assert comparisons_in("IF #COIL-ID = '12345'") == []


def test_comparisons_in_ignores_field_to_field_and_no_literal():
    assert comparisons_in("IF #RETURN-CODE = #EXPECTED-CODE") == []
    assert comparisons_in("no comparison here at all") == []
    assert comparisons_in(None) == []


def test_comparisons_in_requires_a_literal_side():
    """Two outcome-shaped identifiers either side of `=` isn't a literal
    comparison -- there's no "the doc says the wrong direction" claim
    possible without a concrete value to check narrative prose against."""
    assert comparisons_in("IF #STATUS = #OTHER-STATUS") == []


def test_invert_round_trips():
    assert invert("eq") == "ne"
    assert invert("ne") == "eq"


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
