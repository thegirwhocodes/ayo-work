"""HTTP surface of the location lane.

Public (the child's phone has to reach them): GET /l/{token} (the one-tap page) and POST /api/l/{token}.
Private (responder console): /location and /api/location/*, locked behind LOCATION_CONSOLE_KEY. With no key set
the console is OFF (403), so a fresh deploy never exposes a child's location.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.location import device_feed, lane
from app.location.console_page import CONSOLE_PAGE
from app.location.link_page import EXPIRED_PAGE, LINK_PAGE

router = APIRouter()
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _require_console_key(request: Request) -> None:
    secret = os.getenv("LOCATION_CONSOLE_KEY", "")
    supplied = request.headers.get("X-Console-Key") or request.query_params.get("key", "")
    if len(secret) < 12 or not hmac.compare_digest(supplied.encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="forbidden")


@router.get("/l/{token}", response_class=HTMLResponse)
async def location_link_page(token: str):
    if not lane.token_case_id(token):
        return HTMLResponse(EXPIRED_PAGE, status_code=410, headers=NO_STORE)
    return HTMLResponse(LINK_PAGE, headers=NO_STORE)


class GpsPayload(BaseModel):
    latitude: float
    longitude: float
    accuracy_m: Optional[float] = None


@router.post("/api/l/{token}")
async def location_link_submit(token: str, body: GpsPayload):
    if not await lane.record_gps(token, body.latitude, body.longitude, body.accuracy_m):
        raise HTTPException(status_code=410, detail="Link expired or already used")
    return JSONResponse({"ok": True}, headers=NO_STORE)


@router.get("/location", response_class=HTMLResponse)
async def console(request: Request):
    _require_console_key(request)
    return HTMLResponse(CONSOLE_PAGE, headers=NO_STORE)


@router.get("/api/location/cases")
async def cases(request: Request):
    _require_console_key(request)
    return JSONResponse({"cases": [c.to_dict() for c in lane.list_cases()], "profiles": device_feed.list_profiles()},
                        headers=NO_STORE)


class LookupPayload(BaseModel):
    profile_id: Optional[str] = None


@router.post("/api/location/cases/{case_id}/device-lookup")
async def device_lookup(case_id: str, request: Request, body: LookupPayload):
    """Presenter control: ask the seeded device feed about a case (any profile) mid-demo."""
    _require_console_key(request)
    result = await lane.attach_device_location(case_id, body.profile_id)
    if not result.get("attached"):
        raise HTTPException(status_code=404, detail=result.get("reason", "not_found"))
    return JSONResponse(result, headers=NO_STORE)


@router.post("/api/location/cases/{case_id}/send-link")
async def resend_link(case_id: str, request: Request):
    _require_console_key(request)
    return JSONResponse(await lane.send_location_link(case_id), headers=NO_STORE)
