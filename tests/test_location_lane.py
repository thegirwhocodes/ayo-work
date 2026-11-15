"""Location lane: spoken place, one-tap GPS link, seeded device feed, cross-check, console."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.location import device_feed, geo, lane
from app.main import app
from app.voice.caller_location import CallerLocation
from app.voice.twilio_stream import TwilioMediaBridge
from harness.tools.location import LOCATION_DECLARATIONS, LOCATION_TOOL_NAMES
from harness.tools.openai_format import to_openai_tools

client = TestClient(app)
NG = CallerLocation(number="+2348012345001", country="NG", region="Lagos", source="number_prefix")


def open_case(messenger=None, number="+2348031234567", base="https://ayo.example"):
    return lane.start_call(caller_location=CallerLocation(number=number, country="NG", region="Lagos",
                                                          source="number_prefix"),
                           call_sid="CA1", public_base_url=base, messenger=messenger)


def submitted():
    sent = []

    async def messenger(recipient, text):
        sent.append((recipient, text))
        return {"status": "submitted", "message_id": "m1"}
    return messenger, sent


# ------------------------------------------------------------------ spoken
def test_spoken_place_becomes_a_map_point_and_most_specific_wins():
    case = open_case()
    assert lane.record_spoken_address(case.id, "I am near Mile 1 market in Port Harcourt")["saved"]
    assert case.spoken_point["name"] == "Mile 1 Market, Port Harcourt"
    assert case.best == "spoken"


def test_empty_and_overlong_addresses():
    case = open_case()
    assert lane.record_spoken_address(case.id, "   ")["saved"] is False
    lane.record_spoken_address(case.id, "x" * 900)
    assert len(case.spoken_address) == lane.MAX_ADDRESS_CHARS


# ------------------------------------------------------------------ link + gps
def test_link_is_sent_and_gps_token_is_single_use():
    messenger, sent = submitted()
    case = open_case(messenger)
    assert asyncio.run(lane.send_location_link(case.id)) == {"sent": True, "via": ["text"]}
    recipient, text = sent[0]
    assert recipient == "+2348031234567"
    token = text.rsplit("/l/", 1)[1]
    assert lane.token_case_id(token) == case.id
    assert asyncio.run(lane.record_gps(token, 6.5, 3.35, 20)) is case
    assert case.best == "gps" and case.gps["accuracy_m"] == 20
    assert asyncio.run(lane.record_gps(token, 6.5, 3.35, 20)) is None  # used


def test_link_not_claimed_when_provider_unsure_or_down_or_no_https():
    for result in ({"status": "unknown", "reason": "message_submission_uncertain"},
                   {"status": "unavailable", "reason": "photon_not_configured"}):
        async def messenger(recipient, text, result=result):
            return result
        case = open_case(messenger)
        out = asyncio.run(lane.send_location_link(case.id))
        assert out["sent"] is False and case.link_sent_via == []
        assert not lane._tokens  # the unsent token does not linger
    case = open_case(submitted()[0], base="http://insecure.example")
    assert asyncio.run(lane.send_location_link(case.id))["reason"] == "no_public_https_url"


def test_messenger_exception_never_breaks_the_call():
    async def boom(recipient, text):
        raise RuntimeError("network")
    case = open_case(boom)
    assert asyncio.run(lane.send_location_link(case.id)) == {"sent": False, "reason": "text_failed"}


def test_bad_coordinates_rejected_and_do_not_burn_the_token():
    messenger, sent = submitted()
    case = open_case(messenger)
    asyncio.run(lane.send_location_link(case.id))
    token = sent[0][1].rsplit("/l/", 1)[1]
    assert asyncio.run(lane.record_gps(token, 123, 3.3, 5)) is None
    assert lane.token_case_id(token) == case.id


def test_link_status_only_offers_words_when_a_link_really_went():
    async def run(messenger):
        case = open_case(messenger)
        lane.start_auto_link(case.id)
        return await lane.location_link_status(case.id)

    messenger, _ = submitted()
    assert asyncio.run(run(messenger))["also_say"].startswith("I've also sent a link")

    async def down(recipient, text):
        return {"status": "unavailable", "reason": "photon_not_configured"}
    assert asyncio.run(run(down))["also_say"] == ""


def test_gps_arrival_is_passed_to_the_live_model_without_coordinates():
    notes = []

    async def inject(text):
        notes.append(text)
    messenger, sent = submitted()
    case = open_case(messenger)
    lane.set_injector(case.id, inject)
    asyncio.run(lane.send_location_link(case.id))
    asyncio.run(lane.record_gps(sent[0][1].rsplit("/l/", 1)[1], 6.5001, 3.3501, 15))
    assert notes and "6.5001" not in notes[0] and "on the way" in notes[0]


# ------------------------------------------------------------------ device feed + cross-check
def test_seeded_number_gets_a_device_fix_marked_simulated_in_data_only():
    case = lane.start_call(caller_location=NG, call_sid="CA2", public_base_url=None)
    out = asyncio.run(lane.attach_device_location(case.id))
    assert out == {"attached": True, "profile": "lagos_surulere"}
    assert case.device_service["simulated"] is True
    assert case.best == "device_service"
    assert any(h["phone"] == "112" for h in case.nearest_help)


def test_unknown_number_has_no_feed():
    case = open_case()
    assert asyncio.run(lane.attach_device_location(case.id))["reason"] == "no_profile"


def test_agreeing_and_disagreeing_sources():
    case = lane.start_call(caller_location=NG, call_sid="CA3", public_base_url=None)
    asyncio.run(lane.attach_device_location(case.id))            # Surulere
    lane.record_spoken_address(case.id, "somewhere in Surulere")
    assert case.cross_check["status"] == "agree"
    lane.record_spoken_address(case.id, "I'm at Mile 1 in Port Harcourt")
    assert case.cross_check["status"] == "disagree"
    assert "do not pick one" in case.cross_check["summary"]


def test_emergency_numbers_follow_where_the_child_is_not_the_sim_country():
    us_sim = CallerLocation(number="+16095550101", country="US", source="twilio")
    case = lane.start_call(caller_location=us_sim, call_sid="CA4", public_base_url=None)
    lane.record_spoken_address(case.id, "Yaba")
    phones = {h["phone"] for h in case.nearest_help}
    assert "112" in phones and "911" not in phones


def test_no_real_numbers_in_the_seed():
    assert all(n.startswith(("+1609555", "+23480123450")) for n in device_feed.SEEDED_NUMBERS)


# ------------------------------------------------------------------ tools
def test_declarations_convert_for_both_engines_and_route():
    assert {d["name"] for d in LOCATION_DECLARATIONS} == set(LOCATION_TOOL_NAMES)
    assert {t["name"] for t in to_openai_tools(LOCATION_DECLARATIONS)} == set(LOCATION_TOOL_NAMES)
    case = open_case()
    out = asyncio.run(lane.handle_tool_call(case.id, "record_caller_location", {"address": "Yaba"}))
    assert out == {"saved": True}
    assert asyncio.run(lane.handle_tool_call(case.id, "nope", {}))["reason"] == "unknown_tool"


# ------------------------------------------------------------------ bridge
class FakeWS:
    headers = {"host": "ayo.example"}


def test_bridge_opens_the_lane_only_with_a_verified_number_and_ignores_the_unsigned_one(monkeypatch):
    bridge = TwilioMediaBridge(websocket=FakeWS(), system_prompt="x")
    bridge.location = CallerLocation(number="+19999999999", country="US", source="twilio")  # unsigned, attacker-chosen
    texted = []

    async def run():
        monkeypatch.setattr(lane, "_default_messenger", lambda r, t: _record(texted, r, t))
        bridge._open_location_lane("+2348031234567")
        await asyncio.sleep(0.05)
    asyncio.run(run())
    case = lane.get_case(bridge.location_case_id)
    assert case.caller_number == "+2348031234567"
    assert [r for r, _ in texted] == ["+2348031234567"]


async def _record(texted, recipient, text):
    texted.append((recipient, text))
    return {"status": "submitted"}


def test_bridge_without_verified_number_texts_nobody():
    bridge = TwilioMediaBridge(websocket=FakeWS(), system_prompt="x")
    bridge.location = CallerLocation(number="+19999999999", country="US", source="twilio")
    asyncio.run(asyncio.sleep(0))
    bridge._open_location_lane(None)
    assert lane.get_case(bridge.location_case_id).caller_number is None
    assert lane._live[bridge.location_case_id].auto_link is None


def test_bridge_routes_location_tools_and_prompt_mentions_them():
    bridge = TwilioMediaBridge(websocket=FakeWS(), system_prompt="x")
    out = asyncio.run(bridge.relay_to_harness("location_link_status", {}))
    assert out["reason"] == "location_not_ready"
    bridge._open_location_lane(None)
    assert asyncio.run(bridge.relay_to_harness("record_caller_location", {"address": "Yaba"})) == {"saved": True}
    assert "record_caller_location" in bridge.build_system_prompt()


# ------------------------------------------------------------------ http
def test_link_page_and_expired_page():
    messenger, sent = submitted()
    case = open_case(messenger)
    asyncio.run(lane.send_location_link(case.id))
    token = sent[0][1].rsplit("/l/", 1)[1]
    ok = client.get(f"/l/{token}")
    assert ok.status_code == 200 and "navigator.geolocation" in ok.text
    assert client.get("/l/not-a-token").status_code == 410
    post = client.post(f"/api/l/{token}", json={"latitude": 6.5, "longitude": 3.35, "accuracy_m": 12})
    assert post.status_code == 200 and case.gps
    assert client.post(f"/api/l/{token}", json={"latitude": 6.5, "longitude": 3.35}).status_code == 410


def test_console_is_off_without_a_key_and_locked_with_one(monkeypatch):
    assert client.get("/api/location/cases").status_code == 403
    assert client.get("/location").status_code == 403
    monkeypatch.setenv("LOCATION_CONSOLE_KEY", "short")
    assert client.get("/api/location/cases?key=short").status_code == 403  # too short to count as a key
    monkeypatch.setenv("LOCATION_CONSOLE_KEY", "a-long-enough-secret")
    assert client.get("/api/location/cases?key=wrong-key-here!!").status_code == 403
    case = open_case()
    lane.record_spoken_address(case.id, "Yaba")
    data = client.get("/api/location/cases", headers={"X-Console-Key": "a-long-enough-secret"}).json()
    assert data["cases"][0]["id"] == case.id and data["profiles"]
    page = client.get("/location?key=a-long-enough-secret")
    assert page.status_code == 200 and "Caller location" in page.text
    assert "SIMULATED" not in page.text.upper()  # Naomi's call: the presenter discloses it, not the screen


def test_console_can_trigger_a_device_lookup(monkeypatch):
    monkeypatch.setenv("LOCATION_CONSOLE_KEY", "a-long-enough-secret")
    case = open_case()
    r = client.post(f"/api/location/cases/{case.id}/device-lookup", json={"profile_id": "lagos_yaba"},
                    headers={"X-Console-Key": "a-long-enough-secret"})
    assert r.status_code == 200 and case.device_service["area_label"] == "Yaba, Lagos"
    assert client.post(f"/api/location/cases/{case.id}/device-lookup", json={"profile_id": "x"},
                       headers={"X-Console-Key": "a-long-enough-secret"}).status_code == 404


def test_geo_distance_sanity():
    assert 0 < geo.haversine_km(6.5, 3.35, 6.6, 3.35) < 12
