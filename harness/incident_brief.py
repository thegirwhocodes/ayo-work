"""The structured brief a responder receives, for both the text message and the spoken call.

The responder should not have to ask "who, where, how urgent, how do I talk to them?". The application already
knows most of it, so the brief states it up front, in a fixed order, and then adds what the caller agreed to share:

    Ayo alert (urgent). Caller +234... Area (from phone number): Lagos, Nigeria. Speak slowly; short sentences.
    The caller says: ...

Everything except the caller's own words comes from a closed vocabulary or the application's own state, so the
brief never carries free text a model wrote. The facts always come first (about 250 characters). The spoken call
reads only the first 600 characters, so when the message is long it is the caller's words that get shortened on
the phone, never the facts.
"""

from __future__ import annotations

import re

MAX_BRIEF = 1600  # the text message limit; the spoken call separately reads only the first 600 characters

RISKS = ("routine", "elevated", "urgent")
_PACE = {"slow": "speak slowly"}
_BREATHING = {"offer": "breathing exercises help", "avoid": "no breathing exercises"}
_HELPFUL = {
    "grounding": "grounding helps", "choices": "offer choices", "short_sentences": "short sentences",
    "validation": "validate feelings", "play_or_distraction": "gentle distraction helps",
    "naming_feelings": "naming feelings helps", "quiet_pauses": "allow quiet pauses",
}
_AVOID = {
    "grounding": "avoid grounding exercises", "choices": "avoid asking them to choose",
    "short_sentences": "avoid short clipped replies", "validation": "avoid reassurance",
    "play_or_distraction": "avoid distraction", "naming_feelings": "avoid naming feelings",
    "quiet_pauses": "avoid long silences",
}
E164 = re.compile(r"\+[1-9][0-9]{7,14}")


def preference_phrases(profile: dict | None) -> list[str]:
    """Plain-language communication tips from the closed-vocabulary profile; anything else is ignored."""
    profile = profile if isinstance(profile, dict) else {}
    phrases = []
    if profile.get("speaking_pace") in _PACE:
        phrases.append(_PACE[profile["speaking_pace"]])
    if profile.get("breathing") in _BREATHING:
        phrases.append(_BREATHING[profile["breathing"]])
    for key, table in (("helpful_approaches", _HELPFUL), ("avoid_approaches", _AVOID)):
        for value in profile.get(key) or []:
            if value in table and table[value] not in phrases:
                phrases.append(table[value])
    return phrases[:4]


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def build_brief(shared_message: str, *, risk: str | None = None, location: str | None = None,
                caller_number: str | None = None, profile: dict | None = None, limit: int = MAX_BRIEF) -> str:
    """Compose the brief. ``shared_message`` is what the caller agreed to share; the rest is application state."""
    risk = risk if risk in RISKS else None
    head = ["Ayo alert" + (f" ({risk})" if risk else "") + "."]
    if caller_number and E164.fullmatch(caller_number):
        head.append(f"Caller {caller_number}.")
    if location and location != "unknown location":
        head.append(f"Area (from phone number): {_one_line(location)}.")
    tips = preference_phrases(profile)
    if tips:
        head.append("How to talk to them: " + "; ".join(tips) + ".")
    facts = " ".join(head)
    message = _one_line(shared_message)
    if not message:
        return facts[:limit]
    prefix = f"{facts} The caller says: "
    room = max(0, limit - len(prefix))
    return prefix + message[:room] if room else facts[:limit]
