"""Shared vocabulary and output schemas for the case manager and the background coordinator."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from harness.support import APPROACHES, BREATHING, SPEAKING_PACE

# Closed topic vocabulary. These fixed queries are the ONLY text ever sent to the embedding
# provider; caller words never leave through retrieval.
TOPIC_QUERIES = {
    "disclosure": "child disclosure of harm: responding calmly, believing the child, avoiding leading questions",
    "safety": "ensuring a child's immediate safety and first-line support",
    "distress": "child distress: comfort, grounding and coping choices",
    "trusted_adult": "helping a child identify and approach a safe trusted adult",
    "sexual_abuse": "responding to a child or adolescent who has experienced sexual abuse",
    "fear_of_home": "child afraid of a caregiver or of going home",
    "reporting": "explaining reporting, referral and services to a child",
    "mental_health": "psychosocial and mental health support for a child after maltreatment",
    "communication": "communicating sensitively with a child who may have experienced maltreatment",
}
Topic = Literal[tuple(TOPIC_QUERIES)]  # type: ignore[valid-type]
Approach = Literal[APPROACHES]  # type: ignore[valid-type]
DEFAULT_TOPICS = ["communication"]
MAX_TEXT = 300
MAX_ITEMS = 5


class MemoryUpdate(BaseModel):
    speaking_pace: Optional[Literal[SPEAKING_PACE]] = None  # type: ignore[valid-type]
    breathing: Optional[Literal[BREATHING]] = None  # type: ignore[valid-type]
    helpful_approaches: list[Approach] = Field(default_factory=list, max_length=4)
    avoid_approaches: list[Approach] = Field(default_factory=list, max_length=4)


class Triage(BaseModel):
    topics: list[Topic] = Field(default_factory=list, max_length=3)
    memory: MemoryUpdate = Field(default_factory=MemoryUpdate)


class ReferenceNotes(BaseModel):
    notes: list[str] = Field(default_factory=list, max_length=6,
                             description="Up to 6 short, faithful notes taken from the retrieved reference excerpts.")


class Advice(BaseModel):
    risk: Literal["routine", "elevated", "urgent"] = Field(
        default="routine",
        description="routine; elevated (distress or disclosure); urgent (immediate danger, self-harm, or an abuser nearby).")
    stance: str = Field(description="One sentence: the tone to take with the child right now.")
    do: list[str] = Field(default_factory=list, description="Up to 5 short things to do next.")
    avoid: list[str] = Field(default_factory=list, description="Up to 5 short things to avoid.")
    next_move: str = Field(description="One sentence: the best next thing to say or ask.")


class CaseAnalysis(BaseModel):
    """What one case-manager cycle produced. Topics and sources carry over from earlier cycles when nothing new was run."""
    advice: Advice
    topics: list[str] = Field(default_factory=list)
    memory: MemoryUpdate = Field(default_factory=MemoryUpdate)  # empty unless triage ran this cycle
    sources: list[dict] = Field(default_factory=list)
    rubric: str = "unchecked"  # satisfied | revised | unsatisfied | unchecked
    ran: list[str] = Field(default_factory=list)  # specialists run this cycle


# Deterministic backstop under the rubric. The case manager grades its advice, but a model can still slip,
# so lines that break a hard rule never reach the voice agent.
_UNSAFE = re.compile(
    r"\bwhy\b[^.?!]*\?|\bi promise\b|\b(keep|keeping|stay|stays|remain|remains)\b[^.?!]*\b(secret|confidential)\b|"
    r"\b(help|someone|they|police|responder)\b[^.?!]*\b(is|are) (on the way|coming|arriving)\b|"
    r"\b(has|have) been (dispatched|sent)\b|\bwe (have )?(sent|dispatched)\b", re.IGNORECASE)
_CONTACT = re.compile(r"\b(contact|call|text|message|alert|notify)\b[^.?!]*\bresponder\b", re.IGNORECASE)
_CONSENT = re.compile(r"\b(consent|agrees?|agreed|permission|clear(ly)? yes|says? yes|after asking)\b", re.IGNORECASE)
SAFE_NEXT_MOVE = "Stay with them and ask what would help right now."


def is_unsafe_advice(text: str) -> bool:
    text = str(text)
    return bool(_UNSAFE.search(text)) or (bool(_CONTACT.search(text)) and not _CONSENT.search(text))
