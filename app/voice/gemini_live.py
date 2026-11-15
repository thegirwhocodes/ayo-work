"""Gemini Live (BidiGenerateContent) WebSocket client.

One WebSocket stays open for the whole phone call. Caller audio streams in
continuously; Gemini keeps the conversation context, detects when the caller
starts/stops talking, and streams spoken audio back. If the caller talks over
the model, Gemini sends `interrupted` and we must drop any queued audio.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("sabi.gemini_live")

LIVE_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
DEFAULT_MODEL = "gemini-3.1-flash-live-preview"


def _qualified_model(model: str) -> str:
    """Live expects a fully qualified name; accept it with or without the prefix."""
    model = model.strip()
    return model if model.startswith("models/") else f"models/{model}"


class LiveSetupError(RuntimeError):
    """Raised when the Live session cannot be opened or acknowledged."""


@dataclass
class LiveConfig:
    system_prompt: str
    model: str = field(default_factory=lambda: os.getenv("GEMINI_LIVE_MODEL", DEFAULT_MODEL))
    voice: Optional[str] = field(default_factory=lambda: os.getenv("GEMINI_LIVE_VOICE", "Kore") or None)
    input_rate: int = 8000
    # Phone lines are noisy: low sensitivity avoids treating breaths/clicks as speech.
    start_sensitivity: str = "START_SENSITIVITY_LOW"
    end_sensitivity: str = "END_SENSITIVITY_LOW"
    silence_ms: int = 700
    prefix_padding_ms: int = 160
    tools: list[dict[str, Any]] = field(default_factory=list)


def build_setup_message(cfg: LiveConfig) -> dict[str, Any]:
    """Build the first message of a Live session.

    Protocol note: `responseModalities` and `speechConfig` live inside
    `generationConfig`. Putting them directly under `setup` makes the server
    close the socket with error 1007.
    """
    generation: dict[str, Any] = {
        "responseModalities": ["AUDIO"],
        "thinkingConfig": {"thinkingLevel": "minimal"},
    }
    if cfg.voice and cfg.voice.lower() not in {"auto", "none"}:
        generation["speechConfig"] = {
            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": cfg.voice}}
        }

    setup: dict[str, Any] = {
        "model": _qualified_model(cfg.model),
        "generationConfig": generation,
        "systemInstruction": {"parts": [{"text": cfg.system_prompt}]},
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "realtimeInputConfig": {
            "automaticActivityDetection": {
                "disabled": False,
                "startOfSpeechSensitivity": cfg.start_sensitivity,
                "endOfSpeechSensitivity": cfg.end_sensitivity,
                "prefixPaddingMs": cfg.prefix_padding_ms,
                "silenceDurationMs": cfg.silence_ms,
            },
            "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
            "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY",
        },
        # Long calls would otherwise hit the context limit.
        "contextWindowCompression": {
            "triggerTokens": 12000,
            "slidingWindow": {"targetTokens": 8000},
        },
    }
    if cfg.tools:
        setup["tools"] = [{"functionDeclarations": cfg.tools}]
    return {"setup": setup}


AudioHandler = Callable[[bytes], Awaitable[None]]
TextHandler = Callable[[str], Awaitable[None]]
EventHandler = Callable[[], Awaitable[None]]
ToolHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class GeminiLiveSession:
    """Thin async wrapper around one Live WebSocket."""

    def __init__(
        self,
        cfg: LiveConfig,
        *,
        on_audio: AudioHandler,
        on_interrupted: EventHandler,
        on_turn_complete: Callable[[str, str], Awaitable[None]],
        on_tool_call: Optional[ToolHandler] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.cfg = cfg
        self.on_audio = on_audio
        self.on_interrupted = on_interrupted
        self.on_turn_complete = on_turn_complete
        self.on_tool_call = on_tool_call
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self._ws = None
        self._send_lock = asyncio.Lock()
        self._caller_text = ""
        self._model_text = ""

    async def connect(self, timeout: float = 12.0) -> None:
        if not self.api_key:
            raise LiveSetupError("GEMINI_API_KEY is not set")
        import websockets

        try:
            self._ws = await websockets.connect(
                f"{LIVE_URL}?key={self.api_key}",
                open_timeout=timeout,
                ping_interval=20,
                ping_timeout=20,
                max_size=8 * 1024 * 1024,
            )
            await self._send(build_setup_message(self.cfg))
            first = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=timeout))
        except Exception as exc:
            await self.close()
            # Never log the URL: it contains the API key.
            raise LiveSetupError(f"Live connect failed ({type(exc).__name__})") from None
        if "setupComplete" not in first:
            await self.close()
            raise LiveSetupError(f"Live setup not acknowledged: {sorted(first)}")

    async def _send(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self._ws.send(json.dumps(payload))

    async def send_audio(self, pcm: bytes) -> None:
        if not pcm or self._ws is None:
            return
        await self._send(
            {
                "realtimeInput": {
                    "audio": {
                        "data": base64.b64encode(pcm).decode("ascii"),
                        "mimeType": f"audio/pcm;rate={self.cfg.input_rate}",
                    }
                }
            }
        )

    async def send_text(self, text: str, *, turn_complete: bool = True) -> None:
        """Inject a text turn (used to make the model speak the opening line)."""
        await self._send(
            {
                "clientContent": {
                    "turns": [{"role": "user", "parts": [{"text": text}]}],
                    "turnComplete": turn_complete,
                }
            }
        )

    async def end_audio(self) -> None:
        if self._ws is not None:
            try:
                await self._send({"realtimeInput": {"audioStreamEnd": True}})
            except Exception:
                pass

    async def receive_forever(self) -> None:
        async for raw in self._ws:
            msg = json.loads(raw)

            if msg.get("toolCall") and self.on_tool_call:
                await self._answer_tools(msg["toolCall"])

            content = msg.get("serverContent") or {}
            if os.getenv("DEBUG_LOG_LIVE_EVENTS"):
                flags = sorted(k for k in content if k not in ("modelTurn", "inputTranscription", "outputTranscription"))
                if flags or content.get("inputTranscription") or content.get("outputTranscription"):
                    logger.info("LIVE_EVT flags=%s in=%s out=%s", flags, bool(content.get("inputTranscription")),
                                bool(content.get("outputTranscription")))
            if content.get("interrupted"):
                await self.on_interrupted()

            heard = (content.get("inputTranscription") or {}).get("text")
            if heard:
                self._caller_text += heard
            said = (content.get("outputTranscription") or {}).get("text")
            if said:
                self._model_text += said

            for part in (content.get("modelTurn") or {}).get("parts") or []:
                data = (part.get("inlineData") or {}).get("data")
                if data:
                    await self.on_audio(base64.b64decode(data))

            # gemini-3.1 live sends `generationComplete` when the model finishes speaking but holds
            # `turnComplete` until the caller's next activity, so flush the turn on either signal.
            if content.get("generationComplete") or content.get("turnComplete"):
                caller, model = self._caller_text.strip(), self._model_text.strip()
                self._caller_text, self._model_text = "", ""
                if caller or model:
                    await self.on_turn_complete(caller, model)

            if msg.get("goAway"):
                logger.warning("Gemini Live goAway: %s", msg["goAway"])

    async def _answer_tools(self, tool_call: dict[str, Any]) -> None:
        responses = []
        for call in tool_call.get("functionCalls") or []:
            try:
                result = await self.on_tool_call(call.get("name", ""), call.get("args") or {})
            except Exception as exc:  # tool failure must not kill the call
                result = {"error": type(exc).__name__}
            responses.append({"id": call.get("id"), "name": call.get("name"), "response": result})
        await self._send({"toolResponse": {"functionResponses": responses}})

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

