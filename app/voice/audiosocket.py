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
        # AudioSocket carries a call UUID, not a persistent phone number. Until
        # the PBX supplies a trusted caller identity, verification must fail.
        logger.warning("Closing AudioSocket call: caller identity is unavailable for voice verification")
        self.writer.write(HEADER_STRUCT.pack(TYPE_HANGUP, 0))
        await self.writer.drain()
        self.writer.close()
        await self.writer.wait_closed()
        return

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
        if self.on_transcript:
            await self.on_transcript("caller", text)

    async def _on_model_transcript(self, text: str):
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
