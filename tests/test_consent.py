import pytest

from harness.consent import NO, UNCLEAR, YES, classify, is_withdrawal, normalize


@pytest.mark.parametrize("text", [
    "Yes", "yes please", "Yes, you may.", "yes you can", "go ahead", "okay", "sure", "I agree", "please do",
    "yes abeg do am", "Yes o!", "ehn", "Bẹ́ẹ̀ni", "beeni", "ee", "eh to", "to", "e jowo", "yauwa",
])
def test_clear_yes_in_four_languages(text):
    assert classify(text) == YES


@pytest.mark.parametrize("text", [
    "no", "No thanks", "stop", "wait", "I changed my mind", "don't", "yes but don't tell my mum", "rara", "maybe", "not sure",
])
def test_negation_always_wins(text):
    assert classify(text) == NO


@pytest.mark.parametrize("text", [
    "", "   ", "I want to go home", "my uncle is outside", "to the shop", "what do you mean", "yes " * 8,
])
def test_anything_else_is_unclear_and_never_consent(text):
    assert classify(text) == UNCLEAR


def test_diacritics_and_apostrophes_are_stripped():
    assert normalize("Bẹ́ẹ̀ni") == "beeni"
    assert normalize("don’t") == normalize("don't") == "dont"


def test_withdrawal_needs_a_short_refusal():
    assert is_withdrawal("no stop") and is_withdrawal("I changed my mind")
    assert not is_withdrawal("I don't know where my mum is")
    assert not is_withdrawal("yes")
