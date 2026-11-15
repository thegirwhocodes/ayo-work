"""Live case dashboard: what the agents are doing on each call, without what anyone said.

    GET /dashboard            the page (static; it holds no data)
    GET /dashboard/snapshot   JSON built from the content-free tool events, needs ``Authorization: Bearer <DASHBOARD_TOKEN>``

Off unless ``DASHBOARD_TOKEN`` is set to a random secret of at least 24 characters; until then both routes answer
404. The snapshot is built by ``harness/dashboard.py`` from a fixed set of fields, so it never carries transcripts,
names or phone numbers. Events live in this process's memory, so run one worker, or read one process's view.
"""

from __future__ import annotations

import hmac
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from harness.dashboard import build_snapshot
from harness.tool_log import recent_events
from settings import get_settings

router = APIRouter()
PAGE = Path(__file__).with_name("static") / "dashboard.html"
MIN_TOKEN_LENGTH = 24
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}
# The page is one file with inline script and style; it may only call back to this origin.
CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'"


def _token() -> str:
    token = get_settings().dashboard_token
    return token if len(token) >= MIN_TOKEN_LENGTH else ""


@router.get("/dashboard", include_in_schema=False)
async def dashboard_page():
    if not _token():
        return Response(status_code=404)
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={**NO_STORE, "Content-Security-Policy": CSP})


@router.get("/dashboard/snapshot", include_in_schema=False)
async def dashboard_snapshot(request: Request):
    token = _token()
    if not token:
        return Response(status_code=404)
    if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
        return Response(status_code=401, headers=NO_STORE)
    return JSONResponse(build_snapshot(recent_events()), headers=NO_STORE)
