"""Ayo's voice engine: OpenAI Realtime (ears + brain, text-only) + ElevenLabs (voice).

A drop-in replacement for `GeminiLiveSession`: same constructor, same methods, same callbacks,
so the Twilio bridge does not change. Choose it with VOICE_ENGINE=openai.

  caller audio (PCM16 8 kHz) -> mu-law -> OpenAI Realtime (`gpt-realtime-2.1`, output text only)
      OpenAI hears the audio itself (crying, whispering, tone) and decides when the caller finished.
  reply text -> split into sentences -> ElevenLabs (Bukola, eleven_v4_turbo, pcm_24000)
      -> on_audio(PCM16 24 kHz): exactly what Gemini produced, so the bridge's resampler is unchanged.

Behaviour kept identical to GeminiLiveSession for the bridge:
  * on_interrupted() when the caller talks over Ayo (the bridge clears Twilio's buffer);
  * on_turn_complete(caller_text, model_text) once per reply. The consent logic in the bridge
    matches these texts, so the caller transcript (which OpenAI delivers separately and
    sometimes late) is paired with the right reply before the callback fires;
  * on_tool_call(name, args) -> dict, answered back to the model;
  * send_text(text) injects a trusted system message and asks for a reply.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from app.voice.audio import pcm16_to_mulaw
from app.voice.gemini_live import LiveConfig, LiveSetupError
from harness.tools.openai_format import to_openai_tools
from settings import get_settings

logger = logging.getLogger("ayo.openai_realtime")

REALTIME_URL = "wss://api.openai.com/v1/realtime"
TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
# Tuned on Naomi's recorded clips (Nov 14 2026): 1.2 s of quiet ends a turn, so a crying child's
# short pauses don't cut them off; threshold 0.4 hears quiet voices. Replies came 0.7-1.3 s later.
TURN_DETECTION = {"type": "server_vad", "threshold": 0.4, "prefix_padding_ms": 300,
                  "silence_duration_ms": 1200, "create_response": True, "interrupt_response": True}
TRANSCRIPT_WAIT_S = 1.5
PCM24K_BYTES_PER_SECOND = 24000 * 2
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?output_format=pcm_24000"

VOICE_STYLE_NOTE = (
    "\n\nVOICE STYLE: Your words are read aloud by a separate voice. When the caller sounds upset, "
    "scared or tearful, you may begin a sentence with [softly]. If they are whispering, begin with "
    "[whispers]. These two tags are the only exception to any rule against brackets or stage "
    "directions: the voice performs them and they are never heard as words. Use no other bracketed "
    "tags and never say the tag words themselves."
)
# After Ayo finishes speaking, this much caller silence earns one gentle check-in (Ayo prototype).
SILENCE_CHECKIN_S = 8.0
SILENCE_NOTE = ("[LISTENER: long silence. Gently say it's okay if it's hard to talk and that you're "
                "still here. One short sentence.]")
_TAG_RE = re.compile(r"\[(?:softly|whispers)\]\s*", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(.+?[.!?…])(\s+)", re.DOTALL)

AudioHandler = Callable[[bytes], Awaitable[None]]
EventHandler = Callable[[], Awaitable[None]]
TurnHandler = Callable[[str, str], Awaitable[None]]
ToolHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
TtsFunction = Callable[[str], AsyncIterator[bytes]]


def strip_voice_tags(text: str) -> str:
    """Remove [softly]/[whispers] so transcripts (and the bridge's consent matching) see plain words."""
    return " ".join(_TAG_RE.sub("", text or "").split())


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Return the complete sentences at the start of `buffer` and the unfinished remainder."""
    sentences = []
    while True:
        match = _SENTENCE_RE.match(buffer)
        if not match:
            break
        sentence = match.group(1).strip()
        if sentence:
            sentences.append(sentence)
        buffer = buffer[match.end():]
    return sentences, buffer


def build_session_update(cfg: LiveConfig) -> dict[str, Any]:
    session: dict[str, Any] = {
        "type": "realtime",
        "output_modalities": ["text"],
        "instructions": cfg.system_prompt + VOICE_STYLE_NOTE,
        "audio": {"input": {"format": {"type": "audio/pcmu"},
                            "transcription": {"model": TRANSCRIBE_MODEL},
                            "turn_detection": dict(TURN_DETECTION)}},
    }
    if cfg.tools:
        session["tools"] = to_openai_tools(cfg.tools)
        session["tool_choice"] = "auto"
    return {"type": "session.update", "session": session}


class OpenAIRealtimeSession:
    """Same public interface as GeminiLiveSession."""

    def __init__(
        self,
        cfg: LiveConfig,
        *,
        on_audio: AudioHandler,
        on_interrupted: EventHandler,
        on_turn_complete: TurnHandler,
        on_tool_call: Optional[ToolHandler] = None,
        api_key: Optional[str] = None,
        tts: Optional[TtsFunction] = None,
        connector: Optional[Callable[..., Awaitable[Any]]] = None,
    ) -> None:
        settings = get_settings()
        self.cfg = cfg
        self.on_audio = on_audio
        self.on_interrupted = on_interrupted
        self.on_turn_complete = on_turn_complete
        self.on_tool_call = on_tool_call
        self.api_key = api_key or settings.openai_api_key
        self.model = settings.openai_realtime_model
        self._eleven_key = settings.elevenlabs_api_key
        self._eleven_voice = settings.elevenlabs_voice_id
        self._eleven_model = settings.elevenlabs_model
        self._tts = tts
        self._connector = connector
        self._ws = None
        self._http = None
        self._send_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        # reply text -> sentences -> voice
        self._text_buf = ""
        self._model_text = ""
        self._sentences: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._generation = 0          # bumped on barge-in; older audio is dropped
        self._muted = False           # rest of an interrupted reply is not spoken
        self._tts_busy = False
        self._playback_until = 0.0
        self._carry = b""             # keep PCM16 chunks aligned to whole samples
        # pairing caller transcripts with replies
        self._turns: asyncio.Queue[tuple[Optional[str], str]] = asyncio.Queue()
        self._transcripts: dict[str, str] = {}
        self._transcript_events: dict[str, asyncio.Event] = {}
        self._last_committed: Optional[str] = None
        self._response_item: Optional[str] = None
        self._response_active = False
        self._response_wanted = False
        self._pending_calls: list[dict[str, Any]] = []
        # silence check-in
        self._ayo_has_spoken = False
        self._caller_speaking = False
        self._last_caller_activity = time.monotonic()
        self._checked_in = False

    # ---------------- connection ----------------
    async def connect(self, timeout: float = 12.0) -> None:
        if not self.api_key:
            raise LiveSetupError("OPENAI_API_KEY is not set")
        if self._tts is None and not (self._eleven_key and self._eleven_voice):
            raise LiveSetupError("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID are not set")
        connector = self._connector
        if connector is None:
            import websockets
            connector = websockets.connect
        loop = asyncio.get_running_loop()
        try:
            self._ws = await connector(
                f"{REALTIME_URL}?model={self.model}",
                additional_headers={"Authorization": f"Bearer {self.api_key}"},
                open_timeout=timeout, ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024,
            )
            await self._send(build_session_update(self.cfg))
            deadline = loop.time() + timeout
            while True:
                message = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=max(0.1, deadline - loop.time())))
                if message.get("type") == "session.updated":
                    break
                if message.get("type") == "error":
                    raise LiveSetupError(f"Realtime setup rejected: {(message.get('error') or {}).get('message')}")
        except LiveSetupError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise LiveSetupError(f"Realtime connect failed ({type(exc).__name__})") from None
        self._start_workers()

    def _start_workers(self) -> None:
        self._tasks = [asyncio.create_task(self._tts_worker()), asyncio.create_task(self._turn_worker()),
                       asyncio.create_task(self._silence_worker())]

    async def _send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self._ws.send(json.dumps(payload))

    # ---------------- what the bridge calls ----------------
    async def send_audio(self, pcm: bytes) -> None:
        if not pcm or self._ws is None:
            return
        if self.cfg.input_rate != 8000:
            raise ValueError("OpenAIRealtimeSession expects 8 kHz telephone audio")
        await self._send({"type": "input_audio_buffer.append",
                          "audio": base64.b64encode(pcm16_to_mulaw(pcm)).decode("ascii")})

    async def send_text(self, text: str, *, turn_complete: bool = True) -> None:
        """Inject a trusted application message (e.g. an emergency action result)."""
        await self._send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "system", "content": [{"type": "input_text", "text": text}]}})
        if turn_complete:
            await self._request_response()

    async def end_audio(self) -> None:
        """Nothing to flush: server-side turn detection ends each caller turn."""

    async def receive_forever(self) -> None:
        async for raw in self._ws:
            try:
                await self._handle(json.loads(raw))
            except Exception:
                logger.exception("Realtime event handling failed")

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None

    # ---------------- events from OpenAI ----------------
    async def _handle(self, event: dict[str, Any]) -> None:
        kind = event.get("type", "")
        if kind == "input_audio_buffer.speech_started":
            self._caller_speaking, self._checked_in = True, False
            self._last_caller_activity = time.monotonic()
            if self._is_speaking():
                await self._interrupt()
        elif kind == "input_audio_buffer.speech_stopped":
            self._caller_speaking = False
            self._last_caller_activity = time.monotonic()
        elif kind == "input_audio_buffer.committed":
            item_id = event.get("item_id")
            if item_id:
                self._last_committed = item_id
                self._transcript_events.setdefault(item_id, asyncio.Event())
        elif kind in ("conversation.item.input_audio_transcription.completed",
                      "conversation.item.input_audio_transcription.failed"):
            item_id = event.get("item_id")
            if item_id:
                self._transcripts[item_id] = event.get("transcript") or ""
                self._transcript_events.setdefault(item_id, asyncio.Event()).set()
        elif kind == "response.created":
            self._response_active = True
            self._muted = False
            self._response_item, self._last_committed = self._last_committed, None
            self._model_text, self._text_buf = "", ""
        elif kind in ("response.output_text.delta", "response.text.delta"):
            if not self._muted:
                delta = event.get("delta", "")
                self._model_text += delta
                sentences, self._text_buf = split_sentences(self._text_buf + delta)
                for sentence in sentences:
                    self._sentences.put_nowait((self._generation, sentence))
        elif kind == "response.function_call_arguments.done":
            self._pending_calls.append({"call_id": event.get("call_id"), "name": event.get("name", ""),
                                        "arguments": event.get("arguments") or "{}"})
        elif kind == "response.done":
            await self._finish_response()
        elif kind == "error":
            message = (event.get("error") or {}).get("message", "")
            logger.warning("Realtime error: %s", message)

    async def _finish_response(self) -> None:
        if not self._muted and self._text_buf.strip():
            self._sentences.put_nowait((self._generation, self._text_buf.strip()))
        self._text_buf = ""
        self._turns.put_nowait((self._response_item, strip_voice_tags(self._model_text)))
        self._model_text, self._response_item = "", None
        self._response_active = False
        calls, self._pending_calls = self._pending_calls, []
        for call in calls:
            try:
                args = json.loads(call["arguments"]) if call["arguments"] else {}
            except ValueError:
                args = {}
            try:
                result = await self.on_tool_call(call["name"], args) if self.on_tool_call else {"status": "unavailable"}
            except Exception as exc:  # a tool failure must not kill the call
                result = {"error": type(exc).__name__}
            await self._send({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)}})
        if calls or self._response_wanted:
            self._response_wanted = False
            await self._request_response()

    async def _request_response(self) -> None:
        if self._response_active:
            self._response_wanted = True  # OpenAI allows one active reply; ask again when this one ends
            return
        self._response_active = True
        await self._send({"type": "response.create"})

    # ---------------- barge-in ----------------
    def _is_speaking(self) -> bool:
        return self._tts_busy or not self._sentences.empty() or time.monotonic() < self._playback_until

    async def _interrupt(self) -> None:
        self._generation += 1
        self._muted = self._response_active
        while not self._sentences.empty():
            self._sentences.get_nowait()
        self._text_buf, self._carry, self._playback_until = "", b"", 0.0
        await self.on_interrupted()

    # ---------------- voice ----------------
    async def _synthesize(self, sentence: str) -> AsyncIterator[bytes]:
        if self._tts is not None:
            async for chunk in self._tts(sentence):
                yield chunk
            return
        import httpx
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
        async with self._http.stream("POST", TTS_URL.format(voice_id=self._eleven_voice),
                                     headers={"xi-api-key": self._eleven_key},
                                     json={"text": sentence, "model_id": self._eleven_model}) as response:
            if response.status_code != 200:
                logger.warning("ElevenLabs TTS HTTP %s", response.status_code)
                return
            async for chunk in response.aiter_bytes():
                yield chunk

    async def _tts_worker(self) -> None:
        while True:
            generation, sentence = await self._sentences.get()
            if generation != self._generation:
                continue
            self._tts_busy = True
            self._carry = b""  # each sentence is its own audio stream: never carry bytes across
            try:
                async for chunk in self._synthesize(sentence):
                    if generation != self._generation:
                        break  # the caller interrupted
                    data = self._carry + chunk
                    whole = len(data) - len(data) % 2
                    self._carry = data[whole:]
                    if whole:
                        self._ayo_has_spoken = True
                        now = time.monotonic()
                        self._playback_until = max(now, self._playback_until) + whole / PCM24K_BYTES_PER_SECOND
                        await self.on_audio(data[:whole])
            except Exception as exc:
                logger.warning("TTS failed: %s", type(exc).__name__)
            finally:
                self._tts_busy = False

    # ---------------- silence check-in ----------------
    def _silence_due(self, now: float) -> bool:
        """One check-in per silence: only after Ayo has spoken and finished, never mid-reply."""
        return (self._ayo_has_spoken and not self._checked_in and not self._caller_speaking
                and not self._response_active and not self._is_speaking()
                and now - max(self._last_caller_activity, self._playback_until) >= SILENCE_CHECKIN_S)

    async def _silence_worker(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            if self._ws is not None and self._silence_due(time.monotonic()):
                self._checked_in = True
                try:
                    await self.send_text(SILENCE_NOTE)
                except Exception as exc:
                    logger.warning("Silence check-in failed: %s", type(exc).__name__)

    # ---------------- pairing transcripts with replies ----------------
    async def _turn_worker(self) -> None:
        while True:
            item_id, model_text = await self._turns.get()
            caller_text = ""
            if item_id:
                event = self._transcript_events.setdefault(item_id, asyncio.Event())
                try:
                    await asyncio.wait_for(event.wait(), TRANSCRIPT_WAIT_S)
                except asyncio.TimeoutError:
                    pass
                caller_text = self._transcripts.pop(item_id, "").strip()
                self._transcript_events.pop(item_id, None)
            try:
                await self.on_turn_complete(caller_text, model_text)
            except Exception:
                logger.exception("on_turn_complete failed")
