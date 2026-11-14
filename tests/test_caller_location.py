"""Caller location extraction, enrichment and webhook/socket round trip."""

from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.voice.caller_location import (
    CallerLocation,
    build_location_briefing,
    from_twilio_webhook,
    lookup_by_number,
)
from app.voice.twilio_stream import TwilioMediaBridge

client = TestClient(app)


# Realistic Twilio voice webhook payloads. Note that Twilio sends absent
# geographic fields as empty strings rather than omitting the keys.
US_WEBHOOK = {
    "CallSid": "CA00000000000000000000000000000001",
    "AccountSid": "AC00000000000000000000000000000001",
    "From": "+14155551234",
    "To": "+18885550000",
    "CallStatus": "ringing",
    "Direction": "inbound",
    "FromCity": "SAN FRANCISCO",
    "FromState": "CA",
    "FromZip": "94105",
    "FromCountry": "US",
}

# Nigerian caller: Twilio resolves the country but no city/state/zip.
NG_WEBHOOK = {
    "CallSid": "CA00000000000000000000000000000002",
    "From": "+2348031234567",
    "To": "+18885550000",
    "FromCity": "",
    "FromState": "",
    "FromZip": "",
    "FromCountry": "NG",
}

# Worst case: Twilio's lookup returns nothing at all.
BARE_WEBHOOK = {
    "CallSid": "CA00000000000000000000000000000003",
    "From": "+2348120000000",
    "To": "+18885550000",
    "FromCity": "",
    "FromState": "",
    "FromZip": "",
    "FromCountry": "",
}


# --------------------------------------------------------------------------
# Extraction from webhook parameters
# --------------------------------------------------------------------------


def test_us_caller_uses_twilio_geo_verbatim():
    loc = from_twilio_webhook(US_WEBHOOK)
    assert loc.city == "SAN FRANCISCO"
    assert loc.state == "CA"
    assert loc.postal_code == "94105"
    assert loc.country == "US"
    assert loc.source == "twilio"
    # Twilio gave us a precise city, so the coarser prefix region is suppressed.
    assert loc.region is None


def test_international_caller_keeps_country_and_adds_prefix_region():
    loc = from_twilio_webhook(NG_WEBHOOK)
    assert loc.country == "NG"
    assert loc.city is None and loc.state is None
    # The gap Twilio left is filled from the E.164 prefix.
    assert loc.region == "Nigeria"
    assert loc.carrier == "MTN"
    assert loc.source == "mixed"
    assert loc.is_known


def test_caller_with_no_twilio_geo_falls_back_entirely_to_prefix():
    loc = from_twilio_webhook(BARE_WEBHOOK)
    assert loc.country == "NG"
    assert loc.region == "Nigeria"
    assert loc.carrier == "Airtel"
    assert loc.source == "number_prefix"
    assert loc.is_known


def test_empty_strings_are_normalised_to_none():
    loc = from_twilio_webhook({"From": "", "FromCity": "  ", "FromCountry": ""})
    assert loc.number is None
    assert loc.city is None
    assert loc.country is None
    assert loc.source == "unknown"
    assert not loc.is_known


def test_unparseable_number_does_not_raise():
    loc = from_twilio_webhook({"From": "anonymous", "FromCountry": ""})
    assert loc.country is None
    assert loc.source == "unknown"


def test_withheld_caller_id_still_yields_a_location_object():
    # Twilio sends From=+266696687 ("anonymous" on the keypad) for withheld IDs.
    loc = from_twilio_webhook({"From": "+266696687"})
    assert isinstance(loc, CallerLocation)
    assert not loc.is_known


@pytest.mark.parametrize(
    "number,expected_country",
    [
        ("+2348031234567", "NG"),
        ("+14155551234", "US"),
        ("+233244123456", "GH"),
    ],
)
def test_prefix_lookup_resolves_country(number, expected_country):
    assert lookup_by_number(number)["country"] == expected_country


def test_prefix_lookup_is_safe_on_junk():
    assert lookup_by_number(None)["country"] is None
    assert lookup_by_number("not-a-number")["country"] is None
    assert lookup_by_number("+9999999999999")["country"] is None


# --------------------------------------------------------------------------
# Derived fields
# --------------------------------------------------------------------------


def test_describe_orders_most_specific_first():
    assert from_twilio_webhook(US_WEBHOOK).describe() == "SAN FRANCISCO, CA, US"


def test_describe_does_not_repeat_the_country():
    # `region` already spells out "Nigeria"; appending "NG" would be noise.
    assert from_twilio_webhook(NG_WEBHOOK).describe() == "Nigeria"


def test_describe_handles_total_absence():
    assert CallerLocation().describe() == "unknown location"


def test_hotline_routing_by_country():
    ng = from_twilio_webhook(NG_WEBHOOK)
    assert ng.emergency_number == "112"
    assert "Cece Yara" in ng.child_helpline

    us = from_twilio_webhook(US_WEBHOOK)
    assert us.emergency_number == "911"
    assert us.child_helpline == "988"

    assert CallerLocation(country="ZZ").emergency_number is None


# --------------------------------------------------------------------------
# System prompt briefing
# --------------------------------------------------------------------------


