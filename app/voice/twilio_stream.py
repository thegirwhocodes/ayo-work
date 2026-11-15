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

from app.voice.consent import (
    CALLER_TEXT, CONSENT_FOR_ACTION, QUESTIONS, RESPONDER,
    ConsentTracker, memory_question_asked, normalize,
)
from app.voice.audio import (
    mulaw_to_pcm16,
    pcm16_to_mulaw,
    Downsample24kTo8k,
    FrameChunker,
)
from app.voice.caller_location import CallerLocation, build_location_briefing
from app.voice.gemini_live import GeminiLiveSession, LiveConfig
from app.voice.openai_realtime import OpenAIRealtimeSession
from harness.agent import create_harness, load_skill_files
from harness.emergency_jobs import cancel_job, cancel_session_jobs, create_job, execute_job, get_job_result
from harness.call_audio import SpeechCollector
from harness.caller_identity import ANONYMOUS, verify_caller
from harness.datastore.voice import PostgresVoiceStore
from harness.datastore.memory import SupportMemoryStore
from harness.corpus.model import cohere_embeddings
from harness.corpus.store import CorpusStore
from harness.coordinator import Coordinator, GeminiStructuredLLM, load_skills_text
from harness.support import extract_preferences
from harness.tools.declarations import RELAY_DECLARATION, SUPPORT_GUIDANCE_DECLARATION
from harness.tools import EmergencyCallContext, EmergencyTools, SpeakerVerifier, VoiceVerificationTool
from settings import get_settings

logger = logging.getLogger("ayo.voice.twilio")

CONSENT_QUESTION = normalize(QUESTIONS[RESPONDER])  # "may i text the emergency responder with what you shared"
RUNTIME_CONTEXT_PLACEHOLDER = "{RUNTIME_CONTEXT}"
FIRST_WORDS_MAX_BYTES = 30 * 8000 * 2  # keep at most the last 30 s of caller audio for replay
GREETING_MESSAGE_BYTES = 1600          # 200 ms of 8 kHz mu-law per Twilio media message
VERIFICATION_TIMEOUT_S = 12.0          # how long to wait for enough speech before carrying on as "unsure"

# Emergency dispatch. The harness model is only a courier: if it or the Agent Server is slow or down, the
# app sends the job itself, so a model outage can never stop an approved emergency text.
AGENT_DISPATCH_TIMEOUT_S = 8.0         # how long the harness model has to hand the job to the Agent Server
AGENT_RESULT_WAIT_S = 10.0             # then the app sends the job itself
EMERGENCY_RESULT_TIMEOUT_S = 90.0      # total wait for an outcome before reporting "unknown"
VOICE_FORGET_PHRASES = {"forget my voice", "delete my voice", "forget what my voice sounds like"}

