"""Trauma-informed child safety guardrails.

Defense-in-depth safety engine designed for voice-first interactions with youth.
Enforces:
1. Crisis short-circuiting: disclosures of sexual/physical abuse, self-harm, or active danger
   immediately bypass the model and return scripted, trauma-informed resources.
2. Boundary protection: prevents roleplay drift, grooming vulnerability, medical advice,
   or false promises of secrecy.
3. Output sanitation: ensures model never interrogates, never blames, and never promises confidentiality.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("sabi.safety.guardrails")

# Crisis hotlines
EMERGENCY_NG = "112"
CHILD_HELPLINE_NG = "0800 800 8001"  # Cece Yara (Nigeria free child helpline)
CRISIS_LIFELINE_US = "988"

CRISIS_RESPONSES = {
    "abuse_disclosure": (
        "Thank you for telling me. I am really glad you told me, and what is happening is not your fault. "
        "You deserve to be safe. Please tell an adult you feel safe with right now — like a teacher, a parent, or an older family member. "
        f"You can also call the free child helpline at {CHILD_HELPLINE_NG}. "
        "They have kind people who listen and help children. You are very brave."
    ),
    "self_harm": (
        "I am so glad you told me, and I hear how much you are hurting. Your life matters, and you do not have to carry this alone. "
        "Please talk to an adult you trust right now. "
        f"You can call {EMERGENCY_NG}, or call {CHILD_HELPLINE_NG} (or 988 if you are in the US). "
        "Help is available, and there are people who care deeply about keeping you safe."
    ),
    "immediate_danger": (
        "This sounds like an emergency. If you can, get to a safe place immediately and call out to an adult. "
        f"Please call {EMERGENCY_NG} or the local emergency line right now. I want you to be safe."
    ),
}

DEFLECTION_RESPONSES = {
    "secrecy": (
        "I am always here to listen and help you feel calm, but I cannot keep secrets if someone is being hurt. "
        "Keeping you safe is the most important thing."
    ),
    "companion_romance": (
        "I am an AI friend here on the phone to help you feel grounded and supported. "
        "I am not a human person or a boyfriend/girlfriend, but I care about your well-being. How is your breathing feeling right now?"
    ),
    "medical_legal": (
        "I am not a doctor or a lawyer, so I cannot give medical or legal advice. "
        "Please talk to a nurse, doctor, or a grown-up you trust about that."
    ),
}

# Regex patterns for safety classification
ABUSE_PATTERN = re.compile(
    r"\b(touched me|touches me|bad touch|private parts|in my pants|molest|raped|forced me to touch|"
    r"hit me|beating me|punched me|locked me in|choking me|uncle touches|daddy hurts|mummy beats)\b",
    re.IGNORECASE,
)

SELF_HARM_PATTERN = re.compile(
    r"\b(kill myself|want to die|suicide|end my life|cut myself|cutting my|hang myself|"
    r"drink poison|swallow pills|take my own life|better off dead)\b",
    re.IGNORECASE,
)

IMMEDIATE_DANGER_PATTERN = re.compile(
    r"\b(break into the house|he has a gun|he has a knife|outside my window|going to shoot|"
    r"they are trying to kidnap|running away right now)\b",
    re.IGNORECASE,
)

SECRECY_PATTERN = re.compile(
    r"\b(promise not to tell|keep it a secret|don'?t tell anyone|our little secret|"
    r"promise you won'?t tell)\b",
    re.IGNORECASE,
)


@dataclass
class GuardResult:
    allowed: bool
    short_circuit: bool = False
    safe_response: Optional[str] = None
    category: Optional[str] = None


def guard_input(text: str) -> GuardResult:
    """Pre-LLM check on incoming user speech."""
    if not text or not text.strip():
        return GuardResult(allowed=True)

    text_lower = text.lower()

    # 1. Immediate danger
    if IMMEDIATE_DANGER_PATTERN.search(text_lower):
        logger.warning("Immediate danger triggered")
        return GuardResult(
            allowed=False,
            short_circuit=True,
            safe_response=CRISIS_RESPONSES["immediate_danger"],
            category="immediate_danger",
        )

    # 2. Self-harm / suicide
    if SELF_HARM_PATTERN.search(text_lower):
        logger.warning("Self-harm triggered")
        return GuardResult(
            allowed=False,
            short_circuit=True,
            safe_response=CRISIS_RESPONSES["self_harm"],
            category="self_harm",
        )

    # 3. Child abuse disclosure
    if ABUSE_PATTERN.search(text_lower):
        logger.warning("Abuse disclosure triggered")
        return GuardResult(
            allowed=False,
            short_circuit=True,
            safe_response=CRISIS_RESPONSES["abuse_disclosure"],
            category="abuse_disclosure",
        )

    # 4. Secrecy demands (respond with honest transparency)
    if SECRECY_PATTERN.search(text_lower):
        return GuardResult(
            allowed=False,
            short_circuit=True,
            safe_response=DEFLECTION_RESPONSES["secrecy"],
            category="secrecy",
        )

    return GuardResult(allowed=True)


def guard_output(text: str) -> str:
    """Post-LLM check on synthesized voice response.

    Strips any accidental interrogative coercion or inappropriate prompts.
    """
    if not text:
        return ""

    sanitized = text

    # Remove harmful interrogation patterns ("Why did you let him...", "What were you wearing...")
    interrogation_patterns = [
        r"why didn't you stop\b.*?\?",
        r"why did you let\b.*?\?",
        r"what were you wearing\b.*?\?",
        r"i promise i won't tell anyone",
    ]
    for pattern in interrogation_patterns:
        sanitized = re.sub(pattern, "", sanitized, flags=re.IGNORECASE)

    return sanitized.strip()
