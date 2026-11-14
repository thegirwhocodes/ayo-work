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
from harness.agent import create_harness, load_skill_files
from harness.emergency_jobs import cancel_job, cancel_session_jobs, create_job, get_job_result
from harness.call_audio import SpeechCollector
from harness.caller_identity import verify_caller
from harness.datastore.voice import PostgresVoiceStore
from harness.datastore.memory import SupportMemoryStore
from harness.corpus.model import cohere_embeddings
from harness.corpus.store import CorpusStore
from harness.coordinator import Coordinator, GeminiStructuredLLM, load_skills_text
from harness.support import extract_preferences
from harness.tools.declarations import RELAY_DECLARATION, SUPPORT_GUIDANCE_DECLARATION
from harness.tools import EmergencyCallContext, EmergencyTools, SpeakerVerifier, VoiceVerificationTool

logger = logging.getLogger("sabi.voice.twilio")

CONSENT_QUESTION = "may i text the emergency responder with what you shared"


class TwilioMediaBridge:
    def __init__(
        self,
        websocket: WebSocket,
        system_prompt: str,
        voice_name: str = "Puck",
        on_transcript: Optional[Callable[[str, str], Awaitable[None]]] = None,
        on_safety_alert: Optional[Callable[[dict], Awaitable[None]]] = None,
    ):
        self.ws = websocket
        self.system_prompt = system_prompt
        self.voice_name = voice_name
        self.on_transcript = on_transcript
        self.on_safety_alert = on_safety_alert

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
        self.verification_status = None
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
        return self.system_prompt + build_location_briefing(self.location) + support + (
            "\nFor emergency actions use relay_to_harness. Before requesting a responder text, "
            "ask exactly: 'May I text the emergency responder with what you shared?' "
            "Wait for the caller's clear yes; never claim consent yourself. "
            "For location requests, also ask whether the caller wants someone to come get them. "
            "Never promise help is coming or a call was placed without confirmation. "
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
        self.gemini_session = GeminiLiveSession(
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
            logger.error(f"Failed to connect to Gemini Live: {e}")
            self.gemini_session = None
            return False

    async def run(self):
        self._running = True
        logger.info("Starting Twilio Media Bridge...")

        try:
            await self.verify_call()
            await self._setup_support()
            if not await self._connect_gemini():
                await self.ws.close()
                return
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
                    self.handle_start(data.get("start", {}))
                    if not await self._connect_gemini():
                        break
                    tasks.append(asyncio.create_task(self.gemini_session.receive_forever()))
                    tasks.append(asyncio.create_task(self._drain_outbound()))

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

    async def verify_call(self):
        """Collect initial voiced audio and enroll or verify before conversation."""
        collector = SpeechCollector()
        caller_id = None
        deadline = asyncio.get_running_loop().time() + 30
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("not enough speech for verification")
            data = json.loads(await asyncio.wait_for(self.ws.receive_text(), timeout=remaining))
            event = data.get("event")
            if event == "start":
                start = data.get("start", {})
                self.handle_start(start)
                token = start.get("customParameters", {}).get("caller_token", "")
                caller_id = verify_caller(token, os.getenv("TWILIO_AUTH_TOKEN", ""))
                self.emergency_context.caller_number = caller_id
            elif event == "media" and caller_id:
                payload = data.get("media", {}).get("payload", "")
                if collector.add(mulaw_to_pcm16(base64.b64decode(payload, validate=True))):
                    break
            elif event == "stop":
                raise RuntimeError("call ended before verification")

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
            raise RuntimeError("speaker verification tool was not called")
        if result.status not in ("matched", "enrolled"):
            raise RuntimeError("speaker did not match enrollment")
        self.verification_status = result.status
        logger.info("Speaker check result: %s", result.status)

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
        if action not in {"send_text_message", "request_location", "start_phone_call"}:
            return {"status": "unavailable", "reason": "unknown_action"}
        message = args.get("message", "")
        if not isinstance(message, str) or len(message) > 1600:
            return {"status": "unavailable", "reason": "invalid_message"}
        if action == "send_text_message" and not self.emergency_context.responder_consent:
            return {"status": "consent_required"}
        if self.harness_agent is None:
            return {"status": "unavailable", "reason": "harness_not_ready"}
        tools = EmergencyTools(self.emergency_context, action, message)
        try:
            token = await create_job(tools, self.harness_config["configurable"]["thread_id"])
        except Exception as exc:
            logger.warning("Emergency job creation failed: %s", type(exc).__name__)
            return {"status": "unavailable", "reason": "job_store_unavailable"}
        task = asyncio.create_task(self.run_emergency_job(token))
        self.emergency_tasks.add(task)
        task.add_done_callback(self.emergency_tasks.discard)
        return {"status": "pending"}

    async def run_emergency_job(self, token: str):
        try:
            async with self.harness_lock:
                await asyncio.wait_for(self.harness_agent.ainvoke({"messages": [{"role": "user", "content":
                    "Start the emergency async subagent now with the exact task description "
                    f"emergency-job:{token}. Do not change the description or wait for it."}]},
                    config=self.harness_config), timeout=20)
            async with asyncio.timeout(90):
                while True:
                    result = await get_job_result(token)
                    if result is not None:
                        break
                    await asyncio.sleep(0.5)
        except Exception as exc:
            logger.warning("Emergency subagent failed: %s", type(exc).__name__)
            result = {"status": "unknown", "reason": "subagent_failed_or_timed_out"}
        finally:
            try:
                await cancel_job(token)
            except Exception as exc:
                logger.warning("Emergency job cancellation failed: %s", type(exc).__name__)
        if self.gemini_session is not None:
            try:
                await self.gemini_session.send_text(
                    "Emergency action result (trusted application status; never claim delivery or dispatch "
                    "unless confirmed): " + json.dumps(result)
                )
            except Exception as exc:
                logger.warning("Could not deliver emergency status: %s", type(exc).__name__)

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
            answer = re.sub(r"[^a-z ]", "", caller_text.lower()).strip()
            previous_consent = self.emergency_context.responder_consent
            if self.consent_question_pending:
                self.emergency_context.responder_consent = answer in {
                    "yes", "yes please", "yes you may", "yes i agree", "i agree", "please do"
                }
                self.consent_question_pending = False
            elif answer in {"no", "no thanks", "stop", "i changed my mind"}:
                self.emergency_context.responder_consent = False
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
                question = f"may i remember your support preferences for future calls for up to {self.memory_store.retention_days} days"
                self.memory_consent_pending = question in normalized and not self.consent_question_pending
            if self.on_transcript:
                await self.on_transcript("assistant", model_text)
        if self.coordinator:
            self.coordinator.observe(caller_text, model_text)
