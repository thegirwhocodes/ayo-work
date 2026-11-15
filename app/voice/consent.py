"""In-call consent, tracked per action and kept deliberately short-lived.

Consent is the caller's own clear "yes" to a specific question the voice model asked in its
immediately preceding turn. Three rules keep it honest:

* It is per action. Agreeing to a text to the responder is not agreement to a text to the
  caller's own phone (which might be read by someone else), and vice versa.
* It is single use. Each yes covers one send; a second send needs a second yes.
* It expires. A yes that has sat unused for CONSENT_TTL_S seconds no longer counts.

A "no" or "stop" withdraws everything. Anything other than a clear yes after a consent question is
treated as a no (fail closed).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

RESPONDER = "responder"      # share what the caller said with the emergency responder (text or call)
CALLER_TEXT = "caller_text"  # send a text message to the caller's own phone

QUESTIONS = {
    RESPONDER: "May I text the emergency responder with what you shared?",
    CALLER_TEXT: "May I text your phone to ask where you are?",
}

# Which consent each emergency action needs.
CONSENT_FOR_ACTION = {
    "send_text_message": RESPONDER,
    "start_phone_call": RESPONDER,
    "request_location": CALLER_TEXT,
}

CONSENT_TTL_S = 180.0

YES = {"yes", "yes please", "yes you may", "yes i agree", "i agree", "please do"}
NO = {"no", "no thanks", "stop", "i changed my mind"}
# Withdrawing consent errs on the side of withdrawing: any of these words in the caller's reply counts as a
# no ("no, stop", "please don't", "I don't know"). Granting needs an exact, clear yes; withdrawing does not.
_NO_WORDS = {"no", "nope", "stop", "dont", "cancel", "nah", "never"}

# The retention length varies ("30 days" can be transcribed as "thirty days"), so the memory question
# is recognised by its fixed opening, never by the number.
MEMORY_QUESTION_PREFIX = "may i remember your support preferences for future calls"


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse spaces. Digits are kept so numbers survive."""
    return " ".join(re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).split())


def is_no(answer: str) -> bool:
    """True for a clear no, and for anything that sounds like one. `answer` is already normalized."""
    words = answer.split()
    return answer in NO or bool(_NO_WORDS & set(words)) or "do not" in answer


def memory_question_asked(model_text: str) -> bool:
    return MEMORY_QUESTION_PREFIX in normalize(model_text)


@dataclass(frozen=True)
class ConsentOutcome:
    granted: Optional[str] = None       # consent kind just granted
    revoked: tuple = ()                 # kinds that were granted and are now withdrawn
    said_no: bool = False               # an explicit no / stop


class ConsentTracker:
    def __init__(self, ttl_s: float = CONSENT_TTL_S, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_s
        self._clock = clock
        self._pending: Optional[str] = None
        self._granted: dict[str, float] = {}

    @property
    def pending(self) -> Optional[str]:
        """The consent question the voice model asked in its last turn, if any."""
        return self._pending

    def observe_model(self, text: str) -> None:
        """Record which consent question (if any) the model's latest turn ended up asking.

        Every model turn replaces the pending question, so a yes only counts when the question was
        asked in the turn immediately before it.
        """
        norm = normalize(text)
        positions = {kind: norm.rfind(normalize(question)) for kind, question in QUESTIONS.items()}
        asked = {kind: pos for kind, pos in positions.items() if pos >= 0}
        self._pending = max(asked, key=asked.get) if asked else None

    def observe_caller(self, text: str) -> ConsentOutcome:
        answer = normalize(text)
        if self._pending:
            kind, self._pending = self._pending, None
            if answer in YES:
                self._granted[kind] = self._clock() + self._ttl
                return ConsentOutcome(granted=kind)
            return ConsentOutcome(revoked=self.revoke(kind), said_no=is_no(answer))
        if is_no(answer):
            return ConsentOutcome(revoked=self.revoke(None), said_no=True)
        return ConsentOutcome()

    def allows(self, kind: str) -> bool:
        expires = self._granted.get(kind)
        if expires is None:
            return False
        if self._clock() > expires:
            del self._granted[kind]
            return False
        return True

    def consume(self, kind: str) -> bool:
        """Spend the caller's yes. True only if a live, unspent consent existed."""
        if self.allows(kind):
            del self._granted[kind]
            return True
        return False

    def revoke(self, kind: Optional[str] = None) -> tuple:
        kinds = [kind] if kind else list(self._granted)
        revoked = tuple(k for k in kinds if self._granted.pop(k, None) is not None)
        return revoked
