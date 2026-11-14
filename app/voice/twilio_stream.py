"""Twilio Media Streams WebSocket handler.

Handles full-duplex streaming audio between a Twilio phone call and Gemini Live.
- Twilio sends: 8kHz mu-law audio frames (base64 encoded)
- Server decodes: mu-law -> 8kHz PCM16 -> Gemini Live (16kHz or 8kHz PCM)
- Gemini Live yields: 24kHz PCM16 audio
- Server downsamples: 24kHz -> 8kHz PCM16 -> mu-law -> base64 -> Twilio
- Interruption handling: sends {"event": "clear"} to flush Twilio playback buffer.
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
        self.gemini_session: Optional[GeminiLiveSession] = None
        self.resampler = Downsample24kTo8k()
        self.chunker = FrameChunker(frame_bytes=160)  # 20ms of 8kHz mu-law = 160 bytes
        self._running = False

    async def run(self):
        self._running = True
        logger.info("Starting Twilio Media Bridge...")

        try:
            await self.verify_call()
        except Exception as e:
            logger.warning("Call verification failed: %s", type(e).__name__)
            await self.ws.close()
            return

        # Create Gemini Live session
        self.gemini_session = GeminiLiveSession(
            LiveConfig(system_prompt=self.system_prompt, voice=self.voice_name),
            on_audio=self._on_gemini_audio,
            on_turn_complete=self.on_turn_complete,
            on_interrupted=self._on_interrupted,
        )

        # Connect to Gemini Live
        try:
            await self.gemini_session.connect()
        except Exception as e:
            logger.error(f"Failed to connect to Gemini Live: {e}")
            await self.ws.close()
            return

        gemini_loop_task = asyncio.create_task(self.gemini_session.receive_forever())

        try:
            while self._running:
                raw_text = await self.ws.receive_text()
                data = json.loads(raw_text)
                event = data.get("event")

                if event == "connected":
                    logger.info("Twilio connected event received")

                elif event == "start":
                    start_data = data.get("start", {})
                    self.stream_sid = start_data.get("streamSid")
                    self.call_sid = start_data.get("callSid")
                    logger.info(f"Twilio stream started: stream_sid={self.stream_sid} call_sid={self.call_sid}")

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
            gemini_loop_task.cancel()
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
                self.stream_sid = start.get("streamSid")
                self.call_sid = start.get("callSid")
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

    async def on_turn_complete(self, caller: str, model: str):
        if caller:
            await self._on_user_transcript(caller)
        if model:
            await self._on_model_transcript(model)

    async def _on_gemini_audio(self, pcm24k_chunk: bytes):
        """Called when Gemini Live emits 24kHz PCM16 audio."""
        if not self.stream_sid:
            return

        # 1. Downsample 24kHz PCM16 -> 8kHz PCM16
        pcm8k = self.resampler.resample(pcm24k_chunk)
        if not pcm8k:
            return

        # 2. Encode to 8kHz G.711 mu-law
        mulaw = pcm16_to_mulaw(pcm8k)

        # 3. Chunk into 20ms (160 bytes) frames for smooth telephony pacing
        frames = self.chunker.add_samples(mulaw)
        for frame in frames:
            b64_audio = base64.b64encode(frame).decode("utf-8")
            media_message = {
                "event": "media",
                "streamSid": self.stream_sid,
                "media": {
                    "payload": b64_audio,
                },
            }
            await self.ws.send_text(json.dumps(media_message))

    async def _on_interrupted(self):
        """Twilio user barged in: clear downstream playback buffer immediately."""
        if not self.stream_sid:
            return
        logger.info("Barge-in: clearing Twilio audio buffer")
        clear_message = {
            "event": "clear",
            "streamSid": self.stream_sid,
        }
        await self.ws.send_text(json.dumps(clear_message))

    async def _on_user_transcript(self, text: str):
        logger.info(f"[CALLER]: {text}")
        if self.on_transcript:
            await self.on_transcript("caller", text)

    async def _on_model_transcript(self, text: str):
        logger.info(f"[SABI]: {text}")
        if self.on_transcript:
            await self.on_transcript("assistant", text)
