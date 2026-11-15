"""Live location for an Ayo call: where is the child standing right now?

Signals, weakest to strongest:

1. network  - Twilio's From* fields / number prefix (`CallerLocation`). Where the number is registered.
2. spoken   - the place the child says. Ayo saves it with `record_caller_location`.
3. device   - the device-location feed (seeded for the demo, see device_feed.py; real access is a future goal).
4. gps      - one-tap link texted to the child's phone (Photon). The page reads `navigator.geolocation` and
              posts it back while the call is live.

Best order: gps > device > spoken > network. Whenever two or more sources exist they are cross-checked; if they
disagree the responder sees all of them and nothing is picked or averaged.

Evidence lives in memory only (no location data at rest) and is purged after LOCATION_RETENTION_HOURS (default
24). Twilio is never used to text the child. Location text is never logged, only that it was saved.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote_plus

from app.location import device_feed, geo
from app.voice.caller_location import CallerLocation

logger = logging.getLogger("ayo.location")

LINK_TTL_SECONDS = 30 * 60
MAX_ADDRESS_CHARS = 200

Injector = Callable[[str], Awaitable[None]]
Messenger = Callable[[str, str], Awaitable[dict]]  # (recipient, text) -> {"status": "submitted" | ...}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LocationCase:
    id: str
    created_at: str
    updated_at: str
    call_sid: Optional[str]
    caller_number: Optional[str]
    country: Optional[str]
    network: Optional[str]
    network_source: str = "unknown"
    live: bool = True
    spoken_address: Optional[str] = None
    spoken_at: Optional[str] = None
    spoken_point: Optional[dict] = None
    gps: Optional[dict] = None
    device_service: Optional[dict] = None
    link_sent_via: list[str] = field(default_factory=list)
    link_sent_at: Optional[str] = None
    cross_check: Optional[dict] = None
    nearest_help: list[dict] = field(default_factory=list)
    best: str = "none"

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        data["summary"] = describe_best(self)
        return data


@dataclass
class _Live:
    case_id: str
    caller_number: Optional[str]
    public_base_url: Optional[str]
    inject: Optional[Injector] = None
    messenger: Optional[Messenger] = None
    auto_link: Optional["asyncio.Task[dict]"] = None
    feed_task: Optional["asyncio.Task[dict]"] = None


@dataclass
class _Token:
    case_id: str
    expires_at: float
    used: bool = False


_cases: dict[str, LocationCase] = {}
_live: dict[str, _Live] = {}
_tokens: dict[str, _Token] = {}


# --------------------------------------------------------------------------- #
# Descriptions and map links
# --------------------------------------------------------------------------- #
def gps_maps_url(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lng:.6f}"


def address_maps_url(address: str) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}"


def _best(case: LocationCase) -> str:
    if case.gps:
        return "gps"
    if case.device_service:
        return "device_service"
    if case.spoken_address:
        return "spoken"
    if case.network:
        return "network"
    return "none"


def describe_best(case: LocationCase) -> Optional[str]:
    """One line for a responder: the most precise signal, with a maps link."""
    if case.gps:
        acc = f" ±{round(case.gps['accuracy_m'])}m" if case.gps.get("accuracy_m") else ""
        line = f"GPS{acc}: {gps_maps_url(case.gps['latitude'], case.gps['longitude'])}"
    elif case.device_service:
        acc = f" ±{round(case.device_service['accuracy_m'])}m" if case.device_service.get("accuracy_m") else ""
        line = f"Device location{acc}: {gps_maps_url(case.device_service['latitude'], case.device_service['longitude'])}"
    elif case.spoken_address:
        line = f"Caller said: {case.spoken_address} ({address_maps_url(case.spoken_address)})"
    elif case.network:
        return f"Phone network area: {case.network} (registered area, not live)"
    else:
        return None
    if case.cross_check and case.cross_check["status"] == "disagree":
        line += f" | {case.cross_check['summary']}"
    return line


# --------------------------------------------------------------------------- #
# Case lifecycle
# --------------------------------------------------------------------------- #
def _retention_s() -> float:
    try:
        return max(1.0, float(os.getenv("LOCATION_RETENTION_HOURS", "24"))) * 3600
    except ValueError:
        return 24 * 3600


def purge_expired() -> None:
    for case_id in [c.id for c in _cases.values() if not c.live and _age_s(c.updated_at) > _retention_s()]:
        _cases.pop(case_id, None)
    for token in [t for t, e in _tokens.items() if e.expires_at < time.time() or e.case_id not in _cases]:
        _tokens.pop(token, None)


def _age_s(iso: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()


def start_call(
    *,
    caller_location: CallerLocation,
    call_sid: Optional[str],
    public_base_url: Optional[str],
    inject: Optional[Injector] = None,
    messenger: Optional[Messenger] = None,
) -> LocationCase:
    """Open location evidence the moment the call connects, so the console shows it live."""
    purge_expired()
    now = _now()
    case = LocationCase(
        id=f"AYO-{uuid.uuid4().hex[:6].upper()}",
        created_at=now,
        updated_at=now,
        call_sid=call_sid,
        caller_number=caller_location.number,
        country=caller_location.country,
        network=caller_location.describe() if caller_location.is_known else None,
        network_source=caller_location.source,
    )
    case.best = _best(case)
    _cases[case.id] = case
    _live[case.id] = _Live(case.id, caller_location.number, public_base_url, inject=inject, messenger=messenger)
    logger.info("Location case opened case=%s network=%s", case.id, case.network or "unknown")
    return case


def set_injector(case_id: str, inject: Optional[Injector]) -> None:
    live = _live.get(case_id)
    if live:
        live.inject = inject


def end_call(case_id: str) -> None:
    """Call hung up. Links stay valid until they expire: a GPS fix that lands after hangup is still useful."""
    live = _live.pop(case_id, None)
    if live:
        live.inject = None
        for task in (live.auto_link, live.feed_task):
            if task and not task.done():
                task.cancel()
    case = _cases.get(case_id)
    if case:
        case.live = False
        case.updated_at = _now()


def get_case(case_id: str) -> Optional[LocationCase]:
    return _cases.get(case_id)


def list_cases() -> list[LocationCase]:
    return sorted(_cases.values(), key=lambda c: c.created_at, reverse=True)


def _refresh_derived(case: LocationCase) -> None:
    """Recompute what depends on the raw signals: the spoken words as a map point, the cross-check, nearest help."""
    place = geo.resolve_place(case.spoken_address)
    case.spoken_point = (
        {"name": place.name, "latitude": place.lat, "longitude": place.lng, "radius_m": place.radius_m} if place else None
    )
    sources: list[geo.Source] = []
    if case.gps:
        sources.append(geo.Source("GPS link", case.gps["latitude"], case.gps["longitude"], case.gps.get("accuracy_m") or 100.0))
    if case.device_service:
        d = case.device_service
        sources.append(geo.Source("Device location service", d["latitude"], d["longitude"], d.get("accuracy_m") or 300.0))
    if case.spoken_point:
        sp = case.spoken_point
        sources.append(geo.Source("What the caller said", sp["latitude"], sp["longitude"], sp["radius_m"]))
    case.cross_check = geo.cross_check(sources) if sources else None
    anchor = sources[0] if sources else None  # most precise first
    case.nearest_help = geo.nearest_help(anchor.lat, anchor.lng, case.country) if anchor else []
    case.best = _best(case)
    case.updated_at = _now()


def _update(case_id: str, mutate: Callable[[LocationCase], None]) -> Optional[LocationCase]:
    case = _cases.get(case_id)
    if not case:
        return None
    mutate(case)
    _refresh_derived(case)
    return case


# --------------------------------------------------------------------------- #
# Device-location feed (seeded for the demo)
# --------------------------------------------------------------------------- #
async def attach_device_location(case_id: str, profile_id: Optional[str] = None) -> dict[str, Any]:
    """Ask the (seeded) feed about this case. With no profile id it uses the live call's number."""
    live = _live.get(case_id)
    fix = await device_feed.lookup(profile_id=profile_id, phone=live.caller_number if live else None)
    if not fix:
        return {"attached": False, "reason": "no_profile"}

    def mutate(case: LocationCase) -> None:
        case.device_service = {
            "latitude": fix["lat"], "longitude": fix["lng"], "accuracy_m": fix["accuracy_m"],
            "captured_at": _now(), "profile_id": fix["profile_id"], "area_label": fix["area"],
            "simulated": True,  # in the data, not on screen: the presenter discloses it
        }

    if not _update(case_id, mutate):
        return {"attached": False, "reason": "case_not_found"}
    logger.info("Device-location feed attached case=%s profile=%s", case_id, fix["profile_id"])
    return {"attached": True, "profile": fix["profile_id"]}


