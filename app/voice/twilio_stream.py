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
from harness.agent import create_harness
from harness.call_audio import SpeechCollector
from harness.caller_identity import verify_caller
from harness.datastore.voice import PostgresVoiceStore
from harness.tools import SpeakerVerifier, VoiceVerificationTool

logger = logging.getLogger("sabi.voice.twilio")


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
        self.gemini_session: Optional[GeminiLiveSession] = None
        self.resampler = Downsample24kTo8k()
        self.chunker = FrameChunker(frame_bytes=160)  # 20ms of 8kHz mu-law = 160 bytes
        # Gemini's audio callback hands frames to this
        # queue and a writer task does the awaiting.
        self._outbound: asyncio.Queue[bytes] = asyncio.Queue()
        self._running = False

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
        return self.system_prompt + build_location_briefing(self.location)

    async def _connect_gemini(self) -> bool:
        cfg = LiveConfig(
            system_prompt=self.build_system_prompt(),
            voice=self.voice_name,
            input_rate=8000,
        )
        self.gemini_session = GeminiLiveSession(
            cfg,
            on_audio=self._on_gemini_audio,
            on_interrupted=self._on_interrupted,
            on_turn_complete=self._on_turn_complete,
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
        await agent.ainvoke({"messages": [{"role": "user", "content": "Run speaker verification now."}]})
        result = voice_tool.result
        if result is None:
            raise RuntimeError("speaker verification tool was not called")
        if result.status not in ("matched", "enrolled"):
            raise RuntimeError("speaker did not match enrollment")
        logger.info("Speaker check result: %s", result.status)

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
            logger.info(f"[CALLER]: {caller_text}")
            if self.on_transcript:
                await self.on_transcript("caller", caller_text)
        if model_text:
            logger.info(f"[SABI]: {model_text}")
            if self.on_transcript:
                await self.on_transcript("assistant", model_text)
