# Sabi Safe 🛡️📞

A clean, modular rebuild of the **Sabi full-duplex telephony voice stack**, purpose-built for **trauma-informed youth support, emotional grounding, and crisis triage**.

This repository contains the full production telephony architecture of Sabi—free of educational curricula—paired with clinical psychological principles (Psychological First Aid, NICHD Child Forensic Interview Protocol) and low-latency bidirectional voice AI.

---

## ⚡ Key Capabilities

- 📞 **Dual-Protocol Telephony Support:**
  - **Twilio Media Streams** (`/ws/twilio`): Cloud WebSocket audio streaming over standard phone numbers.
  - **Asterisk AudioSocket** (TCP `9020`): Low-latency native telephony protocol for GSM networks (e.g. Africa's Talking).
- 🎙️ **Full-Duplex Gemini Live Streaming:** Real-time conversational voice via Google Gemini Live API with instant barge-in / interruption handling.
- 🎛️ **Fast Audio Codec & Resampling:** In-memory G.711 $\mu$-law encoder/decoder and stateful 3:1 decimator (24 kHz $\rightarrow$ 8 kHz) with zero external C dependencies.
- 🧠 **Trauma-Informed Voice Prompting:** Rooted in Bruce Perry's Neurosequential Model (*Regulate $\rightarrow$ Relate $\rightarrow$ Reason*), open invitations, and de-blaming.
- 🛡️ **Defense-in-Depth Guardrails:** Hard short-circuiting for abuse disclosures, self-harm, and immediate physical peril, routing to verified Nigerian (112, Cece Yara 0800 800 8001) and international (988) emergency services.
- 🚫 **No False Secrecy:** Transparent, compassionate boundary setting that protects youth without mimicking predatory secrecy.

---

## 🚀 Quick Start

### 1. Installation

```bash
git clone <repo-url>
cd sabi-safe

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Environment Variables

Create `.env` based on `.env.example`:

```bash
cp .env.example .env
```

Set your `GEMINI_API_KEY`:
```ini
GEMINI_API_KEY="your-gemini-api-key"
GEMINI_VOICE="Puck"
```

### 3. Run the Server

```bash
# Start FastAPI and the background AudioSocket server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

For the local Compose setup, set `GEMINI_API_KEY` and `TWILIO_AUTH_TOKEN` in
`.env`, then run `docker compose --profile dev up --build`. This starts the app
and a Postgres container with a persistent development volume. For a local
non-Compose server, set `DATABASE_URL` to a PostgreSQL connection string.

The Twilio webhook validates the request signature and passes a short-lived
signed caller number to the media stream. At call start, the service collects
five seconds of detected speech (up to 30 seconds), then uses the Deep Agents
harness to enroll the first embedding for that number or compare subsequent
calls. A failed match, insufficient speech, missing identity, or verification
error ends the call. AudioSocket calls currently end because that protocol
does not provide the persistent caller number required by this flow.

`VOICE_VERIFICATION_THRESHOLD` defaults to 0.75 for demonstration only; it
must be calibrated on representative telephone recordings before relying on
matches. Phone numbers and voice embeddings are stored in Postgres and a phone
number or voice match is not proof of identity. The development database uses
local-only example credentials and is not a production data protection setup.
The first call creates an enrollment without independently proving identity.

The Deep Agents harness bundles the Empathy and CBT skills under
`harness/skills/`. Their descriptions are discoverable by the harness and their
instructions and reference documents are loaded on demand. The harness currently
handles voice verification; Gemini Live handles the separate spoken conversation,
so these skills do not yet guide the caller-facing voice agent.

Emergency SMS uses Photon 2.0. Set `PHOTON_API_KEY`, `PHOTON_PROJECT_ID`,
`PHOTON_SENDER_ID` (a project-owned SMS agent User ID), and
`EMERGENCY_RESPONDER_NUMBER` (E.164). A responder text requires the caller's
clear agreement to the agent's permission question. The location request texts
the caller, but inbound SMS replies are not processed. Photon acceptance is not
proof of delivery, and outbound calls are not implemented. The consent check is
conservative and depends on speech transcription; unclear answers require the
caller to repeat their agreement. Confirm that the configured recipient can
actually receive and act on SMS before relying on this integration.

---

## 🧪 Running Tests

```bash
pytest tests
```

Output:
```
tests/test_api.py ..                                                     [ 18%]
tests/test_audio.py ....                                                 [ 54%]
tests/test_guardrails.py .....                                           [100%]
======================== 11 passed in 0.17s =========================
```

---

## 📖 Documentation

- [Telephony Architecture & Protocol Reference](docs/ARCHITECTURE.md)
- [Trauma-Informed Voice Care (PFA & Neurosequential Model)](docs/psychology/TRAUMA_INFORMED_CARE.md)
- [NICHD Protocol & Child Safeguarding Standards](docs/psychology/NICHD_PROTOCOL_GUIDE.md)

---

## 📄 License
MIT License
