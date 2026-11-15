import asyncio
import json

import httpx
import pytest

from harness.tools import PhotonMessaging
from harness.tools import photon_sms
from settings import get_settings


@pytest.fixture(autouse=True)
def env(monkeypatch):
    get_settings.cache_clear()
    photon_sms._SENDERS.clear()
    monkeypatch.setenv("PHOTON_API_KEY", "key")
    monkeypatch.setenv("PHOTON_PROJECT_ID", "pho_prj_test")
    monkeypatch.setenv("PHOTON_SENDER_ID", "")
    monkeypatch.setenv("PHOTON_RESOURCE_ID", "pho_res_line")
    yield
    get_settings.cache_clear()
    photon_sms._SENDERS.clear()


def send(handler, text="hi", recipient="+2348012345678", count=1):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            photon = PhotonMessaging(client)
            return [await photon.send(recipient, text) for _ in range(count)]
    return asyncio.run(go())


class Photon:
    """A small fake of the Photon API that records every request."""

    def __init__(self, line=None, sender_details=None, message_status=202, message_body=None):
        self.requests = []
        self.line = line or {"state": "active", "abilities": ["pstn_voice", "sms"],
                             "detail": {"phoneNumber": "+12406985113", "activationState": "active"}}
        self.sender_details = sender_details if sender_details is not None else {"resourceId": "pho_res_line"}
        self.message_status, self.message_body = message_status, message_body or {"id": "msg_1"}

    def __call__(self, request: httpx.Request):
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path.replace("/v1/projects/pho_prj_test", ""), body, request.headers))
        path = request.url.path
        if request.method == "GET" and path.endswith("/platforms/resources/pho_res_line"):
            return httpx.Response(200, json=self.line)
        if path.endswith("/users"):
            if body["handle"] == "+12406985113":
                return httpx.Response(201, json={"id": "pho_usr_sender", "details": self.sender_details})
            return httpx.Response(200, json={"id": "pho_usr_recipient", "details": {}})
        if path.endswith("/spaces"):
            return httpx.Response(201, json={"id": "space_1"})
        if path.endswith("/messages"):
            return httpx.Response(self.message_status, json=self.message_body)
        return httpx.Response(404)

    def paths(self):
        return [f"{m} {p}" for m, p, _, _ in self.requests]


def test_sender_is_resolved_from_the_resource_id_and_cached():
    photon = Photon()
    first, second = send(photon, count=2)
    assert first == {"status": "submitted", "message_id": "msg_1"} and second == first
    assert photon.paths()[:5] == ["GET /platforms/resources/pho_res_line", "POST /users", "POST /users", "POST /spaces", "POST /messages"]
    assert sum(path.endswith("platforms/resources/pho_res_line") for path in photon.paths()) == 1  # resolved once
    assert photon.requests[1][2] == {"platform": "sms", "handle": "+12406985113"}  # the line's own number
    message = photon.requests[4][2]
    assert message["senderId"] == "pho_usr_sender" and message["spaceId"] == "space_1"
    assert photon.requests[3][2]["participantIds"] == ["pho_usr_sender", "pho_usr_recipient"]


def test_space_and_message_requests_carry_idempotency_keys():
    photon = Photon()
    send(photon, count=2)
    spaces = [h["idempotency-key"] for m, p, _, h in photon.requests if p.endswith("/spaces")]
    messages = [h["idempotency-key"] for m, p, _, h in photon.requests if p.endswith("/messages")]
    assert len(spaces) == 2 and spaces[0] == spaces[1]  # same participants: the same stable key
    assert len(set(messages)) == 2  # each logical text is distinct, so a repeat is never swallowed
    assert all(1 <= len(key) <= 255 and key.isascii() for key in spaces + messages)


def test_an_explicit_sender_id_skips_the_lookup(monkeypatch):
    monkeypatch.setenv("PHOTON_SENDER_ID", "pho_usr_explicit")
    get_settings.cache_clear()
    photon = Photon()
    assert send(photon)[0]["status"] == "submitted"
    assert not any("platforms/resources" in path for path in photon.paths())
    assert photon.requests[-1][2]["senderId"] == "pho_usr_explicit"


@pytest.mark.parametrize("line", [
    {"state": "unallocated", "abilities": ["sms"], "detail": {"phoneNumber": "+12406985113"}},
    {"state": "active", "abilities": ["pstn_voice"], "detail": {"phoneNumber": "+12406985113"}},
    {"state": "active", "abilities": ["sms"], "detail": {}},
])
def test_a_line_that_cannot_send_sms_sends_nothing(line):
    photon = Photon(line=line)
    assert send(photon)[0] == {"status": "unavailable", "reason": "sender_line_not_ready"}
    assert not any(path.endswith("/messages") for path in photon.paths())


def test_never_sends_as_a_line_the_project_does_not_own():
    photon = Photon(sender_details={"resourceId": "pho_res_someone_else"})
    assert send(photon)[0] == {"status": "unavailable", "reason": "sender_not_owned"}
    assert not any(path.endswith("/messages") for path in photon.paths())


def test_unsendable_sender_reports_the_photon_code():
    photon = Photon(message_status=409, message_body={"code": "SENDER_NOT_SENDABLE", "message": "free text is ignored"})
    assert send(photon)[0] == {"status": "unavailable", "reason": "sender_not_sendable", "photon_status": 409,
                               "photon_error": "SENDER_NOT_SENDABLE"}


def test_server_errors_are_unknown_and_never_retried():
    photon = Photon(message_status=503)
    assert send(photon)[0]["status"] == "unknown"
    assert sum(path.endswith("/messages") for path in photon.paths()) == 1


def test_missing_configuration_makes_no_request(monkeypatch):
    monkeypatch.setenv("PHOTON_RESOURCE_ID", "")
    get_settings.cache_clear()
    photon = Photon()
    assert send(photon)[0] == {"status": "unavailable", "reason": "photon_not_configured"}
    assert photon.requests == []
    monkeypatch.setenv("PHOTON_RESOURCE_ID", "pho_res_line")
    get_settings.cache_clear()
    assert send(photon, recipient="not-a-number")[0]["reason"] == "invalid_recipient"
    assert photon.requests == []
