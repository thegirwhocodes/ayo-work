"""Caller location context for inbound telephony calls.

Twilio performs a best-effort geographic lookup on the `From` number and sends
`FromCity` / `FromState` / `FromZip` / `FromCountry` to the voice webhook *"if
available"*. For many international callers only `FromCountry` arrives, and
sometimes nothing does. Where Twilio is silent we fall back to the E.164 prefix,
which always yields at least a country and often a region.

Two separate connections are involved in a Media Streams call:

1. The voice webhook (`POST /voice/incoming`) — carries the geo parameters.
2. The media WebSocket (`/ws/twilio`) — carries the audio, and *no* geo data.

The only supported bridge between them is `<Parameter>` elements nested inside
the `<Stream>` TwiML, which Twilio replays back as `start.customParameters` on
the socket. `to_custom_parameters()` / `from_custom_parameters()` are the two
ends of that round trip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Optional

logger = logging.getLogger("sabi.voice.caller_location")

# Prefix used for the TwiML <Parameter> names so they cannot collide with any
# other custom parameter a future caller of this module might add.
PARAM_PREFIX = "sabi_"

# Hotline routing by ISO 3166-1 alpha-2 country code. Nigeria and the US are the
# two regions Sabi Safe currently serves; anything else falls back to None and
# the agent relies on the caller naming a local service.
EMERGENCY_NUMBERS = {
    "NG": "112",
    "US": "911",
    "CA": "911",
    "GB": "999",
}

CHILD_HELPLINES = {
    "NG": "0800 800 8001 (Cece Yara)",
    "US": "988",
    "CA": "988",
    "GB": "0800 1111 (Childline)",
}


def _clean(value: Optional[str]) -> Optional[str]:
    """Twilio sends absent geo fields as empty strings, not as missing keys."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True)
class CallerLocation:
    """Everything we know about where a caller is dialing from."""

    number: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None  # ISO 3166-1 alpha-2, e.g. "NG"
    region: Optional[str] = None  # free-text area from the number prefix
    carrier: Optional[str] = None
    source: str = "unknown"  # "twilio" | "number_prefix" | "mixed" | "unknown"

    @property
    def is_known(self) -> bool:
        return bool(self.city or self.state or self.country or self.region)

    @property
    def emergency_number(self) -> Optional[str]:
        return EMERGENCY_NUMBERS.get(self.country or "")

    @property
    def child_helpline(self) -> Optional[str]:
        return CHILD_HELPLINES.get(self.country or "")

    def describe(self) -> str:
        """A short human-readable place name, most specific part first."""
        parts = [p for p in (self.city, self.state, self.region) if p]
        # The prefix lookup already spells the country out inside `region`
        # ("Nigeria"), so appending the code there would read "Nigeria, NG".
        if self.country and not self.region:
            parts.append(self.country)
        return ", ".join(dict.fromkeys(parts)) if parts else "unknown location"

    def to_custom_parameters(self) -> dict[str, str]:
        """Serialize for TwiML `<Parameter>` elements. Empty values are dropped."""
        fields = {
            "number": self.number,
            "city": self.city,
            "state": self.state,
            "postal_code": self.postal_code,
            "country": self.country,
            "region": self.region,
            "carrier": self.carrier,
            "source": self.source,
        }
        return {f"{PARAM_PREFIX}{k}": v for k, v in fields.items() if v}

    @classmethod
    def from_custom_parameters(cls, params: Optional[Mapping[str, str]]) -> "CallerLocation":
        """Rebuild from `start.customParameters` on the Media Streams socket."""
        params = params or {}
        get = lambda key: _clean(params.get(f"{PARAM_PREFIX}{key}"))  # noqa: E731
        return cls(
            number=get("number"),
            city=get("city"),
            state=get("state"),
            postal_code=get("postal_code"),
            country=get("country"),
            region=get("region"),
            carrier=get("carrier"),
            source=get("source") or "unknown",
        )


