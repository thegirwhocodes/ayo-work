"""Stand-in for a FUTURE device-location feed (emergency-location access for a child-safety line).

No such access exists today: Android's Emergency Location Service and Apple's equivalent only hand a phone's
location to accredited emergency services when an emergency number is dialled, and a child-safety line is not
automatically one. Getting access is a long-term partnership goal (government and carrier designation). Until
then the demo needs something that shows what the feature will look like, so this module returns pre-seeded
positions for FICTIONAL child profiles.

The console does not badge it: the presenter tells the audience it is simulated. Every fix still carries
`simulated=True` in the data, and this docstring says what it is, so nothing about it is hidden in the code.
It must never be passed off as real access to anyone's phone.

A real demo phone can be mapped to a profile with
`SIM_DEMO_NUMBERS="+15551230000=princeton_demo,+...=lagos_yaba"`. Numbers stay in the environment, never in
the repo.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

PROFILES: dict[str, dict] = {
    # Princeton campus centre. A device feed built on cell/Wi-Fi is not GPS-precise, hence 600 m.
    # SIM_PRINCETON_LATLNG="lat,lng" moves it to wherever you are rehearsing.
    "princeton_demo": {
        "label": "Demo child, Princeton NJ", "lat": 40.3430, "lng": -74.6514, "accuracy_m": 600,
        "area": "Princeton, NJ (campus)", "country": "US",
    },
    "lagos_surulere": {
        "label": "Demo child, Surulere (Lagos)", "lat": 6.5001, "lng": 3.3530, "accuracy_m": 150,
        "area": "Surulere, Lagos", "country": "NG",
    },
    "lagos_yaba": {
        "label": "Demo child, Yaba (Lagos)", "lat": 6.5101, "lng": 3.3720, "accuracy_m": 150,
        "area": "Yaba, Lagos", "country": "NG",
    },
    "ph_mile1": {
        "label": "Demo child, Mile 1 (Port Harcourt)", "lat": 4.7893, "lng": 7.0016, "accuracy_m": 150,
        "area": "Mile 1, Port Harcourt", "country": "NG",
    },
    "mismatch_demo": {
        "label": "Demo: device says Ikeja (a spoken place elsewhere will DISAGREE)", "lat": 6.5981, "lng": 3.3511,
        "accuracy_m": 150, "area": "Ikeja, Lagos", "country": "NG",
    },
}

# Fictional numbers (never real people).
SEEDED_NUMBERS: dict[str, str] = {
    "+16095550101": "princeton_demo",
    "+2348012345001": "lagos_surulere",
    "+2348012345002": "lagos_yaba",
    "+2348012345003": "ph_mile1",
    "+2348012345004": "mismatch_demo",
}


def _env_numbers() -> dict[str, str]:
    out: dict[str, str] = {}
    for item in os.getenv("SIM_DEMO_NUMBERS", "").split(","):
        number, _, profile = item.strip().partition("=")
        if number and profile.strip() in PROFILES:
            out[number.strip()] = profile.strip()
    return out


def profile_for_number(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    return _env_numbers().get(phone) or SEEDED_NUMBERS.get(phone)


def _profile(pid: str) -> Optional[dict]:
    profile = PROFILES.get(pid)
    if not profile:
        return None
    profile = dict(profile)
    override = os.getenv("SIM_PRINCETON_LATLNG", "") if pid == "princeton_demo" else ""
    if override:
        try:
            lat, lng = (float(x) for x in override.split(","))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                profile["lat"], profile["lng"] = lat, lng
        except ValueError:
            pass
    return profile


def list_profiles() -> list[dict]:
    return [{"id": pid, "label": p["label"], "area": p["area"]} for pid, p in PROFILES.items()]


def delay_s() -> float:
    try:
        return max(0.0, float(os.getenv("SIM_LOCATION_DELAY_S", "1.5")))
    except ValueError:
        return 1.5


def auto_enabled() -> bool:
    return os.getenv("SIM_LOCATION_AUTO", "1").strip().lower() not in ("0", "false", "off", "no")


async def lookup(profile_id: Optional[str] = None, phone: Optional[str] = None) -> Optional[dict]:
    """Pretend to ask the device-location feed. Returns the seeded fix after a short, visible delay."""
    pid = profile_id or profile_for_number(phone)
    profile = _profile(pid or "")
    if not profile:
        return None
    await asyncio.sleep(delay_s())  # the lookup is not instant in real life either
    return {"profile_id": pid, **profile, "simulated": True}
