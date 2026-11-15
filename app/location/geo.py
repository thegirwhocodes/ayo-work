"""Small geography helpers for Ayo's location lane: distances, a seed gazetteer, nearest help, cross-check.

The gazetteer and help-centre list are a SEED for the demo, not survey data. Every point is an approximate
area centroid (good to a kilometre or two), which is why each carries a radius. Replace or extend them with
real data (OpenStreetMap extracts or a curated list) before relying on them. Nothing here calls Google:
Google Maps Platform terms limit caching of coordinates to 30 days, so a stored gazetteer is built from
other sources.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

EARTH_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lng: float
    radius_m: float  # how far the true spot could be from this centroid
    country: str
    aliases: tuple[str, ...] = ()


# (name, lat, lng, radius_m, country, aliases). When a sentence names several places, the most specific one
# (smallest radius) wins, so "Mile 1 market in Port Harcourt" resolves to the market, not the whole city.
_PLACES = [
    # Lagos
    ("Surulere, Lagos", 6.4969, 3.3539, 3000, "NG", ("surulere",)),
    ("Yaba, Lagos", 6.5095, 3.3711, 2000, "NG", ("yaba",)),
    ("Ojuelegba, Lagos", 6.5090, 3.3623, 1000, "NG", ("ojuelegba",)),
    ("Ikeja, Lagos", 6.6018, 3.3515, 4000, "NG", ("ikeja",)),
    ("Ajegunle, Lagos", 6.4531, 3.3298, 3000, "NG", ("ajegunle",)),
    ("Mushin, Lagos", 6.5280, 3.3541, 3000, "NG", ("mushin",)),
    ("Mile 12, Lagos", 6.6046, 3.3958, 2000, "NG", ("mile 12", "mile twelve")),
    ("Oshodi, Lagos", 6.5550, 3.3434, 2000, "NG", ("oshodi",)),
    ("Maryland, Lagos", 6.5667, 3.3667, 1500, "NG", ("maryland",)),
    ("Ojota, Lagos", 6.5900, 3.3800, 2000, "NG", ("ojota",)),
    ("Apapa, Lagos", 6.4489, 3.3594, 3000, "NG", ("apapa",)),
    ("Ikorodu, Lagos", 6.6194, 3.5105, 6000, "NG", ("ikorodu",)),
    ("Lekki, Lagos", 6.4698, 3.5852, 8000, "NG", ("lekki",)),
    ("Victoria Island, Lagos", 6.4281, 3.4219, 3000, "NG", ("victoria island", "v i", "vi")),
    ("Ikoyi, Lagos", 6.4549, 3.4246, 3000, "NG", ("ikoyi",)),
    ("Lagos Island", 6.4541, 3.3947, 3000, "NG", ("lagos island",)),
    ("Iyana Ipaja, Lagos", 6.6100, 3.2700, 4000, "NG", ("iyana ipaja", "ipaja")),
    ("Lagos (city-wide)", 6.5244, 3.3792, 15000, "NG", ("lagos",)),
    # Rivers State / Abuja
    ("Mile 1 Market, Port Harcourt", 4.7891, 7.0014, 1500, "NG", ("mile 1", "mile one")),
    ("Port Harcourt", 4.8156, 7.0498, 8000, "NG", ("port harcourt", "pitakwa", "ph")),
    ("Abuja", 9.0765, 7.3986, 12000, "NG", ("abuja",)),
    # New Jersey (demo venue)
    ("Princeton University", 40.3430, -74.6514, 1200, "US", ("princeton university", "frist", "campus")),
    ("Nassau Street, Princeton", 40.3489, -74.6593, 800, "US", ("nassau street", "nassau st")),
    ("Princeton, NJ", 40.3573, -74.6672, 5000, "US", ("princeton",)),
    ("Trenton, NJ", 40.2206, -74.7597, 6000, "US", ("trenton",)),
    ("Newark, NJ", 40.7357, -74.1724, 8000, "US", ("newark",)),
    ("Camden, NJ", 39.9259, -75.1196, 6000, "US", ("camden",)),
]

PLACES = [Place(n, la, lo, r, c, a) for n, la, lo, r, c, a in _PLACES]
_ALIAS_INDEX = [(alias, p) for p in PLACES for alias in p.aliases]


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


def resolve_place(text: Optional[str]) -> Optional[Place]:
    """Match the caller's own words to a known area: whole words only, most specific place wins."""
    norm = _normalize(text or "")
    if not norm:
        return None
    found = [p for alias, p in _ALIAS_INDEX if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", norm)]
    return min(found, key=lambda p: p.radius_m) if found else None


# ---------------------------------------------------------------------------------------------------------
# Help centres. Area-level positions (the centroid of the area each one serves), real organisations, and a
# phone number only where it was verified. Demo list: a real deployment needs a curated, reviewed directory.
# ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class HelpCentre:
    name: str
    kind: str
    country: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    area: Optional[str] = None
    phone: Optional[str] = None


HELP_CENTRES = [
    HelpCentre("Mirabel Centre (sexual assault referral, LASUTH)", "sexual-assault referral", "NG", 6.6018, 3.3515, "Ikeja, Lagos"),
    HelpCentre("Lagos State Domestic & Sexual Violence Agency", "state agency", "NG", 6.6018, 3.3515, "Ikeja, Lagos"),
    HelpCentre("Cece Yara Foundation", "child helpline & centre", "NG", 6.4549, 3.4246, "Ikoyi / Lagos Island", "0800 800 8001"),
    HelpCentre("Capital Health Regional Medical Center", "hospital", "US", 40.2206, -74.7597, "Trenton, NJ"),
    HelpCentre("Penn Medicine Princeton Medical Center", "hospital", "US", 40.3573, -74.6672, "Plainsboro / Princeton, NJ"),
    # National lines have no position; they are always offered for the matching country.
    HelpCentre("Emergency services", "emergency", "NG", phone="112"),
    HelpCentre("Cece Yara child helpline (free, 24/7)", "child helpline", "NG", phone="0800 800 8001"),
    HelpCentre("Emergency services", "emergency", "US", phone="911"),
    HelpCentre("Childhelp National Child Abuse Hotline", "child helpline", "US", phone="1-800-422-4453"),
]

NEAR_KM = 60.0


def nearest_help(lat: float, lng: float, country: Optional[str] = None, limit: int = 3) -> list[dict]:
    located = [(haversine_km(lat, lng, h.lat, h.lng), h) for h in HELP_CENTRES if h.lat is not None]
    located.sort(key=lambda dh: dh[0])
    near = [(d, h) for d, h in located if d <= NEAR_KM][:limit]
    if near:
        # Where the child is decides which emergency numbers to show, not where the SIM was registered. A US number
        # calling from Lagos must be offered 112 and Cece Yara, not 911. `country` is only the fallback.
        country = near[0][1].country
    out = [{"name": h.name, "kind": h.kind, "phone": h.phone, "area": h.area, "distance_km": round(d, 1)} for d, h in near]
    out += [{"name": h.name, "kind": h.kind, "phone": h.phone, "area": None, "distance_km": None}
            for h in HELP_CENTRES if h.lat is None and h.country == country]
    return out


# ---------------------------------------------------------------------------------------------------------
# Cross-check. Two location sources "agree" when the gap between them is no bigger than their combined
# uncertainty. When they do not, the responder sees both. Nothing is ever picked or averaged.
# ---------------------------------------------------------------------------------------------------------
SLACK_M = 200.0


@dataclass(frozen=True)
class Source:
    label: str
    lat: float
    lng: float
    radius_m: float


def cross_check(sources: list[Source]) -> dict:
    if len(sources) < 2:
        return {"status": "insufficient", "pairs": [], "sources_used": [s.label for s in sources],
                "summary": "Only one location source so far, so there is nothing to cross-check yet."}
    pairs = []
    for i, a in enumerate(sources):
        for b in sources[i + 1:]:
            km = haversine_km(a.lat, a.lng, b.lat, b.lng)
            pairs.append({"a": a.label, "b": b.label, "distance_km": round(km, 2),
                          "agree": km * 1000 <= a.radius_m + b.radius_m + SLACK_M})
    bad = [p for p in pairs if not p["agree"]]
    if not bad:
        far = max(p["distance_km"] for p in pairs)
        names = ", ".join(s.label for s in sources)
        return {"status": "agree", "pairs": pairs, "sources_used": [s.label for s in sources],
                "summary": f"{names} agree (the furthest apart are {far} km, within their uncertainty)."}
    detail = "; ".join(f"{p['a']} and {p['b']} are {p['distance_km']} km apart" for p in bad)
    return {"status": "disagree", "pairs": pairs, "sources_used": [s.label for s in sources],
            "summary": f"DISAGREE: {detail}. Show every source to the responder and do not pick one."}
