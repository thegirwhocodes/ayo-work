"""Background coordinator: hands each finished turn to the case manager and publishes its guidance.

The voice agent never waits on this. Every finished turn is handed to `observe()`, which returns
immediately. A single worker task analyses the conversation at its own pace (coalescing turns that
arrive mid-analysis) by calling the analyst, normally the Deep Agents case manager in
`harness/case_manager.py`, and publishes the newest guidance. The voice agent pulls it with
`latest()`, which is instant and reports how many turns behind the guidance is.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from harness.case_models import MAX_ITEMS, MAX_TEXT, DEFAULT_TOPICS, SAFE_NEXT_MOVE, is_unsafe_advice
from harness.support import merge_profile, sanitize_profile

logger = logging.getLogger("sabi.harness.coordinator")

STALE_AFTER_TURNS = 2

# Until the first cycle finishes the voice agent still gets something to work with, so the opening replies are warm
# and short instead of generic or stalled. It is fixed text, grounded in the bundled empathy skill; never model output.
OPENING_GUIDANCE = {
    "risk": "routine",
    "stance": "Warm, slow and steady. Name the feeling first, then stay close.",
    "do": ["Reflect what the child said in your own simple words.", "Offer one gentle question or one small choice.",
           "Tell them you are staying with them."],
    "avoid": ["Do not ask why questions.", "Do not give long lists of instructions.",
              "Do not repeat the emergency number unless danger is clear."],
    "next_move": "Reflect their feeling in one sentence, then ask one gentle question.",
    "note": "Full guidance is still being prepared. Use this for now and reply right away. Never say you are thinking or waiting.",
}


def _clean(text) -> str:
    return " ".join(str(text).split())[:MAX_TEXT]


def _clean_list(items, drop_unsafe: bool = False) -> list[str]:
    cleaned = [_clean(i) for i in (items or []) if str(i).strip()]
    return [i for i in cleaned if not (drop_unsafe and is_unsafe_advice(i))][:MAX_ITEMS]


class Coordinator:
    def __init__(self, analyst, profile=None, on_profile=None, history_turns: int = 12,
                 cycle_timeout: float = 90.0, min_interval: float = 0.0, on_guidance=None):
        # analyst: async (transcript, profile) -> CaseAnalysis, normally harness.case_manager.CaseManager.
        self.analyst = analyst
        self._profile = sanitize_profile(profile)
        self.on_profile = on_profile
        self.on_guidance = on_guidance  # called with each newly published guidance, so it can be pushed to the call
        self.history_turns, self.cycle_timeout = history_turns, cycle_timeout
        # Spaces cycles out so a rate-limited API key (e.g. 15 requests/minute) is not exhausted.
        self.min_interval = min_interval
        self._turns: list[tuple[str, str]] = []
        self._turn = 0
        self._guidance: Optional[dict] = None
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._closed = False

    @property
    def profile(self) -> dict:
        return dict(self._profile)

    def reset_profile(self):
        """Forget everything learned about the caller (consent withdrawn)."""
        self._profile = {}

    def update_profile(self, update: dict) -> bool:
        merged = merge_profile(self._profile, update)
        if merged == self._profile:
            return False
        self._profile = merged
        if self.on_profile:
            try:
                self.on_profile(self.profile)
            except Exception as exc:
                logger.warning("Profile callback failed: %s", type(exc).__name__)
        return True

    def observe(self, caller_text: str, model_text: str = ""):
        """Record a finished turn and wake the worker. Never blocks."""
        if self._closed or not (caller_text or model_text):
            return
        if caller_text:
            self._turns.append(("Child", caller_text))
        if model_text:
            self._turns.append(("Assistant", model_text))
        self._turns = self._turns[-self.history_turns:]
        self._turn += 1
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        self._wake.set()

    def latest(self) -> dict:
        if self._guidance is None:
            return {"status": "pending"} if self._turn else {"status": "unavailable"}
        behind = self._turn - self._guidance["as_of_turn"]
        return {**self._guidance, "turns_behind": behind, "stale": behind > STALE_AFTER_TURNS}

    async def close(self):
        self._closed = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def idle(self):
        """Wait until every observed turn has been analysed (used by tests)."""
        while not self._closed and (self._wake.is_set() or self._busy):
            await asyncio.sleep(0)

    _busy = False

    async def _run(self):
        while True:
            await self._wake.wait()
            self._wake.clear()
            self._busy = True
            turn, transcript = self._turn, self._transcript()
            started = asyncio.get_running_loop().time()
            try:
                await asyncio.wait_for(self._cycle(turn, transcript), self.cycle_timeout)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Coordinator cycle failed: %s", type(exc).__name__)
            finally:
                # Turns that arrive during this wait coalesce into the next cycle.
                wait = self.min_interval - (asyncio.get_running_loop().time() - started)
                if wait > 0:
                    await asyncio.sleep(wait)
                self._busy = False

    def _transcript(self) -> str:
        return "\n".join(f"{who}: {text}" for who, text in self._turns)

    async def _cycle(self, turn: int, transcript: str):
        analysis = await self.analyst(transcript, dict(self._profile))
        self.update_profile(analysis.memory.model_dump(exclude_none=True))
        advice = analysis.advice
        next_move, stance = _clean(advice.next_move), _clean(advice.stance)
        self._guidance = {
            "status": "ready", "as_of_turn": turn, "risk": advice.risk, "topics": list(analysis.topics) or DEFAULT_TOPICS,
            "stance": SAFE_NEXT_MOVE if is_unsafe_advice(stance) else stance,
            "do": _clean_list(advice.do, drop_unsafe=True), "avoid": _clean_list(advice.avoid),
            "next_move": SAFE_NEXT_MOVE if is_unsafe_advice(next_move) else next_move,
            "sources": [dict(s) for s in analysis.sources], "checked": analysis.rubric,
            "note": "Background guidance only. Never read it aloud. Safety rules and emergency tools take priority.",
        }
        if self.on_guidance:
            try:
                self.on_guidance(dict(self._guidance))
            except Exception as exc:
                logger.warning("Guidance callback failed: %s", type(exc).__name__)