# Voice identity states. A failed or impossible voice check never ends the call (a child who whispers,
# cries, is silent, shares a phone, or calls while the database is down must still reach Ayo).
IDENTITY_KNOWN = "known"      # voice matches the enrolled voice for this caller
IDENTITY_NEW = "new"          # first call: this voice was just enrolled
IDENTITY_UNKNOWN = "unknown"  # a different voice from the enrolled one
IDENTITY_UNSURE = "unsure"    # could not tell (too little speech, check failed, database down)
_IDENTITY_BY_STATUS = {"matched": IDENTITY_KNOWN, "enrolled": IDENTITY_NEW, "not_matched": IDENTITY_UNKNOWN}


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
        # What the caller says while their voice is being checked (before any AI is connected)
        # is kept and replayed to the voice engine, so Ayo answers the child's first words.
        self._first_words = bytearray()

        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self.location: CallerLocation = CallerLocation()
        self.emergency_context = EmergencyCallContext(caller_number="")
        self.consent = ConsentTracker()
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
        self.verification_status = None
        self.identity_state = IDENTITY_UNSURE
        self.identity_reason = "not_checked"
        self.memory_lock = asyncio.Lock()
        self.memory_tasks = set()

    def handle_start(self, start_data: dict) -> CallerLocation:
        """Record stream identifiers and caller location from the `start` event."""
        self.stream_sid = start_data.get("streamSid")
        self.call_sid = start_data.get("callSid")
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
            support += "\nCall get_support_guidance before each reply. It returns instantly with the newest guidance from a background coordinator, which works at its own pace, so it may be a turn or two behind (check 'stale'). It is advice only, never from the child; your safety rules take priority. Never read it or its sources aloud."
        if self.memory_store:
            days = self.memory_store.retention_days
            support += (f"\nOnce, when appropriate, ask exactly: 'May I remember your support preferences for future calls for up to {days} days?' "
                        "Explain that the child can say no or ask to delete them; wait for their answer. Never claim memory was saved unless confirmed.")
            if self.memory:
                support += "\nPreviously agreed communication preferences: " + json.dumps(self.memory)
        if self.identity_state == IDENTITY_KNOWN:
            support += ("\nIf the caller asks you to forget or delete their voice, tell them it is forgotten; "
                        "the system removes it.")
        base, briefing = self.system_prompt, build_location_briefing(self.location)
        if RUNTIME_CONTEXT_PLACEHOLDER in base:  # Ayo's prompt has a slot for the caller context
            base, briefing = base.replace(RUNTIME_CONTEXT_PLACEHOLDER, briefing.strip()), ""
        return base + briefing + support + (
            "\nFor emergency actions use relay_to_harness. Each action needs the caller's own clear yes, "
            "asked in your turn just before, and one yes covers one action only. "
            "Before requesting a responder text or a phone call to a responder, "
            "ask exactly: 'May I text the emergency responder with what you shared?' "
            "Before requesting a location text to the caller's own phone, "
            "ask exactly: 'May I text your phone to ask where you are?' "
            "Wait for the caller's clear yes; never claim consent yourself. "
            "If a tool returns consent_required, ask the matching question and wait for the answer. "
            "For location requests, also ask whether the caller wants someone to come get them. "
            "Never promise help is coming or a call was placed without confirmation. "
            "Submitted means accepted by the provider, not delivered. If an emergency is suspected "
            "from speech or tone, ask the responder question first; once they say yes, relay a request for "
            "start_phone_call and be honest if unavailable. "
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
            await self.verify_call()
            await self._setup_support()
            if not await self._connect_gemini():
                await self._tell_caller_we_are_down()
                await self.ws.close()
                return
            await self._replay_first_words()
        except Exception as e:
            logger.warning("Call verification failed: %s", type(e).__name__)
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

                elif event == "start":
                    # verify_call already handled the one real start event. A second one must not swap in
                    # new stream ids/location or reconnect the voice engine without a voice check.
                    logger.warning("Ignoring a repeated start event")

                elif event == "media":
                    media_payload = data.get("media", {}).get("payload")
                    if media_payload and self.gemini_session:
                        # 1. Base64 decode Twilio mu-law
                        mulaw_bytes = base64.b64decode(media_payload)
                        # 2. Decode mu-law to 8kHz PCM16
                        pcm16_bytes = mulaw_to_pcm16(mulaw_bytes)
                        # 3. Stream to Gemini Live
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
            for task in self.emergency_tasks:
                task.cancel()
            if self.emergency_tasks:
                await asyncio.gather(*self.emergency_tasks, return_exceptions=True)
            await self._cancel_pending_jobs()
            if self.coordinator:
                await self.coordinator.close()
            if self.memory_tasks:
                await asyncio.gather(*self.memory_tasks, return_exceptions=True)
            if self.gemini_session:
                await self.gemini_session.close()
            for task in tasks:
                task.cancel()
            logger.info("Twilio Media Bridge closed.")

    async def verify_call(self):
        """Collect initial voiced audio, then enroll or verify the caller's voice.

        Sets `identity_state` (known / new / unknown / unsure) and never ends the call over the
        result. Only a forged caller token or a hang-up before the stream starts is fatal. A caller whose
        number is withheld arrives with a signed, number-less token: the call carries on as "unsure".
        """
        collector = SpeechCollector()
        caller_id = None
        started = anonymous = False
        enough_speech = True
        deadline = asyncio.get_running_loop().time() + VERIFICATION_TIMEOUT_S
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                enough_speech = False
                break
            try:
                data = json.loads(await asyncio.wait_for(self.ws.receive_text(), timeout=remaining))
            except asyncio.TimeoutError:
                enough_speech = False
                break
            event = data.get("event")
            if event == "start":
                if started:  # the first start event fixes this call's identity; later ones change nothing
                    logger.warning("Ignoring a repeated start event during the voice check")
                    continue
                started = True
                start = data.get("start", {})
                self.handle_start(start)
                token = (start.get("customParameters") or {}).get("caller_token", "")
                caller_id = verify_caller(token, os.getenv("TWILIO_AUTH_TOKEN", ""))
                self._start_greeting()
                if caller_id == ANONYMOUS:
                    anonymous, caller_id = True, None
                    break
                self.emergency_context.caller_number = caller_id
            elif event == "media" and caller_id:
                payload = data.get("media", {}).get("payload", "")
                pcm = mulaw_to_pcm16(base64.b64decode(payload, validate=True))
                self._keep_first_words(pcm)
                if collector.add(pcm):
                    break
            elif event == "stop":
                raise RuntimeError("call ended before verification")

        if anonymous:
            self._set_identity(IDENTITY_UNSURE, "no_caller_number")
            return
        if not caller_id:
            raise RuntimeError("no caller identity: the stream never started")
        if not enough_speech:
            self._set_identity(IDENTITY_UNSURE, "not_enough_speech")
            return
        try:
            await self._check_voice(collector, caller_id)
        except Exception as exc:  # database down, model failure, ...: carry on, never hang up
            self._set_identity(IDENTITY_UNSURE, f"check_failed:{type(exc).__name__}")

    async def _tell_caller_we_are_down(self) -> None:
        """The voice engine could not start. Rather than dead air, redirect the live call to a short spoken
        message with the local emergency number, then hang up."""
        settings = get_settings()
        if not (self.call_sid and settings.twilio_account_sid and settings.twilio_auth_token):
            return
        number = self.location.emergency_number
        danger = (f"If you are in danger, call {' '.join(number)} now." if number
                  else "If you are in danger, call your local emergency number now.")
        twiml = ("<Response><Say>I am sorry. Ayo cannot talk right now. " + danger +
                 " Please try calling again in a few minutes.</Say><Hangup/></Response>")

        def redirect() -> None:
            from twilio.rest import Client
            Client(settings.twilio_account_sid, settings.twilio_auth_token).calls(self.call_sid).update(twiml=twiml)

        try:
            await asyncio.to_thread(redirect)
        except Exception as exc:
            logger.warning("Could not play the fallback message: %s", type(exc).__name__)

    def _set_identity(self, state: str, reason: str) -> None:
        self.identity_state, self.identity_reason = state, reason
        logger.info("Voice identity: %s (%s)", state, reason)

    async def _check_voice(self, collector, caller_id: str) -> None:
        store = PostgresVoiceStore(os.getenv("DATABASE_URL", ""))
        await asyncio.to_thread(store.initialize)
        verifier = SpeakerVerifier(store, float(os.getenv("VOICE_VERIFICATION_THRESHOLD", "0.75")))
        voice_tool = VoiceVerificationTool(verifier, caller_id, collector.audio_16khz())
        agent = create_harness(voice_tool)
        self.harness_agent = agent
        await agent.ainvoke({
            "messages": [{"role": "user", "content": "Run speaker verification now."}],
            "files": load_skill_files(),
        }, config=self.harness_config)
        voice_tool.audio_data = b""
        result = voice_tool.result
        if result is None:
            self._set_identity(IDENTITY_UNSURE, "verification_tool_not_called")
            return
        self.verification_status = result.status if result.status in _IDENTITY_BY_STATUS else None
        self._set_identity(_IDENTITY_BY_STATUS.get(result.status, IDENTITY_UNSURE), result.status)

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

    def _keep_first_words(self, pcm: bytes) -> None:
        self._first_words.extend(pcm)
        overflow = len(self._first_words) - FIRST_WORDS_MAX_BYTES
        if overflow > 0:
            del self._first_words[:overflow]

    async def _replay_first_words(self) -> None:
        """Give the voice engine what the caller said during the voice check, in 20 ms frames."""
        audio, self._first_words = bytes(self._first_words), bytearray()
        if not audio or self.gemini_session is None:
            return
        for i in range(0, len(audio), 320):
            await self.gemini_session.send_audio(audio[i:i + 320])
        logger.info("Replayed %.1f s of the caller's first words to the voice engine", len(audio) / 16000)

    async def _setup_support(self):
        database_url = os.getenv("DATABASE_URL", "")
        if os.getenv("SUPPORT_MEMORY_ENABLED", "false").lower() == "true":
            try:
                self.memory_store = SupportMemoryStore(database_url, os.getenv("SUPPORT_MEMORY_ENCRYPTION_KEY", ""),
                    os.getenv("SUPPORT_MEMORY_IDENTITY_KEY", ""), int(os.getenv("SUPPORT_MEMORY_RETENTION_DAYS", "30")))
                await asyncio.to_thread(self.memory_store.purge_expired)
                if self.verification_status == "matched":
                    self.memory = await asyncio.to_thread(self.memory_store.read, self.emergency_context.caller_number)
            except Exception as exc:
                self.memory_store = None
                logger.warning("Support memory disabled: %s", type(exc).__name__)
        if os.getenv("SUPPORT_GUIDANCE_ENABLED", "false").lower() == "true":
            self._setup_coordinator(database_url)

    def _setup_coordinator(self, database_url):
        """Background coordinator: skills always, retrieval when the corpus is configured."""
        try:
            embeddings = corpus = None
            if database_url and os.getenv("COHERE_API_KEY"):
                embeddings, corpus = cohere_embeddings(), CorpusStore(database_url)
            llm = GeminiStructuredLLM(os.getenv("COORDINATOR_MODEL") or os.getenv("HARNESS_MODEL", "gemini-2.5-flash"),
                                      os.getenv("GEMINI_API_KEY", ""))
            self.coordinator = Coordinator(llm, load_skills_text(), embeddings, corpus,
                                           profile=self.memory, on_profile=self._on_profile)
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
                if self.verification_status == "matched" and (answer in {"delete my memory", "forget my information", "forget what you remember", "delete what you remember", "stop remembering"} or (pending and answer in {"no", "no thanks", "i do not agree"})):
                    await asyncio.to_thread(self.memory_store.delete, caller_id)
                    self.memory = None
                    if self.coordinator:
                        self.coordinator.reset_profile()
                elif pending and answer in {"yes", "yes please", "yes i agree", "i agree"}:
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
        if name == "get_support_guidance" and self.coordinator:
            return self.coordinator.latest()
        if name != "relay_to_harness" or not isinstance(args, dict):
            return {"status": "unavailable", "reason": "unknown_tool"}
        action = args.get("action")
        if action not in CONSENT_FOR_ACTION:
            return {"status": "unavailable", "reason": "unknown_action"}
        message = args.get("message", "")
        if not isinstance(message, str) or len(message) > 1600:
            return {"status": "unavailable", "reason": "invalid_message"}
        # Every action needs the caller's own live, unspent yes (single use, expires). Nothing is sent without it.
        kind = CONSENT_FOR_ACTION[action]
        spent = self.consent.consume(kind)
        self._sync_consent_flags()
        if not spent:
            return {"status": "consent_required", "ask": QUESTIONS[kind]}
        context = EmergencyCallContext(
            self.emergency_context.caller_number,
            responder_consent=kind == RESPONDER,
            caller_text_consent=kind == CALLER_TEXT,
        )
        task = asyncio.create_task(self.run_emergency(EmergencyTools(context, action, message)))
        self.emergency_tasks.add(task)
        task.add_done_callback(self.emergency_tasks.discard)
        return {"status": "pending"}

    def _sync_consent_flags(self) -> None:
        """Mirror live consent onto the call context (what tools and job snapshots read)."""
        self.emergency_context.responder_consent = self.consent.allows(RESPONDER)
        self.emergency_context.caller_text_consent = self.consent.allows(CALLER_TEXT)

    def _ensure_harness_agent(self) -> None:
        """The agent is normally built during the voice check; build it now if that was skipped."""
        if self.harness_agent is not None or not self.emergency_context.caller_number:
            return
        try:
            self.harness_agent = create_harness(VoiceVerificationTool(None, self.emergency_context.caller_number, b""))
        except Exception as exc:
            logger.warning("Harness unavailable: %s", type(exc).__name__)

    def _use_agent_dispatch(self) -> bool:
        if get_settings().emergency_dispatch == "direct":
            return False
        if self.harness_agent is None:
            self._ensure_harness_agent()
        return self.harness_agent is not None

    async def _cancel_pending_jobs(self) -> None:
        settings = get_settings()
        if not (settings.database_url and settings.emergency_job_encryption_key):
            return  # no job store configured, so no queued jobs exist
        try:
            await cancel_session_jobs(self.harness_config["configurable"]["thread_id"])
        except Exception as exc:
            logger.warning("Emergency job cleanup failed: %s", type(exc).__name__)

    async def run_emergency(self, tools: EmergencyTools) -> None:
        """Carry out one approved emergency action and tell the voice model what happened.

        Preferred route: encrypted job -> harness model -> Agent Server -> app callback. The model is only
        a courier, so if the job store, the model or the Agent Server is unavailable the app sends it itself.
        """
        token = None
        result = None
        try:
            if self._use_agent_dispatch():
                try:
                    token = await create_job(tools, self.harness_config["configurable"]["thread_id"])
                except Exception as exc:
                    logger.warning("Emergency job creation failed: %s", type(exc).__name__)
                else:
                    result = await self._dispatch_via_agent(token)
            if token is None:
                result = await self._run_directly(tools)  # no job exists, so this cannot double-send
        except Exception as exc:
            logger.warning("Emergency action failed: %s", type(exc).__name__)
        finally:
            if token:
                try:
                    await cancel_job(token)
                except Exception as exc:
                    logger.warning("Emergency job cancellation failed: %s", type(exc).__name__)
        await self._report_emergency_result(result or {"status": "unknown", "reason": "no_outcome_in_time"})

    async def _start_subagent(self, token: str) -> None:
        async with self.harness_lock:
            await self.harness_agent.ainvoke({"messages": [{"role": "user", "content":
                "Start the emergency async subagent now with the exact task description "
                f"emergency-job:{token}. Do not change the description or wait for it."}]},
                config=self.harness_config)

    async def _poll_result(self, token: str, until: float) -> dict | None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                result = await get_job_result(token)
            except Exception as exc:
                logger.warning("Emergency result read failed: %s", type(exc).__name__)
                result = None
            if result is not None:
                return result
            if loop.time() >= until:
                return None
            await asyncio.sleep(0.5)

    async def _dispatch_via_agent(self, token: str) -> dict | None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        handed_over = True
        try:
            # The lock wait counts against the timeout too, so a stuck earlier job cannot hold this one up.
            await asyncio.wait_for(self._start_subagent(token), timeout=AGENT_DISPATCH_TIMEOUT_S)
        except Exception as exc:
            handed_over = False
            logger.warning("Harness could not hand the job over (%s); the app will send it itself", type(exc).__name__)
        if handed_over:
            result = await self._poll_result(token, started + AGENT_RESULT_WAIT_S)
            if result is not None:
                return result
            logger.warning("No result from the Agent Server in %.0fs; the app will send the job itself", AGENT_RESULT_WAIT_S)
        # The app holds the job, so it can send it. The claim is atomic: if the Agent Server's callback got
        # there first this returns invalid_or_expired_job and we just keep waiting for its result.
        try:
            await execute_job(token)
        except Exception as exc:
            logger.warning("Direct emergency execution failed: %s", type(exc).__name__)
        return await self._poll_result(token, started + EMERGENCY_RESULT_TIMEOUT_S)

    async def _run_directly(self, tools: EmergencyTools) -> dict:
        try:
            await tools.run(tools.action)
        except Exception as exc:
            logger.warning("Direct emergency action failed: %s", type(exc).__name__)
            return {"status": "unavailable", "reason": "tool_failed"}
        return tools.result or {"status": "unavailable", "reason": "tool_not_called"}

    async def _report_emergency_result(self, result: dict) -> None:
        if self.gemini_session is None:
            return
        try:
            await self.gemini_session.send_text(
                "Emergency action result (trusted application status; never claim delivery or dispatch "
                "unless confirmed): " + json.dumps(result)
            )
        except Exception as exc:
            logger.warning("Could not deliver emergency status: %s", type(exc).__name__)

    async def _forget_voice(self) -> None:
        """The caller asked to be forgotten and their voice was recognised: delete the voiceprint."""
        caller = self.emergency_context.caller_number
        try:
            store = PostgresVoiceStore(os.getenv("DATABASE_URL", ""))
            deleted = await asyncio.to_thread(store.delete, caller)
        except Exception as exc:
            logger.warning("Voice reset failed: %s", type(exc).__name__)
            return
        self.verification_status = None
        self._set_identity(IDENTITY_UNSURE, "voice_forgotten" if deleted else "voice_not_found")

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
        if caller_text:
            answer = normalize(caller_text)
            outcome = self.consent.observe_caller(caller_text)
            self._sync_consent_flags()
            if outcome.said_no or outcome.revoked:
                await self._cancel_pending_jobs()  # a no / stop withdraws anything still queued
            if answer in VOICE_FORGET_PHRASES and self.identity_state == IDENTITY_KNOWN:
                task = asyncio.create_task(self._forget_voice())
                self.memory_tasks.add(task)
                task.add_done_callback(self.memory_tasks.discard)
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
            # Each model turn replaces the pending consent question, so a yes only counts if the question
            # was asked in the turn right before it.
            self.consent.observe_model(model_text)
            if self.memory_store:
                self.memory_consent_pending = memory_question_asked(model_text) and self.consent.pending is None
            if self.on_transcript:
                await self.on_transcript("assistant", model_text)
        if self.coordinator:
            self.coordinator.observe(caller_text, model_text)
