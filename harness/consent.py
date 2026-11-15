"""Deterministic reading of a caller's answer to a consent question.

Contacting a responder with a child's words needs a clear yes. The matcher is deliberately closed and
conservative, and no model is involved:

* An answer is a YES only when it is short, every word is a known yes or filler word, and at least one is a
  real yes word. "I want to go home" or "yes but don't tell my mum" are therefore never a yes.
* Any negation or withdrawal word makes it a NO, whatever else is said.
* Everything else is UNCLEAR, which the caller treats as "not consented"; the voice agent asks again.

Vocabularies cover English, Nigerian Pidgin, Yoruba and Hausa. Diacritics are stripped before matching
(Yoruba "bẹ́ẹ̀ni" is read as "beeni").
"""

from __future__ import annotations

import re
import unicodedata

YES, NO, UNCLEAR = "yes", "no", "unclear"
MAX_WORDS = 6

YES_WORDS = frozenset({
    # English
    "yes", "yeah", "yep", "yup", "sure", "okay", "ok", "agree", "ahead", "definitely", "absolutely",
    # Nigerian Pidgin
    "ehn", "yessir", "abeg",
    # Yoruba
    "beeni", "beni", "joo",
    # Hausa
    "ee", "eh", "yauwa", "yarda",
})
# Words that may surround a yes ("yes you may", "go ahead", "yes abeg do am") but never make one alone.
FILLER_WORDS = frozenset({
    "please", "you", "may", "can", "could", "i", "it", "do", "does", "am", "go", "thank", "thanks", "so", "that",
    "is", "fine", "na", "dey", "o", "oh", "jowo", "e", "now", "sir", "ma", "madam", "my", "of", "course",
    "contact", "them", "text", "call", "tell", "ina", "to", "sha", "very", "much", "alright", "right",
})
# Answers that are only a yes as a whole (Hausa "to" means "okay"), not as a word inside a longer sentence.
YES_PHRASES = frozenset({"to", "to eh", "to ee", "e jowo", "joo e", "please do", "do it", "you may", "you can", "i do"})
NO_WORDS = frozenset({
    "no", "nope", "nah", "not", "dont", "don", "never", "stop", "wait", "cancel", "rara", "aa", "mba", "ba",
    "hold", "later", "unsure", "maybe",
})
NO_PHRASES = ("changed my mind", "change my mind", "not sure", "dont want", "do not", "no be so", "a a")


def normalize(text: str) -> str:
    """Lower-case, drop diacritics and apostrophes, keep only letters and single spaces."""
    text = unicodedata.normalize("NFKD", (text or "").lower().replace("’", "").replace("'", ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z ]", " ", text).split())


def classify(text: str) -> str:
    """Return YES, NO or UNCLEAR for a caller's reply to a consent question."""
    answer = normalize(text)
    if not answer:
        return UNCLEAR
    words = answer.split()
    if any(word in NO_WORDS for word in words) or any(phrase in answer for phrase in NO_PHRASES):
        return NO
    if answer in YES_PHRASES:
        return YES
    if len(words) <= MAX_WORDS and any(word in YES_WORDS for word in words) \
            and all(word in YES_WORDS or word in FILLER_WORDS for word in words):
        return YES
    return UNCLEAR


def is_yes(text: str) -> bool:
    return classify(text) == YES


def is_no(text: str) -> bool:
    return classify(text) == NO


WITHDRAWAL_MAX_WORDS = 4


def is_withdrawal(text: str) -> bool:
    """A short, unprompted refusal ("no", "stop", "I changed my mind") that withdraws consent already given.

    Long sentences that merely contain a negation ("I don't know where my mum is") are not withdrawals.
    """
    return classify(text) == NO and len(normalize(text).split()) <= WITHDRAWAL_MAX_WORDS