def start_device_lookup(case_id: str) -> None:
    """On a call from a seeded number, the feed answers a moment after the call connects."""
    live = _live.get(case_id)
    if not live or not device_feed.auto_enabled() or not device_feed.profile_for_number(live.caller_number):
        return
    live.feed_task = asyncio.create_task(attach_device_location(case_id))


# --------------------------------------------------------------------------- #
# Spoken place
# --------------------------------------------------------------------------- #
def record_spoken_address(case_id: str, address: str) -> dict[str, Any]:
    text = " ".join((address or "").split())[:MAX_ADDRESS_CHARS]
    if not text:
        return {"saved": False, "reason": "empty_address"}

    def mutate(case: LocationCase) -> None:
        case.spoken_address = text
        case.spoken_at = _now()

    if not _update(case_id, mutate):
        return {"saved": False, "reason": "case_not_found"}
    logger.info("Spoken location saved case=%s chars=%d", case_id, len(text))
    return {"saved": True}


# --------------------------------------------------------------------------- #
# One-tap GPS link
# --------------------------------------------------------------------------- #
def issue_link_token(case_id: str) -> str:
    token = secrets.token_urlsafe(16)
    _tokens[token] = _Token(case_id=case_id, expires_at=time.time() + LINK_TTL_SECONDS)
    return token


