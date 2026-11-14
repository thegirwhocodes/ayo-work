#!/usr/bin/env python3
"""Simulate a Twilio inbound voice webhook and show the caller context it produces.

Runs the real webhook handler and prints what the voice agent would be told
about the caller, without placing a call or spending Twilio credit.

    # Built-in sample callers (US, Nigeria, Nigeria with no Twilio geo, withheld)
    python scripts/simulate_call.py

    # Any number, as if Twilio resolved nothing but the number itself
    python scripts/simulate_call.py --number +2348031234567

    # Pretend Twilio also resolved a city
    python scripts/simulate_call.py --number +14155551234 \
        --city "SAN FRANCISCO" --state CA --zip 94105 --country US

    # Run against an already-running server instead of in-process
    python scripts/simulate_call.py --url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.voice.caller_location import CallerLocation, build_location_briefing  # noqa: E402

SAMPLES = [
    {
        "label": "US caller, full Twilio geo lookup",
        "From": "+14155551234",
        "FromCity": "SAN FRANCISCO",
        "FromState": "CA",
        "FromZip": "94105",
        "FromCountry": "US",
    },
    {
        "label": "Nigerian caller, Twilio resolves country only",
        "From": "+2348031234567",
        "FromCity": "",
        "FromState": "",
        "FromZip": "",
        "FromCountry": "NG",
    },
    {
        "label": "Nigerian caller, Twilio resolves nothing",
        "From": "+2348120000000",
        "FromCity": "",
        "FromState": "",
        "FromZip": "",
        "FromCountry": "",
    },
    {
        "label": "Ghanaian caller, no country we have hotlines for",
        "From": "+233244123456",
        "FromCountry": "",
    },
    {
        "label": "Withheld caller ID",
        "From": "+266696687",
        "FromCountry": "",
    },
]


def post_webhook(payload: dict, url: str | None) -> str:
    """Return the TwiML the webhook produces, in-process or over HTTP."""
    if url:
        import httpx

        response = httpx.post(f"{url.rstrip('/')}/voice/incoming", data=payload, timeout=10)
        response.raise_for_status()
        return response.text

    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).post(
        "/voice/incoming", data=payload, headers={"host": "sabi-safe.test"}
    )
    response.raise_for_status()
    return response.text


def stream_parameters(twiml: str) -> dict[str, str]:
    stream = ElementTree.fromstring(twiml).find("./Connect/Stream")
    if stream is None:
        raise SystemExit("TwiML had no <Connect><Stream>; the webhook is misconfigured.")
    return {p.attrib["name"]: p.attrib["value"] for p in stream.findall("Parameter")}


def report(label: str, payload: dict, url: str | None) -> None:
    print("=" * 78)
    print(label)
    print("=" * 78)
    print(f"Twilio sent      : {json.dumps(payload, sort_keys=True)}")

    twiml = post_webhook(payload, url)
    params = stream_parameters(twiml)
    print(f"TwiML parameters : {json.dumps(params, sort_keys=True)}")

    # Exactly what the media socket reconstructs from start.customParameters.
    location = CallerLocation.from_custom_parameters(params)
    print(f"Resolved location: {location.describe()}  (source: {location.source})")
    print(f"Emergency / child: {location.emergency_number or '-'} / {location.child_helpline or '-'}")
    print("\nAppended to the agent's system prompt:")
    print(build_location_briefing(location).strip())
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--number", help="Caller number in E.164 form, e.g. +2348031234567")
    parser.add_argument("--city", default="", help="Value Twilio would send as FromCity")
    parser.add_argument("--state", default="", help="Value Twilio would send as FromState")
    parser.add_argument("--zip", dest="zip_code", default="", help="Value Twilio would send as FromZip")
    parser.add_argument("--country", default="", help="Value Twilio would send as FromCountry")
    parser.add_argument("--url", help="Base URL of a running server (default: run in-process)")
    args = parser.parse_args()

    if args.number:
        report(
            f"Caller {args.number}",
            {
                "CallSid": "CA" + "0" * 32,
                "From": args.number,
                "To": "+18885550000",
                "FromCity": args.city,
                "FromState": args.state,
                "FromZip": args.zip_code,
                "FromCountry": args.country,
            },
            args.url,
        )
        return

    for sample in SAMPLES:
        payload = {k: v for k, v in sample.items() if k != "label"}
        payload.setdefault("CallSid", "CA" + "0" * 32)
        payload.setdefault("To", "+18885550000")
        report(sample["label"], payload, args.url)


if __name__ == "__main__":
    main()
