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
