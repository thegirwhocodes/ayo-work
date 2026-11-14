"""Authenticate private Agent Protocol requests from the application."""

import hmac

from langgraph_sdk import Auth

from settings import get_settings

auth = Auth()


@auth.authenticate
async def authenticate(authorization: str | None = None) -> str:
    secret = get_settings().agent_server_auth_secret
    if len(secret) < 32 or not hmac.compare_digest(authorization or "", f"Bearer {secret}"):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid credentials")
    return "sabi-safe-app"
