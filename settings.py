"""Cached application settings loaded from the environment and optional .env."""

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = field(repr=False)
    gemini_voice: str
    gemini_live_model: str
    gemini_live_voice: str
    harness_model: str
    port: int
    audiosocket_port: int
    enable_audiosocket: bool
    twilio_account_sid: str
    twilio_auth_token: str = field(repr=False)
    twilio_phone_number: str
    database_url: str = field(repr=False)
    voice_verification_threshold: float
    photon_api_key: str = field(repr=False)
    photon_project_id: str
    photon_sender_id: str
    emergency_responder_number: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Read settings once per process; environment values override .env."""
    values = dotenv_values(".env")

    def value(name: str, default: str = "") -> str:
        if name in os.environ:
            return os.environ[name]
        return values[name] if values.get(name) is not None else default

    return Settings(
        gemini_api_key=value("GEMINI_API_KEY"),
        gemini_voice=value("GEMINI_VOICE", "Puck"),
        gemini_live_model=value("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview"),
        gemini_live_voice=value("GEMINI_LIVE_VOICE", "Kore"),
        harness_model=value("HARNESS_MODEL", "gemini-2.5-flash"),
        port=int(value("PORT", "8000")),
        audiosocket_port=int(value("AUDIOSOCKET_PORT", "9020")),
        enable_audiosocket=value("ENABLE_AUDIOSOCKET", "true").lower() in ("true", "1", "yes"),
        twilio_account_sid=value("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=value("TWILIO_AUTH_TOKEN"),
        twilio_phone_number=value("TWILIO_PHONE_NUMBER"),
        database_url=value("DATABASE_URL"),
        voice_verification_threshold=float(value("VOICE_VERIFICATION_THRESHOLD", "0.75")),
        photon_api_key=value("PHOTON_API_KEY"),
        photon_project_id=value("PHOTON_PROJECT_ID"),
        photon_sender_id=value("PHOTON_SENDER_ID"),
        emergency_responder_number=value("EMERGENCY_RESPONDER_NUMBER"),
    )