def token_case_id(token: str) -> Optional[str]:
    """Case id for a link that is still usable, or None."""
    entry = _tokens.get(token)
    if not entry or entry.used or entry.expires_at < time.time() or entry.case_id not in _cases:
        return None
    return entry.case_id


def _public_base(live: Optional[_Live]) -> Optional[str]:
    configured = (os.getenv("LOCATION_LINK_BASE_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return configured or (live.public_base_url if live else None)


async def _default_messenger(recipient: str, text: str) -> dict:
    from harness.tools.emergency import PhotonMessaging  # lazy: keeps the lane importable without httpx config

    return await PhotonMessaging().send(recipient, text)


async def send_location_link(case_id: str) -> dict[str, Any]:
    """Text the child a one-tap link through Photon. Twilio SMS is never used."""
    live = _live.get(case_id)
    if not live or not live.caller_number:
        return {"sent": False, "reason": "no_caller_number"}
    base = _public_base(live)
    if not base or not base.startswith("https://"):
        return {"sent": False, "reason": "no_public_https_url"}

    token = issue_link_token(case_id)
    body = f"Ayo: here is the link we talked about. Tap it, then tap Share. {base}/l/{token}"
    try:
        result = await (live.messenger or _default_messenger)(live.caller_number, body)
    except Exception as exc:  # a text failure must never break a live call
        logger.warning("Location link text failed: %s", type(exc).__name__)
        return {"sent": False, "reason": "text_failed"}
    if result.get("status") != "submitted":
        # "unknown" means the provider may or may not have it: Ayo must not claim a link was sent.
        logger.warning("Location link not confirmed: %s", result.get("reason") or result.get("status"))
        _tokens.pop(token, None)
        return {"sent": False, "reason": result.get("reason") or "text_unavailable"}

    def mutate(case: LocationCase) -> None:
        case.link_sent_via = sorted(set(case.link_sent_via + ["text"]))
        case.link_sent_at = _now()

    _update(case_id, mutate)
    return {"sent": True, "via": ["text"]}


async def record_gps(token: str, latitude: float, longitude: float, accuracy_m: Optional[float]) -> Optional[LocationCase]:
    case_id = token_case_id(token)
    if not case_id:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    _tokens[token].used = True
    fix = {
        "latitude": latitude, "longitude": longitude,
        "accuracy_m": accuracy_m if accuracy_m and accuracy_m > 0 else None,
        "captured_at": _now(),
    }

    def mutate(case: LocationCase) -> None:
        case.gps = fix

    case = _update(case_id, mutate)
    logger.info("GPS fix saved case=%s accuracy=%s", case_id, fix["accuracy_m"])

    live = _live.get(case_id)
    if case and live and live.inject:
        acc = f" (accurate to about {round(fix['accuracy_m'])} metres)" if fix["accuracy_m"] else ""
        try:
            await live.inject(
                "[SYSTEM NOTE, not spoken by the caller] The caller's phone just shared its location"
                f"{acc}. In one short, calm sentence, let them know you got it, then carry on listening. "
                "Do not read out coordinates, and do not say anyone is on the way; you do not know that."
            )
        except Exception as exc:  # the call may have just ended
            logger.warning("Could not tell the live call about the GPS fix: %s", type(exc).__name__)
    return case


# --------------------------------------------------------------------------- #
# Tool handling (declarations live in harness/tools/location.py)
# --------------------------------------------------------------------------- #
def auto_link_enabled() -> bool:
    return os.getenv("AUTO_LOCATION_LINK", "1").strip().lower() not in ("0", "false", "off", "no")


def start_auto_link(case_id: str) -> None:
    """Text the link as soon as the call connects, in the background. Ayo asks `location_link_status` before
    mentioning it, so she never talks about a link the child did not get."""
    live = _live.get(case_id)
    if not live or not auto_link_enabled():
        return
    live.auto_link = asyncio.create_task(send_location_link(case_id))


# The words only exist in the tool result, so Ayo can say them only when a link really reached the phone.
_ALSO_SAY = "I've also sent a link to your phone. If you can, open it and tap Share, so help can find you."


async def location_link_status(case_id: str) -> dict[str, Any]:
    live = _live.get(case_id)
    case = _cases.get(case_id)
    shared = bool(case and case.gps)
    if live and live.auto_link is not None:
        try:
            result = await asyncio.wait_for(asyncio.shield(live.auto_link), timeout=4)
        except asyncio.TimeoutError:
            return {"also_say": "", "location_shared": shared}
        except Exception:
            result = {"sent": False}
        return {"also_say": _ALSO_SAY if result.get("sent") and not shared else "", "location_shared": shared}
    sent = bool(case and case.link_sent_via)
    return {"also_say": _ALSO_SAY if sent and not shared else "", "location_shared": shared}


async def handle_tool_call(case_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "record_caller_location":
        return record_spoken_address(case_id, str(args.get("address") or ""))
    if name == "location_link_status":
        return await location_link_status(case_id)
    if name == "send_location_link":
        return await send_location_link(case_id)
    return {"status": "unavailable", "reason": "unknown_tool"}


def reset_for_tests() -> None:
    _cases.clear()
    _live.clear()
    _tokens.clear()
