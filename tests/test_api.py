import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "sabi-safe"


def test_twilio_voice_incoming():
    response = client.post("/voice/incoming", headers={"host": "api.sabi-safe.org"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    xml_content = response.text
    assert "<Connect>" in xml_content
    assert '<Stream url="ws://api.sabi-safe.org/ws/twilio"' in xml_content or '<Stream url="wss://' in xml_content
