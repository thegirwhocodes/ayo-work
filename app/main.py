"""Ayo — FastAPI Application Entrypoint (built on the Sabi Safe telephony stack).

Provides:
- Inbound Twilio voice webhook (/voice/incoming) returning TwiML Media Streams.
- Full-duplex Twilio WebSocket stream (/ws/twilio) connected to Gemini Live.
- Background Asterisk AudioSocket server (TCP port 9020) for GSM telephony.
- Health and status endpoints.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from xml.sax.saxutils import quoteattr

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.voice.caller_location import from_twilio_webhook
from app.voice.twilio_stream import TwilioMediaBridge
from app.voice.audiosocket import start_audiosocket_server
from harness.caller_identity import sign_caller
from harness.emergency_jobs import execute_job, purge_expired_jobs
from settings import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ayo.main")

# Must run before the module-level os.getenv calls below, or .env is ignored.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # the real environment is authoritative in containers
    pass

# Load system prompt.
# Ayo's prompt ships in this repo (app/prompts/ayo_prompt.md). AYO_PROMPT_PATH can override it with
# another file (e.g. a local experiment). Fortune's original prompt stays as a last-resort fallback.
REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = Path(__file__).parent / "prompts" / "ayo_prompt.md"
LEGACY_PROMPT_PATH = Path(__file__).parent / "prompts" / "sabi_safe_prompt.md"


def _resolve(path: str) -> Path:
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


def load_system_prompt() -> str:
    private_path = os.getenv("AYO_PROMPT_PATH", "")
    if private_path:
        resolved = _resolve(private_path)
        if resolved.is_file():
            logger.info("Using the prompt from AYO_PROMPT_PATH")
            return resolved.read_text(encoding="utf-8")
        logger.warning("AYO_PROMPT_PATH is set but the file is missing; using the bundled Ayo prompt")
    for path in (PROMPT_PATH, LEGACY_PROMPT_PATH):
        if path.exists():
            return path.read_text(encoding="utf-8")
    return "You are Ayo, a calm, warm listening friend on a free phone line for children."


def load_greeting_audio() -> bytes:
    """Ayo's pre-recorded greeting (raw 8 kHz mu-law), played the moment the call connects."""
    path = os.getenv("AYO_GREETING_AUDIO", "")
    if not path:
        return b""
    resolved = _resolve(path)
    if not resolved.is_file():
        logger.warning("AYO_GREETING_AUDIO is set but the file is missing; using the spoken prompt")
        return b""
    return resolved.read_bytes()


SYSTEM_PROMPT = load_system_prompt()
GREETING_AUDIO = load_greeting_audio()

AUDIOSOCKET_PORT = int(os.getenv("AUDIOSOCKET_PORT", "9020"))
ENABLE_AUDIOSOCKET = os.getenv("ENABLE_AUDIOSOCKET", "true").lower() in ("true", "1", "yes")

audiosocket_server: asyncio.Server | None = None


async def maintain_emergency_jobs():
    while True:
        try:
            await purge_expired_jobs()
        except Exception as exc:
            logger.warning("Emergency job cleanup failed: %s", type(exc).__name__)
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global audiosocket_server
    logger.info("Initializing Ayo services...")
    cleanup_task = None
    if get_settings().emergency_job_encryption_key:
        cleanup_task = asyncio.create_task(maintain_emergency_jobs())

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

    logger.info("Shutting down Ayo services...")
    if cleanup_task:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
    if audiosocket_server:
        audiosocket_server.close()
        await audiosocket_server.wait_closed()
        logger.info("AudioSocket server closed.")


app = FastAPI(
    title="Ayo Voice Telephony Stack",
    description="Ayo, a listening friend for children on the phone: full-duplex voice stack "
                "(OpenAI Realtime + ElevenLabs, or Gemini Live)",
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


@app.post("/internal/emergency-job")
async def emergency_job(request: Request):
    """Consume a one-use job capability from an authenticated agent worker."""
    secret = get_settings().emergency_worker_secret
    supplied = request.headers.get("X-Emergency-Worker-Key", "")
    if len(secret) < 32 or not hmac.compare_digest(supplied, secret):
        return Response(status_code=403)
    body = await request.json()
    token = body.get("token") if isinstance(body, dict) else None
    if not isinstance(token, str) or len(token) != 43:
        return Response(status_code=400)
    try:
        return await execute_job(token)
    except Exception as exc:
        logger.warning("Emergency job store unavailable: %s", type(exc).__name__)
        return Response(status_code=503)


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
        "app": "Ayo",
        "description": "A listening friend for children, on the phone",
        "endpoints": {
            "health": "/health",
            "twilio_webhook": "/voice/incoming",
            "twilio_stream": "/ws/twilio",
        },
    }


async def _twilio_params(request: Request) -> dict[str, str]:
    """Collect Twilio's request parameters regardless of configured HTTP method.

    Twilio sends them as a urlencoded body for POST webhooks and as query string
    parameters for GET webhooks; query values are merged in first so an explicit
    body value wins.
    """
    params: dict[str, str] = dict(request.query_params)
    if request.method == "POST":
        try:
            params.update({k: str(v) for k, v in (await request.form()).items()})
        except Exception:  # malformed body must not drop the call
            logger.warning("Could not parse Twilio webhook form body", exc_info=True)
    return params


@app.api_route("/voice/incoming", methods=["GET", "POST"])
async def voice_incoming(request: Request):
    """Twilio Inbound Voice Webhook.

    Returns TwiML that connects the inbound phone call to our WebSocket Media
    Stream, carrying the caller's location across as `<Parameter>` elements.
    The media socket is a separate connection and never receives Twilio's geo
    parameters on its own.
    """
    host = request.headers.get("host", "localhost:8000")
    protocol = "wss" if "https" in str(request.base_url) else "ws"
    params = await _twilio_params(request)
    caller = params.get("From", "")
    secret = os.getenv("TWILIO_AUTH_TOKEN", "")
    parameter = ""
    if caller and secret:
        from twilio.request_validator import RequestValidator

        if not RequestValidator(secret).validate(str(request.url), params if request.method == "POST" else {}, request.headers.get("X-Twilio-Signature", "")):
            return Response(status_code=403)
        try:
            token = sign_caller(caller, secret)
        except ValueError:
            return Response(status_code=400)
        parameter = f'<Parameter name="caller_token" value={quoteattr(token)} />'

    location = from_twilio_webhook(params)
    logger.info(
        "Inbound call from %s | location=%s country=%s source=%s call_sid=%s",
        location.number or "unknown",
        location.describe(),
        location.country or "-",
        location.source,
        params.get("CallSid", "-"),
    )

    stream_params = "".join(
        f'\n            <Parameter name={quoteattr(name)} value={quoteattr(value)} />'
        for name, value in sorted(location.to_custom_parameters().items())
    )

    # With Ayo's recorded greeting configured, the bridge plays it in Bukola's voice as soon as
    # the stream starts, so Twilio's robotic <Say> is left out.
    say = "" if GREETING_AUDIO else (
        "\n    <Say>We use your voice to recognize you on future calls. Please speak for a few seconds.</Say>")
    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>{say}
    <Connect>
        <Stream url="{protocol}://{host}/ws/twilio">{parameter}{stream_params}
        </Stream>
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
        greeting_audio=GREETING_AUDIO,
    )
    await bridge.run()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
