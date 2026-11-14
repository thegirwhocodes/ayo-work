"""Asterisk AudioSocket server implementation.

AudioSocket is Asterisk's low-latency TCP protocol for bidirectional audio streaming.
Specification:
- Header: 3 bytes
  - Byte 0: Message Type
    - 0x01: Audio (8kHz signed 16-bit linear PCM mono)
    - 0x10: UUID (16-byte call channel UUID)
    - 0x00: Hangup
    - 0x02: Silence
    - 0xFF: Error
  - Bytes 1-2: Payload length (uint16 big-endian)
- Payload: Raw binary data of length indicated in header.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import uuid
from typing import Optional, Callable, Awaitable

from app.voice.audio import Downsample24kTo8k, FrameChunker
from app.voice.gemini_live import GeminiLiveSession

logger = logging.getLogger("sabi.voice.audiosocket")

TYPE_HANGUP = 0x00
TYPE_AUDIO = 0x01
TYPE_SILENCE = 0x02
TYPE_UUID = 0x10
TYPE_ERROR = 0xFF

HEADER_STRUCT = struct.Struct("!BH")  # 1 byte type, 2 bytes uint16 length (3 bytes total)


class AudioSocketConnection:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        system_prompt: str,
        voice_name: str = "Puck",
        on_transcript: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ):
        self.reader = reader
        self.writer = writer
        self.system_prompt = system_prompt
        self.voice_name = voice_name
        self.on_transcript = on_transcript

        self.call_uuid: Optional[uuid.UUID] = None
        self.gemini_session: Optional[GeminiLiveSession] = None
        self.resampler = Downsample24kTo8k()
        self.chunker = FrameChunker(frame_bytes=320)  # 20ms of 8kHz PCM16 = 320 bytes
        self._running = False

    async def handle(self):
        self._running = True
        peername = self.writer.get_extra_info("peername")
        logger.info(f"New AudioSocket connection from {peername}")

        self.gemini_session = GeminiLiveSession(
            system_instruction=self.system_prompt,
            voice_name=self.voice_name,
            input_sample_rate=8000,
            output_sample_rate=24000,
            on_audio_out=self._on_gemini_audio,
            on_user_transcript=self._on_user_transcript,
            on_model_transcript=self._on_model_transcript,
            on_interrupted=self._on_interrupted,
        )

        try:
            await self.gemini_session.connect()
        except Exception as e:
            logger.error(f"Failed to connect to Gemini Live: {e}")
            self.writer.close()
            await self.writer.wait_closed()
            return

        gemini_loop_task = asyncio.create_task(self.gemini_session.receive_loop())

        try:
            while self._running:
                header = await self.reader.readexactly(3)
                msg_type, length = HEADER_STRUCT.unpack(header)

                payload = b""
                if length > 0:
                    payload = await self.reader.readexactly(length)

                if msg_type == TYPE_AUDIO:
                    # Inbound 8kHz PCM16 from Asterisk -> feed to Gemini Live
                    if self.gemini_session:
                        await self.gemini_session.send_audio(payload)

                elif msg_type == TYPE_UUID:
                    if len(payload) == 16:
                        self.call_uuid = uuid.UUID(bytes=payload)
                        logger.info(f"AudioSocket Call UUID: {self.call_uuid}")

                elif msg_type == TYPE_HANGUP:
                    logger.info(f"AudioSocket hangup received for {self.call_uuid}")
                    break

                elif msg_type == TYPE_ERROR:
                    logger.warning(f"AudioSocket error received: {payload}")
                    break

        except (asyncio.IncompleteReadError, ConnectionResetError):
            logger.info("AudioSocket connection closed by remote Asterisk")
        except Exception as e:
            logger.error(f"Error handling AudioSocket: {e}", exc_info=True)
        finally:
            self._running = False
            if self.gemini_session:
                await self.gemini_session.close()
            gemini_loop_task.cancel()
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
            logger.info("AudioSocket connection terminated")

    async def _on_gemini_audio(self, pcm24k_chunk: bytes):
        """Downsample 24kHz PCM16 -> 8kHz PCM16 and stream to Asterisk."""
        pcm8k = self.resampler.resample(pcm24k_chunk)
        if not pcm8k:
            return

        frames = self.chunker.add_samples(pcm8k)
        for frame in frames:
            header = HEADER_STRUCT.pack(TYPE_AUDIO, len(frame))
            self.writer.write(header + frame)
            await self.writer.drain()

    async def _on_interrupted(self):
        """Barge-in / interruption from Gemini Live."""
        logger.info("AudioSocket barge-in detected")

    async def _on_user_transcript(self, text: str):
        logger.info(f"[CALLER]: {text}")
        if self.on_transcript:
            await self.on_transcript("caller", text)

    async def _on_model_transcript(self, text: str):
        logger.info(f"[SABI]: {text}")
        if self.on_transcript:
            await self.on_transcript("assistant", text)


async def start_audiosocket_server(
    host: str = "0.0.0.0",
    port: int = 9020,
    system_prompt: str = "",
    voice_name: str = "Puck",
) -> asyncio.Server:
    """Start the TCP AudioSocket server."""
    async def client_connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        conn = AudioSocketConnection(reader, writer, system_prompt, voice_name)
        asyncio.create_task(conn.handle())

    server = await asyncio.start_server(client_connected, host, port)
    logger.info(f"AudioSocket server listening on {host}:{port}")
    return server