def test_briefing_carries_local_services():
    briefing = build_location_briefing(from_twilio_webhook(NG_WEBHOOK))
    assert "Nigeria" in briefing
    assert "112" in briefing
    assert "0800 800 8001" in briefing


def test_briefing_forbids_volunteering_location_to_the_caller():
    # A frightened child who hears "I know where you are" feels watched.
    briefing = build_location_briefing(from_twilio_webhook(US_WEBHOOK))
    assert "Never state their location back to them unprompted" in briefing


def test_briefing_for_unknown_location_tells_agent_to_ask():
    briefing = build_location_briefing(CallerLocation())
    assert "could not be determined" in briefing
    assert "ask what country or city" in briefing


def test_briefing_warns_when_no_verified_emergency_number():
    briefing = build_location_briefing(CallerLocation(country="GH", region="Ghana"))
    assert "No verified emergency number is on file" in briefing


# --------------------------------------------------------------------------
# Webhook -> TwiML -> WebSocket round trip
# --------------------------------------------------------------------------


def _stream_parameters(twiml: str) -> dict[str, str]:
    """Parse <Parameter> elements out of a TwiML response."""
    root = ElementTree.fromstring(twiml)
    stream = root.find("./Connect/Stream")
    assert stream is not None, "TwiML is missing <Connect><Stream>"
    return {p.attrib["name"]: p.attrib["value"] for p in stream.findall("Parameter")}


def test_webhook_embeds_us_location_as_stream_parameters():
    response = client.post("/voice/incoming", data=US_WEBHOOK, headers={"host": "x.test"})
    assert response.status_code == 200
    params = _stream_parameters(response.text)
    assert params["sabi_city"] == "SAN FRANCISCO"
    assert params["sabi_state"] == "CA"
    assert params["sabi_postal_code"] == "94105"
    assert params["sabi_country"] == "US"
    assert params["sabi_number"] == "+14155551234"


def test_webhook_embeds_international_location():
    response = client.post("/voice/incoming", data=NG_WEBHOOK, headers={"host": "x.test"})
    params = _stream_parameters(response.text)
    assert params["sabi_country"] == "NG"
    assert params["sabi_region"] == "Nigeria"
    assert params["sabi_carrier"] == "MTN"
    assert params["sabi_source"] == "mixed"


def test_webhook_omits_parameters_it_has_no_value_for():
    response = client.post("/voice/incoming", data=NG_WEBHOOK, headers={"host": "x.test"})
    params = _stream_parameters(response.text)
    assert "sabi_city" not in params
    assert "sabi_postal_code" not in params


def test_webhook_supports_get_with_query_parameters():
    # Twilio sends query parameters when the webhook is configured as GET.
    response = client.get("/voice/incoming", params=US_WEBHOOK, headers={"host": "x.test"})
    assert response.status_code == 200
    assert _stream_parameters(response.text)["sabi_country"] == "US"


def test_webhook_escapes_xml_metacharacters_in_place_names():
    hostile = dict(US_WEBHOOK, FromCity='Sand & "Gravel" <City>')
    response = client.post("/voice/incoming", data=hostile, headers={"host": "x.test"})
    # Parsing is the assertion: unescaped quotes or angle brackets break it.
    assert _stream_parameters(response.text)["sabi_city"] == 'Sand & "Gravel" <City>'


def test_webhook_still_returns_valid_twiml_with_no_caller_data():
    response = client.post("/voice/incoming", data={}, headers={"host": "x.test"})
    assert response.status_code == 200
    root = ElementTree.fromstring(response.text)
    assert root.find("./Connect/Stream").attrib["url"] == "ws://x.test/ws/twilio"


# --------------------------------------------------------------------------
# The bridge's half of the round trip
# --------------------------------------------------------------------------


def _bridge() -> TwilioMediaBridge:
    return TwilioMediaBridge(websocket=None, system_prompt="BASE PROMPT")


def test_bridge_reads_location_from_start_custom_parameters():
    webhook_loc = from_twilio_webhook(NG_WEBHOOK)
    start_event = {
        "streamSid": "MZ0000000000000000000000000000001",
        "callSid": "CA00000000000000000000000000000002",
        "customParameters": webhook_loc.to_custom_parameters(),
    }

    bridge = _bridge()
    loc = bridge.handle_start(start_event)

    assert bridge.stream_sid == "MZ0000000000000000000000000000001"
    assert bridge.call_sid == "CA00000000000000000000000000000002"
    # Everything the webhook knew survived the trip through TwiML.
    assert loc == webhook_loc


def test_bridge_injects_location_into_the_system_prompt():
    bridge = _bridge()
    bridge.handle_start(
        {"customParameters": from_twilio_webhook(NG_WEBHOOK).to_custom_parameters()}
    )
    prompt = bridge.build_system_prompt()
    assert prompt.startswith("BASE PROMPT")
    assert "Nigeria" in prompt
    assert "0800 800 8001" in prompt


def test_bridge_tolerates_start_without_custom_parameters():
    bridge = _bridge()
    loc = bridge.handle_start({"streamSid": "MZ1", "callSid": "CA1"})
    assert not loc.is_known
    assert "could not be determined" in bridge.build_system_prompt()
