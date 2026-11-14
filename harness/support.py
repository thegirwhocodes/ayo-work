"""Closed-vocabulary caller memory rules.

Long-term memory only ever holds values from the fixed sets below. Nothing the
caller says (names, places, disclosures) can be stored, whichever component
proposes the update.
"""
import re

SPEAKING_PACE = ("slow", "normal")
BREATHING = ("offer", "avoid")
APPROACHES = (
    "grounding", "choices", "short_sentences", "validation",
    "play_or_distraction", "naming_feelings", "quiet_pauses",
)
MAX_APPROACHES = 8


def sanitize_profile(profile):
    """Drop every key or value outside the closed vocabulary."""
    profile = profile if isinstance(profile, dict) else {}
    result = {}
    if profile.get("speaking_pace") in SPEAKING_PACE:
        result["speaking_pace"] = profile["speaking_pace"]
    if profile.get("breathing") in BREATHING:
        result["breathing"] = profile["breathing"]
    for key in ("helpful_approaches", "avoid_approaches"):
        values = profile.get(key)
        if isinstance(values, list):
            kept = list(dict.fromkeys(v for v in values if v in APPROACHES))[-MAX_APPROACHES:]
            if kept:
                result[key] = kept
    return result


def merge_profile(current, update):
    """Fold an update into a profile; the newest signal wins on conflict."""
    result = sanitize_profile(current)
    update = sanitize_profile(update)
    for key in ("speaking_pace", "breathing"):
        if key in update:
            result[key] = update[key]
    for key, opposite in (("helpful_approaches", "avoid_approaches"), ("avoid_approaches", "helpful_approaches")):
        for value in update.get(key, []):
            kept = [v for v in result.get(key, []) if v != value] + [value]
            result[key] = kept[-MAX_APPROACHES:]
            if value in result.get(opposite, []):
                result[opposite] = [v for v in result[opposite] if v != value]
                if not result[opposite]:
                    del result[opposite]
    return result


def extract_preferences(text, previous):
    """Deterministic, explicit-request preferences (a backstop to the coordinator)."""
    result = dict(previous)
    value = text.lower()
    if re.search(r"\b(speak|talk) (more )?(slowly|slower)\b", value):
        result["speaking_pace"] = "slow"
    if re.search(r"\b(no|don't|dont|do not|stop) (the )?(breathing|breath|breathe)\b", value):
        result["breathing"] = "avoid"
    if re.search(r"\b(i (like|want|prefer)|please) (to )?(breathe|breathing)\b", value):
        result["breathing"] = "offer"
    return result
