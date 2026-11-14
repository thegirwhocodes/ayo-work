"""Sabi Safe — FastAPI Application Entrypoint.

Provides:
- Inbound Twilio voice webhook (/voice/incoming) returning TwiML Media Streams.
- Full-duplex Twilio WebSocket stream (/ws/twilio) connected to Gemini Live.
- Background Asterisk AudioSocket server (TCP port 9020) for GSM telephony.
- Health and status endpoints.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl
from xml.sax.saxutils import quoteattr

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.voice.twilio_stream import TwilioMediaBridge
from app.voice.audiosocket import start_audiosocket_server
from harness.caller_identity import sign_caller

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sabi.main")

# Load system prompt
PROMPT_PATH = Path(__file__).parent / "prompts" / "sabi_safe_prompt.md"
if PROMPT_PATH.exists():
    SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
else:
    SYSTEM_PROMPT = "You are Sabi Safe, a warm, calm, trauma-informed AI voice companion for youth."

AUDIOSOCKET_PORT = int(os.getenv("AUDIOSOCKET_PORT", "9020"))
ENABLE_AUDIOSOCKET = os.getenv("ENABLE_AUDIOSOCKET", "true").lower() in ("true", "1", "yes")

audiosocket_server: asyncio.Server | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global audiosocket_server
    logger.info("Initializing Sabi Safe services...")

    # Start Asterisk AudioSocket server in background if enabled
    if ENABLE_AUDIOSOCKET:
        try:
            audiosocket_server = await start_audiosocket_server(
                host="0.0.0.0",
                port=AUDIOSOCKET_PORT,
                system_prompt=SYSTEM_PROMPT,
            )
            logger.info(f"Asterisk AudioSocket listening on port {AUDIOSOCKET_PORT}")
        except Exception as e:
            logger.warning(f"Could not bind AudioSocket on port {AUDIOSOCKET_PORT}: {e}")

    yield

    logger.info("Shutting down Sabi Safe services...")
    if audiosocket_server:
        audiosocket_server.close()
        await audiosocket_server.wait_closed()
        logger.info("AudioSocket server closed.")


app = FastAPI(
    title="Sabi Safe Voice Telephony Stack",
    description="Trauma-informed, full-duplex voice helpline stack powered by Gemini Live",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "sabi-safe",
        "version": "1.0.0",
        "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
        "audiosocket_active": audiosocket_server is not None,
    }


@app.get("/")
async def root():
    return {
        "app": "Sabi Safe",
        "description": "Trauma-informed voice helpline telephony stack",
        "endpoints": {
            "health": "/health",
            "twilio_webhook": "/voice/incoming",
            "twilio_stream": "/ws/twilio",
        },
    }


@app.post("/voice/incoming")
async def voice_incoming(request: Request):
    """Twilio Inbound Voice Webhook.

    Returns TwiML that connects the inbound phone call to our WebSocket Media Stream.
    """
    host = request.headers.get("host", "localhost:8000")
    protocol = "wss" if "https" in str(request.base_url) else "ws"
    form = dict(parse_qsl((await request.body()).decode("utf-8")))
    caller = form.get("From", "")
    secret = os.getenv("TWILIO_AUTH_TOKEN", "")
    parameter = ""
    if caller and secret:
        from twilio.request_validator import RequestValidator

        if not RequestValidator(secret).validate(str(request.url), form, request.headers.get("X-Twilio-Signature", "")):
            return Response(status_code=403)
        try:
            token = sign_caller(caller, secret)
        except ValueError:
            return Response(status_code=400)
        parameter = f'<Parameter name="caller_token" value={quoteattr(token)} />'

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>We use your voice to recognize you on future calls. Please speak for a few seconds.</Say>
    <Connect>
        <Stream url="{protocol}://{host}/ws/twilio">{parameter}</Stream>
    </Connect>
</Response>"""

    return Response(content=twiml_response, media_type="application/xml")


@app.websocket("/ws/twilio")
async def ws_twilio(websocket: WebSocket):
    """Twilio Media Streams full-duplex WebSocket endpoint."""
    await websocket.accept()
    bridge = TwilioMediaBridge(
        websocket=websocket,
        system_prompt=SYSTEM_PROMPT,
        voice_name=os.getenv("GEMINI_VOICE", "Puck"),
    )
    await bridge.run()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
