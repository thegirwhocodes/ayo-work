"""Twilio Media Streams WebSocket handler.

Handles full-duplex streaming audio between a Twilio phone call and Gemini Live.
- Twilio sends: 8kHz mu-law audio frames (base64 encoded)
- Server decodes: mu-law -> 8kHz PCM16 -> Gemini Live
- Gemini Live yields: 24kHz PCM16 audio
- Server downsamples: 24kHz -> 8kHz PCM16 -> mu-law -> base64 -> Twilio
- Interruption handling: sends {"event": "clear"} to flush Twilio playback buffer.

The Gemini session is opened on the `start` event rather than up front, because
`start.customParameters` carries the caller's location and that has to be baked
into the system prompt before the model speaks its first word.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import uuid
from typing import Optional, Callable, Awaitable

from fastapi import WebSocket, WebSocketDisconnect

from app.voice.audio import (
    mulaw_to_pcm16,
    pcm16_to_mulaw,
    Downsample24kTo8k,
    FrameChunker,
)
from app.voice.caller_location import CallerLocation, build_location_briefing
from app.voice.gemini_live import GeminiLiveSession, LiveConfig
from app.voice.openai_realtime import OpenAIRealtimeSession
from harness.agent import create_harness
from harness import emergency_plan
from harness import consent
from harness.incident_brief import build_brief
from harness.emergency_jobs import cancel_job, cancel_session_jobs, create_job, get_job_result, job_progress, token_hash
from harness.call_audio import SpeechCollector
from harness.caller_identity import verify_caller
from harness.datastore.voice import PostgresVoiceStore
from harness.datastore.memory import SupportMemoryStore
from harness.corpus.model import cohere_embeddings
from harness.corpus.store import CorpusStore
from harness.case_manager import gemini_case_manager
from harness.coordinator import Coordinator
from harness.support import extract_preferences
from harness.tool_log import bind_call, log_tool_event, short_ref, timed
from harness.tools.search_reference import ReferenceSearch
from harness.tools.declarations import RELAY_DECLARATION, SUPPORT_GUIDANCE_DECLARATION
from harness.tools import EmergencyCallContext, EmergencyTools, SpeakerVerifier, VoiceVerificationTool
from settings import get_settings

logger = logging.getLogger("ayo.voice.twilio")

VOICE_RECOGNITION_NOTE = (
    "\nThis line quietly recognizes returning callers by their voice, in the background, from what they say as you talk. "
    "You run no check: never ask the caller to say, repeat or read anything for it, and never tell them how it turned out. "
    "Once, at a calm moment after the child has spoken a few times and is not in distress, say in one short plain sentence that "
    "you can recognize their voice next time so they will not have to start over. Do not bring it up again unless they ask.")
CONSENT_QUESTION = "may i contact the emergency responder with what you shared"
RUNTIME_CONTEXT_PLACEHOLDER = "{RUNTIME_CONTEXT}"
GREETING_MESSAGE_BYTES = 1600          # 200 ms of 8 kHz mu-law per Twilio media message
START_TIMEOUT_S = 15.0                 # how long to wait for Twilio's `start` event before giving up on the call

# Voice identity states. The voice check runs quietly in the background on whatever the caller says during the
# conversation: nothing waits for it and the caller is never asked to say anything for it. A failed or impossible
# check never ends the call (a child who whispers, cries, is silent, shares a phone, or calls while the database
# is down must still reach Ayo). Until the check finishes the state is "unsure".
IDENTITY_KNOWN = "known"      # voice matches the enrolled voice for this caller
IDENTITY_NEW = "new"          # first call: this voice was just enrolled
IDENTITY_UNKNOWN = "unknown"  # a different voice from the enrolled one
IDENTITY_UNSURE = "unsure"    # could not tell (too little speech, check failed, database down)
_IDENTITY_BY_STATUS = {"matched": IDENTITY_KNOWN, "enrolled": IDENTITY_NEW, "not_matched": IDENTITY_UNKNOWN}

# How the application tells the voice agent about an escalation step. The emergency agent chooses only the
# note's name (harness/emergency_plan.py VOICE_NOTES); these words are fixed here, so nothing the model writes
# reaches the child. None of them lets the voice agent promise that help is coming.
VOICE_NOTE_TEXT = {
    "check_in": ("Several minutes have passed and the responder has not acknowledged. At a gentle moment, ask the caller "
                 "whether they are still safe and whether anyone has come to them. Do not promise that help is coming."),
    "call_emergency_number": ("The responder has not acknowledged yet. If it is safe, encourage the caller to call the local "
                              "emergency number now. Do not promise that help is coming."),
    "responder_unreachable": ("No way of reaching the responder was accepted. Tell the caller honestly that you could not reach "
                              "them, and encourage them to call the local emergency number if it is safe."),
}
RISK_ORDER = {"routine": 0, "elevated": 1, "urgent": 2}
# Said to the voice agent in the application's own words when the case manager first rates the call urgent.
# It must be safe to hear mid-call: it asks for a safety check and the consent question, and promises nothing.
URGENT_NUDGE = ("(Application instruction, not from the caller.) This call is now rated urgent. At your next reply, "
                "first check whether the caller is safe right now. If they are in danger or ask for help, ask exactly: "
                "'May I contact the emergency responder with what you shared?' and wait for a clear yes before using "
                "relay_to_harness for the responder. Do not promise that help is coming.")
URGENT_REMINDER = ("(Application instruction, not from the caller.) The call is still rated urgent and no help has been "
                   "requested yet. At a gentle moment, ask whether the caller wants you to contact the emergency responder, "
                   "using the exact consent question. Do not promise that help is coming.")
URGENT_REMINDER_AFTER_CYCLES = 3  # further urgent guidance cycles without a relay before the one reminder
JOB_POLL_SECONDS = 1.0
FIRST_RESULT_SECONDS = 90  # how long the emergency agent has to run its first action before the job counts as lost
# Saving communication preferences is low-risk, so ordinary affirmatives count. The emergency
# responder consent above stays on its own, stricter list.
MEMORY_YES = {"yes", "yes please", "yes i agree", "i agree", "yeah", "yep", "yes it is okay", "yes that is okay",
              "okay", "ok", "okay please", "sure", "sure please", "please do", "that is okay", "it is okay"}


class TwilioMediaBridge:
    def __init__(
        self,
        websocket: WebSocket,
        system_prompt: str,
        voice_name: str = "Puck",
        on_transcript: Optional[Callable[[str, str], Awaitable[None]]] = None,
        on_safety_alert: Optional[Callable[[dict], Awaitable[None]]] = None,
        greeting_audio: bytes = b"",
    ):
        self.ws = websocket
        self.system_prompt = system_prompt
        self.voice_name = voice_name
        self.on_transcript = on_transcript
        self.on_safety_alert = on_safety_alert
        # Ayo's pre-recorded greeting (raw 8 kHz mu-law), played as soon as the stream starts.
        self.greeting_audio = greeting_audio
        self._greeting_task: Optional[asyncio.Task] = None
        # Caller speech is also fed to this collector as the conversation goes on; once it holds enough voiced audio
        # the check runs in a background task. The voice engine never waits for it.
        self._voice_collector: Optional[SpeechCollector] = None
        self._verify_task: Optional[asyncio.Task] = None

        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self.location: CallerLocation = CallerLocation()
        self.emergency_context = EmergencyCallContext(caller_number="")
        self.consent_question_pending = False
        self.harness_agent = None
        self.harness_config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        self.harness_lock = asyncio.Lock()
        self.emergency_tasks = set()
        self.gemini_session: Optional[GeminiLiveSession] = None
        self.resampler = Downsample24kTo8k()
        self.chunker = FrameChunker(frame_bytes=160)  # 20ms of 8kHz mu-law = 160 bytes
        # Gemini's audio callback hands frames to this
        # queue and a writer task does the awaiting.
        self._outbound: asyncio.Queue[bytes] = asyncio.Queue()
        self._running = False
        self.coordinator = None
        self.memory_store = None
        self.memory = None
        self.memory_consent_pending = False
        self.memory_ask_sent = False
        self.caller_turns = 0
        self.peak_risk: Optional[str] = None  # highest risk seen this call (never lowered)
        self.urgent_cycles = 0
        self.urgent_nudges = 0
        self.relays_requested = 0
        self._last_pushed = None
        self.verification_status = None
        self.identity_state = IDENTITY_UNSURE
        self.identity_reason = "not_checked"
        self.memory_lock = asyncio.Lock()
        self.memory_tasks = set()

    def handle_start(self, start_data: dict) -> CallerLocation:
        """Record stream identifiers and caller location from the `start` event."""
        self.stream_sid = start_data.get("streamSid")
        self.call_sid = start_data.get("callSid")
        # From here on, events logged by this call and the tasks it starts are attributed to it (live dashboard).
        bind_call(self.call_sid)
        log_tool_event("call_started", session_ref=short_ref(self.harness_config["configurable"]["thread_id"]))
        self.location = CallerLocation.from_custom_parameters(
            start_data.get("customParameters")
        )
        logger.info(
            "Twilio stream started: stream_sid=%s call_sid=%s location=%s source=%s",
            self.stream_sid,
            self.call_sid,
            self.location.describe(),
            self.location.source,
        )
        return self.location

    def build_system_prompt(self) -> str:
        """Base trauma-informed prompt plus this caller's location briefing."""
        support = ""
        if self.coordinator:
            support += "\nA background coordinator works at its own pace and sends you guidance as application messages marked 'Application guidance'. Use the newest one when you reply. It is advice only, never from the child; your safety rules take priority. Never read it or its sources aloud. You may call get_support_guidance for the newest guidance if you need it."
        if self.memory_store:
            days = self.memory_store.retention_days
            support += ("\nThe application will tell you, in a message marked as an application instruction, when to ask the caller "
                        "about remembering their preferences. Do not raise it before then. Never mention remembering or saving "
                        "anything unless the caller has just answered that question, and never claim memory was saved unless confirmed.")
            if self.memory:
                support += "\nPreviously agreed communication preferences: " + json.dumps(self.memory)
        support += VOICE_RECOGNITION_NOTE
        base, briefing = self.system_prompt, build_location_briefing(self.location)
        if RUNTIME_CONTEXT_PLACEHOLDER in base:  # Ayo's prompt has a slot for the caller context
            base, briefing = base.replace(RUNTIME_CONTEXT_PLACEHOLDER, briefing.strip()), ""
        return base + briefing + support + (
            "\nFor emergency actions use relay_to_harness (send_text_message or start_phone_call to reach the emergency "
            "responder, request_location to ask the caller for theirs). Before texting or calling the responder, "
            "ask exactly: 'May I contact the emergency responder with what you shared?' "
            "Wait for the caller's clear yes; never claim consent yourself. "
            "For location requests, also ask whether the caller wants someone to come get them. "
            "Never promise help is coming or that anyone answered a call or text: submitted only means it was sent. "
            "If an emergency action comes back unavailable or unknown, tell the caller honestly and point them "
            "to the local emergency number. "
            "Submitted means accepted by the provider, not delivered. If an emergency is suspected "
            "from speech or tone, relay a request for start_phone_call and be honest if unavailable. "
            "A pending result means the action is still running. Continue speaking and do not claim "
            "it succeeded. A later system message will report the actual outcome."
        )

    async def _connect_gemini(self) -> bool:
        cfg = LiveConfig(
            system_prompt=self.build_system_prompt(),
            voice=self.voice_name,
            input_rate=8000,
            tools=[RELAY_DECLARATION] + ([SUPPORT_GUIDANCE_DECLARATION] if self.coordinator else []),
        )
        # VOICE_ENGINE=openai: OpenAI Realtime hears + thinks, ElevenLabs speaks (Ayo).
        # Anything else keeps the original Gemini Live engine. Both share one interface.
        engine = OpenAIRealtimeSession if get_settings().voice_engine == "openai" else GeminiLiveSession
        self.gemini_session = engine(
            cfg,
            on_audio=self._on_gemini_audio,
            on_interrupted=self._on_interrupted,
            on_turn_complete=self._on_turn_complete,
            on_tool_call=self.relay_to_harness,
        )
        try:
            await self.gemini_session.connect()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to the voice engine: {e}")
            self.gemini_session = None
            return False

    async def run(self):
        self._running = True
        logger.info("Starting Twilio Media Bridge...")

        try:
            await self._await_start()
            await self._setup_support()
            self._ensure_harness_agent()
            if not await self._connect_gemini():
                await self.ws.close()
                return
            await self._open_conversation()
        except Exception as e:
            logger.warning("Call setup failed: %s", type(e).__name__)
            await self.ws.close()
            return

        tasks: list[asyncio.Task] = [
            asyncio.create_task(self.gemini_session.receive_forever()),
            asyncio.create_task(self._drain_outbound()),
        ]
        try:
            while self._running:
                raw_text = await self.ws.receive_text()
                data = json.loads(raw_text)
                event = data.get("event")

                if event == "connected":
                    logger.info("Twilio connected event received")

                elif event == "media":
                    media_payload = data.get("media", {}).get("payload")
                    if media_payload and self.gemini_session:
                        # 1. Base64 decode Twilio mu-law
                        mulaw_bytes = base64.b64decode(media_payload)
                        # 2. Decode mu-law to 8kHz PCM16
                        pcm16_bytes = mulaw_to_pcm16(mulaw_bytes)
                        # 3. Quietly collect speech for the background voice check (never blocks the call)
                        self._collect_for_verification(pcm16_bytes)
                        # 4. Stream to the voice engine
                        await self.gemini_session.send_audio(pcm16_bytes)

                elif event == "stop":
                    logger.info("Twilio call stopped event received")
                    break

                elif event == "mark":
                    pass

        except WebSocketDisconnect:
            logger.info("Twilio WebSocket disconnected")
        except Exception as e:
            logger.error(f"Twilio bridge loop error: {e}", exc_info=True)
        finally:
            self._running = False
            log_tool_event("call_ended")
            if self._verify_task:
                self._verify_task.cancel()
                await asyncio.gather(self._verify_task, return_exceptions=True)
            for task in self.emergency_tasks:
                task.cancel()
            if self.emergency_tasks:
                await asyncio.gather(*self.emergency_tasks, return_exceptions=True)
            try:
                await cancel_session_jobs(self.harness_config["configurable"]["thread_id"])
            except Exception as exc:
                logger.warning("Emergency job cleanup failed: %s", type(exc).__name__)
            if self.coordinator:
                await self.coordinator.close()
            if self.memory_tasks:
                await asyncio.gather(*self.memory_tasks, return_exceptions=True)
            if self.gemini_session:
                await self.gemini_session.close()
            for task in tasks:
                task.cancel()
            logger.info("Twilio Media Bridge closed.")

    async def _await_start(self):
        """Read Twilio's events up to `start`, check the signed caller token and begin the greeting.

        Nothing else happens here: the voice engine connects straight afterwards and the voice check is
        collected later from the conversation itself. Only a bad caller token or a hang-up before the
        stream starts is fatal.
        """
        deadline = asyncio.get_running_loop().time() + START_TIMEOUT_S
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError("no caller identity: the stream never started")
            try:
                data = json.loads(await asyncio.wait_for(self.ws.receive_text(), timeout=remaining))
            except asyncio.TimeoutError:
                raise RuntimeError("no caller identity: the stream never started")
            event = data.get("event")
            if event == "start":
                start = data.get("start", {})
                self.handle_start(start)
                token = start.get("customParameters", {}).get("caller_token", "")
                caller_id = verify_caller(token, os.getenv("TWILIO_AUTH_TOKEN", ""))
                self.emergency_context.caller_number = caller_id
                self._voice_collector = SpeechCollector()
                self._start_greeting()
                return
            if event == "stop":
                raise RuntimeError("call ended before the stream started")

    def _collect_for_verification(self, pcm: bytes) -> None:
        """Feed caller speech to the voice check; when there is enough, run it in the background."""
        collector = self._voice_collector
        if collector is None:
            return
        try:
            enough = collector.add(pcm)
        except Exception as exc:  # never let the check disturb the call
            logger.warning("Voice collection failed: %s", type(exc).__name__)
            self._voice_collector = None
            return
        if enough:
            self._voice_collector = None
            self._verify_task = asyncio.create_task(self._verify_in_background(collector))

    async def _verify_in_background(self, collector) -> None:
        """Enroll or compare the voice while the conversation carries on. Never raises, never ends the call."""
        try:
            await self._check_voice(collector, self.emergency_context.caller_number)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # database down, model failure, ...: carry on as unsure
            self._set_identity(IDENTITY_UNSURE, f"check_failed:{type(exc).__name__}")
            return
        if self.verification_status == "matched":
            await self._load_memory()  # a returning, voice-matched caller gets their consented preferences back

    def _set_identity(self, state: str, reason: str) -> None:
        self.identity_state, self.identity_reason = state, reason
        logger.info("Voice identity: %s (%s)", state, reason)

    async def _check_voice(self, collector, caller_id: str) -> None:
        store = PostgresVoiceStore(os.getenv("DATABASE_URL", ""))
        await asyncio.to_thread(store.initialize)
        verifier = SpeakerVerifier(store, float(os.getenv("VOICE_VERIFICATION_THRESHOLD", "0.75")))
        voice_tool = VoiceVerificationTool(verifier, caller_id, collector.audio_16khz())
        # Deterministic: no LLM sits in the verification path, so a model outage or quota error can
        # never block or fail voice recognition.
        with timed() as timer:
            status = await asyncio.wait_for(voice_tool.verify_speaker(), timeout=60)
        voice_tool.audio_data = b""
        similarity = getattr(voice_tool.result, "similarity", None)
        log_tool_event("tool_call", tool="verify_speaker", call_sid=self.call_sid, status=status,
                       similarity=round(similarity, 3) if similarity is not None else None, duration_ms=timer.ms)
        self.verification_status = status if status in _IDENTITY_BY_STATUS else None
        self._set_identity(_IDENTITY_BY_STATUS.get(status, IDENTITY_UNSURE), status)

    def _start_greeting(self) -> None:
        if self.greeting_audio and self.stream_sid and self._greeting_task is None:
            self._greeting_task = asyncio.create_task(self._play_greeting())

    async def _play_greeting(self) -> None:
        """Send Ayo's recorded greeting to Twilio; Twilio buffers and plays it in order."""
        try:
            for i in range(0, len(self.greeting_audio), GREETING_MESSAGE_BYTES):
                frame = self.greeting_audio[i:i + GREETING_MESSAGE_BYTES]
                await self.ws.send_text(json.dumps({
                    "event": "media", "streamSid": self.stream_sid,
                    "media": {"payload": base64.b64encode(frame).decode("ascii")}}))
        except Exception as exc:  # a greeting failure must never end the call
            logger.warning("Greeting playback failed: %s", type(exc).__name__)

    async def _open_conversation(self):
        """The agent speaks first: Ayo's recorded greeting is already playing; without one, prompt the opening
        line so the call never starts in silence."""
        if not self.greeting_audio:
            await self._greet()

    async def _greet(self):
        """Gemini Live only speaks after hearing the caller, so prompt the opening line."""
        await self.gemini_session.send_text("The call has just connected. Greet the caller now.")

    async def _load_memory(self):
        """A returning, voice-matched caller gets their consented preferences back."""
        if not self.memory_store:
            return
        try:
            memory = await asyncio.to_thread(self.memory_store.read, self.emergency_context.caller_number)
        except Exception as exc:
            logger.warning("Memory read failed: %s", type(exc).__name__)
            return
        if memory is not None:
            self.memory = memory
            if self.coordinator:
                self.coordinator.update_profile(memory)

    async def _setup_support(self):
        database_url = os.getenv("DATABASE_URL", "")
        if os.getenv("SUPPORT_MEMORY_ENABLED", "false").lower() == "true":
            try:
                self.memory_store = SupportMemoryStore(database_url, os.getenv("SUPPORT_MEMORY_ENCRYPTION_KEY", ""),
                    os.getenv("SUPPORT_MEMORY_IDENTITY_KEY", ""), int(os.getenv("SUPPORT_MEMORY_RETENTION_DAYS", "30")))
                await asyncio.to_thread(self.memory_store.purge_expired)
            except Exception as exc:
                self.memory_store = None
                logger.warning("Support memory disabled: %s", type(exc).__name__)
        if os.getenv("SUPPORT_GUIDANCE_ENABLED", "false").lower() == "true":
            self._setup_coordinator(database_url)

    def _setup_coordinator(self, database_url):
        """Background case manager (a Deep Agents team): skills always, retrieval when the corpus is configured."""
        try:
            search = None
            if database_url and os.getenv("COHERE_API_KEY"):
                search = ReferenceSearch(cohere_embeddings(), CorpusStore(database_url))
            models = [os.getenv("COORDINATOR_MODEL") or os.getenv("HARNESS_MODEL", "gemini-3.5-flash")]
            models += [m.strip() for m in os.getenv("COORDINATOR_FALLBACK_MODELS", "gemini-3.5-flash-lite").split(",") if m.strip()]
            case_manager = gemini_case_manager(models, os.getenv("GEMINI_API_KEY", ""), search)
            self.coordinator = Coordinator(case_manager, profile=self.memory, on_profile=self._on_profile, on_guidance=self._on_guidance,
                                           min_interval=float(os.getenv("COORDINATOR_MIN_INTERVAL_SECONDS", "8")))
        except Exception as exc:
            logger.warning("Support guidance disabled: %s", type(exc).__name__)

    def _on_profile(self, profile):
        """Persist what the coordinator learned, only once the caller has agreed to memory."""
        if self.memory_store and self.memory is not None and profile != self.memory:
            task = asyncio.create_task(self._persist_profile(profile))
            self.memory_tasks.add(task)
            task.add_done_callback(self.memory_tasks.discard)

    async def _persist_profile(self, profile):
        async with self.memory_lock:
            try:
                if self.memory is not None and profile != self.memory:
                    if await asyncio.to_thread(self.memory_store.save, self.emergency_context.caller_number, profile):
                        self.memory = profile
            except Exception as exc:
                logger.warning("Memory operation failed: %s", type(exc).__name__)

    async def _process_memory(self, answer, pending):
        async with self.memory_lock:
            try:
                caller_id = self.emergency_context.caller_number
                if self.verification_status == "matched" and (answer in {"delete my memory", "forget my information", "forget what you remember", "delete what you remember", "stop remembering"} or (pending and consent.is_no(answer))):
                    await asyncio.to_thread(self.memory_store.delete, caller_id)
                    self.memory = None
                    if self.coordinator:
                        self.coordinator.reset_profile()
                elif pending and (answer in MEMORY_YES or consent.is_yes(answer)):
                    memory = self.coordinator.profile if self.coordinator else (self.memory or {})
                    saved = await asyncio.to_thread(self.memory_store.save, caller_id, memory, consent=True, matched=self.verification_status == "matched")
                    if saved:
                        self.memory = memory
                elif self.memory is not None and not self.coordinator:
                    updated = extract_preferences(answer, self.memory)
                    if updated != self.memory:
                        if await asyncio.to_thread(self.memory_store.save, caller_id, updated):
                            self.memory = updated
            except Exception as exc:
                logger.warning("Memory operation failed: %s", type(exc).__name__)

    async def relay_to_harness(self, name: str, args: dict) -> dict:
        """Every Gemini Live tool call lands here; log it (no content) and dispatch."""
        args = args if isinstance(args, dict) else {}
        message = args.get("message")
        fields = {"tool": name, "call_sid": self.call_sid,
                  "action": args.get("action") if isinstance(args.get("action"), str) else None,
                  "message_chars": len(message) if isinstance(message, str) else None,
                  "responder_consent": self.emergency_context.responder_consent}
        with timed() as timer:
            try:
                result = await self._dispatch_tool(name, args)
            except Exception as exc:
                log_tool_event("tool_call", status="error", error=type(exc).__name__, duration_ms=timer.ms, **fields)
                raise
        log_tool_event("tool_call", status=result.get("status"), reason=result.get("reason"), risk=result.get("risk"),
                       turns_behind=result.get("turns_behind"), duration_ms=timer.ms, **fields)
        return result

    async def _dispatch_tool(self, name: str, args: dict) -> dict:
        if name == "get_support_guidance" and self.coordinator:
            return self.coordinator.latest()
        if name != "relay_to_harness":
            return {"status": "unavailable", "reason": "unknown_tool"}
        action = args.get("action")
        if action not in {"send_text_message", "request_location", "start_phone_call"}:
            return {"status": "unavailable", "reason": "unknown_action"}
        message = args.get("message", "")
        if not isinstance(message, str) or len(message) > 1600:
            return {"status": "unavailable", "reason": "invalid_message"}
        if action in {"send_text_message", "start_phone_call"} and not self.emergency_context.responder_consent:
            return {"status": "consent_required"}
        if self.harness_agent is None:
            self._ensure_harness_agent()
        if self.harness_agent is None:
            return {"status": "unavailable", "reason": "harness_not_ready"}
        if action in {"send_text_message", "start_phone_call"}:
            message = self._responder_brief(message)  # facts the app already knows, then what the caller agreed to share
        self.emergency_context.message = message
        tools = EmergencyTools(self.emergency_context, action, message)
        try:
            token = await create_job(tools, self.harness_config["configurable"]["thread_id"])
        except Exception as exc:
            logger.warning("Emergency job creation failed: %s", type(exc).__name__)
            return {"status": "unavailable", "reason": "job_store_unavailable"}
        self.relays_requested += 1
        log_tool_event("emergency_job_created", job_ref=token_hash(token)[:12], action=action, call_sid=self.call_sid)
        task = asyncio.create_task(self.run_emergency_job(token))
        self.emergency_tasks.add(task)
        task.add_done_callback(self.emergency_tasks.discard)
        return {"status": "pending"}

    def _on_guidance(self, guidance: dict) -> None:
        """Called by the coordinator for each new guidance. Raises the call's risk floor, pushes the newest
        guidance to the voice agent, and, when the call is urgent, has the voice agent check safety and offer
        help. Everything is sent as context only (no forced reply), so it never talks over the caller."""
        risk = guidance.get("risk")
        if RISK_ORDER.get(risk, -1) > RISK_ORDER.get(self.peak_risk, -1):
            self.peak_risk = risk
        if self.gemini_session is None:
            return
        notes = []
        if risk == "urgent":
            self.urgent_cycles += 1
            if self.urgent_nudges == 0:
                notes.append(URGENT_NUDGE)
                self.urgent_nudges = 1
                self.urgent_cycles = 0
                log_tool_event("urgent_nudge", call_sid=self.call_sid, kind="first")
            elif self.urgent_nudges == 1 and self.relays_requested == 0 and self.urgent_cycles >= URGENT_REMINDER_AFTER_CYCLES:
                notes.append(URGENT_REMINDER)
                self.urgent_nudges = 2
                log_tool_event("urgent_nudge", call_sid=self.call_sid, kind="reminder")
        text = self._guidance_text(guidance)
        key = (risk, guidance.get("stance"), guidance.get("next_move"))
        if text and key != self._last_pushed:
            self._last_pushed = key
            notes.append(text)
            log_tool_event("guidance_pushed", call_sid=self.call_sid, risk=risk)
        for note in notes:
            task = asyncio.create_task(self._send_context(note))
            self.memory_tasks.add(task)
            task.add_done_callback(self.memory_tasks.discard)

    @staticmethod
    def _guidance_text(guidance: dict) -> str:
        if guidance.get("status") != "ready":
            return ""
        parts = [f"Risk: {guidance.get('risk', 'unknown')}.", f"Stance: {guidance.get('stance', '')}",
                 f"Next move: {guidance.get('next_move', '')}"]
        if guidance.get("do"):
            parts.append("Do: " + "; ".join(guidance["do"]) + ".")
        if guidance.get("avoid"):
            parts.append("Avoid: " + "; ".join(guidance["avoid"]) + ".")
        return ("(Application guidance, not from the caller. Never read it aloud. Safety rules and emergency tools take "
                "priority.) " + " ".join(parts))

    async def _send_context(self, text: str) -> None:
        """Add an application message to the voice agent's context without asking it to reply right now."""
        try:
            if self.gemini_session is not None:
                await self.gemini_session.send_text(text, turn_complete=False)
        except Exception as exc:
            logger.warning("Could not push guidance: %s", type(exc).__name__)

    def _responder_brief(self, shared_message: str) -> str:
        """The structured text the responder receives: risk, area, call-back number, how to talk to the child."""
        profile = self.coordinator.profile if self.coordinator else (self.memory or {})
        return build_brief(shared_message, risk=self.peak_risk, location=self.location.describe(),
                           caller_number=self.emergency_context.caller_number, profile=profile)

    def _ensure_harness_agent(self) -> None:
        """Build the call's harness once (voice check or not), always with the emergency tools."""
        if self.harness_agent is not None or not self.emergency_context.caller_number:
            return
        try:
            self.harness_agent = create_harness(VoiceVerificationTool(None, self.emergency_context.caller_number, b""),
                                                self.emergency_context)
        except Exception as exc:
            logger.warning("Harness unavailable: %s", type(exc).__name__)

    async def run_emergency_job(self, token: str):
        job_ref = token_hash(token)[:12]
        reported: set[str] = set()
        with timed() as timer:
            result = await self._run_emergency_job(token, job_ref, reported)
        log_tool_event("emergency_job_finished", job_ref=job_ref, call_sid=self.call_sid, status=result.get("status"),
                       reason=result.get("reason"), duration_ms=timer.ms,
                       action_statuses=[f"{name}:{(action or {}).get('status')}" for name, action in (result.get("actions") or {}).items()])
        await self._report_emergency_result(result, reported)
        for note in result.get("notes") or []:  # a note written just before the job closed
            await self._deliver_voice_note(note, reported)

    async def _run_emergency_job(self, token: str, job_ref: str, reported: set[str] | None = None) -> dict:
        """Start the emergency agent, then follow the job until it closes.

        The first action must show up within ``FIRST_RESULT_SECONDS``. After that the agent may keep escalating
        for the whole plan window, and each action outcome and voice note reaches the voice agent as it appears.
        """
        reported = set() if reported is None else reported
        try:
            async with self.harness_lock:
                with timed() as harness_timer:
                    outcome = await asyncio.wait_for(self.harness_agent.ainvoke({"messages": [{"role": "user", "content":
                        "Start the emergency async subagent now with the exact task description "
                        f"emergency-job:{token}. Do not change the description or wait for it."}]},
                        config=self.harness_config), timeout=20)
            # Which tools did the harness model actually call? (start_async_task means the subagent was spawned.)
            tool_names = [call.get("name") for message in (outcome or {}).get("messages", [])
                          for call in (getattr(message, "tool_calls", None) or [])]
            log_tool_event("harness_tool_calls", job_ref=job_ref, call_sid=self.call_sid, tools=tool_names,
                           subagent_spawned="start_async_task" in tool_names, duration_ms=harness_timer.ms)
            loop = asyncio.get_running_loop()
            started, heard_from_agent, warned = loop.time(), False, False
            while True:
                result = await get_job_result(token)
                if result is not None:
                    break
                try:
                    progress = await job_progress(token)
                except Exception as exc:  # a failed look never ends the wait; the final result still arrives
                    progress = None
                    if not warned:
                        warned = True
                        logger.warning("Emergency job progress unavailable: %s", type(exc).__name__)
                if progress:
                    heard_from_agent = heard_from_agent or bool(progress["results"])
                    await self._report_progress(progress, reported)
                limit = emergency_plan.PLAN_WINDOW_SECONDS + FIRST_RESULT_SECONDS if heard_from_agent else FIRST_RESULT_SECONDS
                if loop.time() - started > limit:
                    raise TimeoutError("emergency job did not finish")
                await asyncio.sleep(JOB_POLL_SECONDS)
        except Exception as exc:
            logger.warning("Emergency subagent failed: %s", type(exc).__name__)
            log_tool_event("emergency_job_error", job_ref=job_ref, call_sid=self.call_sid, error=type(exc).__name__)
            result = {"status": "unknown", "reason": "subagent_failed_or_timed_out"}
        finally:
            try:
                await cancel_job(token)
            except Exception as exc:
                logger.warning("Emergency job cancellation failed: %s", type(exc).__name__)
        return result

    async def _report_progress(self, progress: dict, reported: set[str]):
        """Tell the voice agent about each action outcome and escalation note the moment it appears."""
        fresh = {name: (action or {}).get("status") for name, action in progress["results"].items() if name not in reported}
        for name, status in fresh.items():
            log_tool_event("emergency_action", action=name, status=status)
        if fresh and self.gemini_session is not None:
            reported.update(fresh)
            if "submitted" in fresh.values():
                guidance = ("Only the actions listed as submitted were accepted by a provider. Do not say anything was delivered, "
                            "answered, or that help is coming. More steps may follow; you will be told.")
            else:
                guidance = ("That was not accepted. The application may try another way to reach the responder and will tell you "
                            "the result. Do not say anything was sent.")
            try:
                await self.gemini_session.send_text(
                    "Emergency action update (trusted application status): " + json.dumps({"actions": fresh}) + " " + guidance)
            except Exception as exc:
                logger.warning("Could not deliver emergency update: %s", type(exc).__name__)
        elif fresh:
            reported.update(fresh)
        for note in progress["notes"]:
            await self._deliver_voice_note(note, reported)

    async def _deliver_voice_note(self, note: str, reported: set[str]):
        """Pass an escalation note on to the voice agent in the application's own words, once."""
        marker, text = f"note:{note}", VOICE_NOTE_TEXT.get(note)
        if marker in reported or text is None:
            return
        reported.add(marker)
        if self.gemini_session is None:
            return
        log_tool_event("emergency_voice_note", call_sid=self.call_sid, note=note)
        try:
            await self.gemini_session.send_text("(Application instruction, not from the caller.) " + text)
        except Exception as exc:
            logger.warning("Could not deliver emergency note: %s", type(exc).__name__)

    async def _report_emergency_result(self, result: dict, reported: set[str] | None = None):
        """Tell the voice agent the real outcome, and say plainly what it may and may not claim.

        ``reported`` holds the actions the voice agent already heard about; when the outcome adds nothing new
        it stays quiet rather than make the voice agent repeat itself to the caller.
        """
        if self.gemini_session is None:
            return
        outcomes = {name: (action or {}).get("status") for name, action in (result.get("actions") or {}).items()}
        if reported is not None and outcomes and all(name in reported for name in outcomes):
            return
        if result.get("status") == "submitted":
            guidance = ("Only the actions listed as submitted were accepted by a provider. Do not say anything was delivered, "
                        "answered, or that help is coming. Be honest about any action that was not submitted.")
        else:
            guidance = ("Nothing was sent. Tell the caller honestly that you could not reach the responder, "
                        "and, if it is safe, encourage them to call the local emergency number now.")
        summary = {"status": result.get("status"), "reason": result.get("reason"), "actions": outcomes}
        try:
            await self.gemini_session.send_text(
                "Emergency action result (trusted application status): " + json.dumps(summary) + " " + guidance)
        except Exception as exc:
            logger.warning("Could not deliver emergency status: %s", type(exc).__name__)

    def _maybe_request_memory_consent(self):
        """Once, after a few exchanges and never while the coordinator rates the call urgent,
        have the agent ask the consent question. Deterministic, so it does not depend on the
        model remembering to ask."""
        if not self.memory_store or self.memory_ask_sent or self.caller_turns < 4 or self.gemini_session is None:
            return
        if self.coordinator and self.coordinator.latest().get("risk") == "urgent":
            return
        self.memory_ask_sent = True
        days = self.memory_store.retention_days
        task = asyncio.create_task(self.gemini_session.send_text(
            "(Application instruction, not from the caller.) At a gentle moment in your next reply, ask exactly: "
            f"'May I remember your support preferences for future calls for up to {days} days?' "
            "Tell them they can say no or ask you to delete it, then wait for their answer."))
        self.memory_tasks.add(task)
        task.add_done_callback(self.memory_tasks.discard)

    async def _on_gemini_audio(self, pcm24k_chunk: bytes) -> None:
        """Queue Gemini audio for the outbound writer."""
        self._outbound.put_nowait(pcm24k_chunk)


    async def _drain_outbound(self) -> None:
        """Resample, encode and pace Gemini audio out to Twilio."""
        while True:
            pcm24k_chunk = await self._outbound.get()
            if not self.stream_sid:
                continue

            # 1. Downsample 24kHz PCM16 -> 8kHz PCM16
            pcm8k = self.resampler.resample(pcm24k_chunk)
            if not pcm8k:
                continue

            # 2. Encode to 8kHz G.711 mu-law
            mulaw = pcm16_to_mulaw(pcm8k)

            # 3. Chunk into 20ms (160 bytes) frames for smooth telephony pacing
            for frame in self.chunker.add_samples(mulaw):
                await self.ws.send_text(
                    json.dumps(
                        {
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {"payload": base64.b64encode(frame).decode("utf-8")},
                        }
                    )
                )

    async def _on_interrupted(self):
        """Twilio user barged in: clear downstream playback buffer immediately."""
        if not self.stream_sid:
            return
        logger.info("Barge-in: clearing Twilio audio buffer")
        # Drop audio already queued locally, or we would keep feeding Twilio
        # frames from the turn the caller just interrupted.
        while not self._outbound.empty():
            self._outbound.get_nowait()
        await self.ws.send_text(
            json.dumps({"event": "clear", "streamSid": self.stream_sid})
        )

    async def _on_turn_complete(self, caller_text: str, model_text: str):
        if os.getenv("DEBUG_LOG_TRANSCRIPTS", "false").lower() == "true":  # dev only: never enable with real callers
            logger.info("DEBUG transcript | caller=%r | agent=%r", caller_text, model_text)
        if caller_text:
            answer = re.sub(r"[^a-z ]", "", caller_text.lower()).strip()
            previous_consent = self.emergency_context.responder_consent
            if self.consent_question_pending:
                # Closed English / Pidgin / Yoruba / Hausa matcher; anything unclear counts as "not consented".
                self.emergency_context.responder_consent = consent.is_yes(caller_text)
                self.consent_question_pending = False
            elif consent.is_withdrawal(caller_text):
                self.emergency_context.responder_consent = False
            if previous_consent != self.emergency_context.responder_consent:
                log_tool_event("responder_consent_changed", call_sid=self.call_sid,
                               granted=self.emergency_context.responder_consent)
            if previous_consent and not self.emergency_context.responder_consent:
                try:
                    await cancel_session_jobs(self.harness_config["configurable"]["thread_id"], responder_only=True)
                except Exception as exc:
                    logger.warning("Emergency consent revocation failed: %s", type(exc).__name__)
            if self.coordinator:
                self.coordinator.update_profile(extract_preferences(caller_text, {}))
            if self.memory_store:
                pending = self.memory_consent_pending
                self.memory_consent_pending = False
                task = asyncio.create_task(self._process_memory(answer, pending))
                self.memory_tasks.add(task)
                task.add_done_callback(self.memory_tasks.discard)
            if self.on_transcript:
                await self.on_transcript("caller", caller_text)
        if model_text:
            normalized = re.sub(r"[^a-z ]", "", model_text.lower())
            self.consent_question_pending = CONSENT_QUESTION in normalized
            if self.memory_store:
                # The agent speaks the retention period ("thirty days"), so match only the stable part.
                question = "may i remember your support preferences for future calls"
                self.memory_consent_pending = question in normalized and not self.consent_question_pending
            if self.on_transcript:
                await self.on_transcript("assistant", model_text)
        if self.coordinator:
            self.coordinator.observe(caller_text, model_text)
        if caller_text:
            self.caller_turns += 1
            self._maybe_request_memory_consent()