def lookup_by_number(number: Optional[str]) -> dict[str, Optional[str]]:
    """Derive country/region/carrier from an E.164 number via libphonenumber.

    Returns empty values if `phonenumbers` is not installed or the number is
    unparseable — this is enrichment, and must never break a live call.
    """
    blank: dict[str, Optional[str]] = {"country": None, "region": None, "carrier": None}
    if not number:
        return blank

    try:
        import phonenumbers
        from phonenumbers import carrier as pn_carrier
        from phonenumbers import geocoder as pn_geocoder
    except ImportError:
        logger.debug("phonenumbers not installed; skipping prefix lookup")
        return blank

    try:
        parsed = phonenumbers.parse(number, None)
    except Exception:
        logger.debug("Could not parse caller number for geo lookup")
        return blank

    if not phonenumbers.is_valid_number(parsed):
        return blank

    return {
        "country": phonenumbers.region_code_for_number(parsed),
        "region": _clean(pn_geocoder.description_for_number(parsed, "en")),
        "carrier": _clean(pn_carrier.name_for_number(parsed, "en")),
    }


def from_twilio_webhook(form: Mapping[str, str]) -> CallerLocation:
    """Build caller location from the voice webhook's POST parameters.

    Twilio's own geo data wins where present; the number prefix fills the gaps.
    """
    number = _clean(form.get("From"))
    city = _clean(form.get("FromCity"))
    state = _clean(form.get("FromState"))
    postal_code = _clean(form.get("FromZip"))
    country = _clean(form.get("FromCountry"))

    had_twilio_geo = any((city, state, postal_code, country))

    prefix = lookup_by_number(number)
    used_prefix = False

    if not country and prefix["country"]:
        country = prefix["country"]
        used_prefix = True

    region = None
    # Only surface the prefix region when Twilio gave us nothing more specific;
    # "Lagos, Nigeria" alongside an exact city would just be noise.
    if not city and not state and prefix["region"]:
        region = prefix["region"]
        used_prefix = True

    carrier_name = prefix["carrier"]
    if carrier_name:
        used_prefix = True

    if had_twilio_geo and used_prefix:
        source = "mixed"
    elif had_twilio_geo:
        source = "twilio"
    elif used_prefix:
        source = "number_prefix"
    else:
        source = "unknown"

    return CallerLocation(
        number=number,
        city=city,
        state=state,
        postal_code=postal_code,
        country=country,
        region=region,
        carrier=carrier_name,
        source=source,
    )


def build_location_briefing(location: CallerLocation) -> str:
    """Render caller location as a system-prompt appendix for the voice agent.

    Deliberately instructs the model *not* to volunteer the location back to the
    caller. A distressed child who hears "I can see you're calling from Lagos"
    experiences surveillance, not safety, which is the opposite of what this
    line is for. The location exists to route emergency services correctly.
    """
    if not location.is_known:
        return (
            "\n\n---\n\n## CALLER CONTEXT\n\n"
            "The caller's location could not be determined. If the conversation reaches "
            "a point where emergency help is needed, gently ask what country or city they "
            "are in before naming any helpline, so you do not give them a number that does "
            "not work where they are."
        )

    lines = [f"- Calling from: {location.describe()}"]
    if location.country:
        lines.append(f"- Country code: {location.country}")
    if location.carrier:
        lines.append(f"- Carrier: {location.carrier}")
    if location.emergency_number:
        lines.append(f"- Local emergency number: {location.emergency_number}")
    if location.child_helpline:
        lines.append(f"- Local child helpline: {location.child_helpline}")

    briefing = "\n\n---\n\n## CALLER CONTEXT\n\n" + "\n".join(lines)
    briefing += (
        "\n\nUse this only to route the caller to services that exist where they are. "
        "Never state their location back to them unprompted — a frightened child who hears "
        "that you know where they are feels watched, not safe. If they ask, answer honestly "
        "that the phone network shows the area their number is registered to."
    )
    if not location.emergency_number:
        briefing += (
            "\n\nNo verified emergency number is on file for this country. If help is needed, "
            "ask the caller what emergency service they can reach locally rather than guessing."
        )
    return briefing
