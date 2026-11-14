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

### Child-support knowledge and memory (review required)

The optional Twilio support harness runs a background coordinator (`harness/coordinator.py`). After every turn the bridge hands it the transcript without waiting; it triages topic and risk, retrieves reviewed corpus chunks (when `DATABASE_URL` and `COHERE_API_KEY` are set; otherwise skills only), applies the bundled skills, and publishes structured guidance (stance, do, avoid, next move, source references). Gemini pulls the newest guidance with `get_support_guidance`, which returns instantly with `turns_behind` and `stale` flags, so the coordinator never slows the call. Retrieval uses only a fixed set of topic queries; caller words are not sent to Cohere (they are sent to Gemini, as in the call itself). Use `COORDINATOR_MODEL` to pick its model. The corpus is declared in `harness/corpus/sources.json` (Markdown file plus metadata). On each start the sync computes a content digest per source, combined with the chunking/embedding pipeline signature, and compares it with the stored one: unchanged sources are skipped with no chunking or Cohere calls; changed ones are re-chunked and re-embedded in one transaction. Sources ship `review_status: pending` and `rights_verified: false`, so nothing is ingested until a person reviews the licence and clinical content and flips both fields; setting a source back to pending withdraws it from retrieval on the next start. Syncing is best-effort (a Cohere outage is logged and does not block startup; use `--strict` to fail instead). `python -m harness.corpus.ingest --draft-from-md <md> out.json` drafts a manifest entry from a Markdown file.

1. Use a PostgreSQL instance with pgvector installed; the `dev` Compose database uses `pgvector/pgvector:pg17`. Set `DATABASE_URL`. With Docker Compose, the `support-init` service runs before the backend on every start: it applies `migrations/001_support_rag.sql` (idempotent, must succeed) and then syncs corpus chunks (`python -m scripts.sync_support`; outside Compose run the same command with `DATABASE_URL` set, or `python scripts/migrate_support.py` for the schema only).
2. `harness/corpus/sources.pending.json` lists candidate sources and draft paraphrases, not an approved corpus. Have an appropriate child-safeguarding specialist review source text, page references, suitability, and reuse rights. Replace the draft sections with reviewed text and set each source's `review_status` to `approved` and `rights_verified` to `true` only after that review. The WHO handbook's license is noncommercial and share-alike. Ingest the reviewed JSON with `python -m harness.corpus.ingest path/to/reviewed.json`. The command embeds text through Cohere and requires `COHERE_API_KEY`.
3. After clinical, identity, and provider-data reviews, set `SUPPORT_GUIDANCE_ENABLED=true` and/or `SUPPORT_MEMORY_ENABLED=true`. Both default to false. Memory requires `SUPPORT_MEMORY_ENCRYPTION_KEY` (a Fernet key) and a separate random `SUPPORT_MEMORY_IDENTITY_KEY`; protect both outside the database. Generate a Fernet key with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. Set `SUPPORT_MEMORY_RETENTION_DAYS` from 1 to 365 (default 30).

Memory is built as the call progresses: the coordinator proposes updates and they are held in RAM, then saved (encrypted) only once the caller agrees to memory, and re-saved as more is learned. It stores only a fixed vocabulary: speaking pace, breathing preference, and helpful/avoided approaches (`harness/support.py`). It does not summarize abuse or record free-form history. Consent and deletion recognition are intentionally narrow; verify the call experience with safeguarding specialists before enabling. Expired memory is purged at the next memory-enabled call; schedule `SupportMemoryStore.purge_expired()` separately if deletion must occur without later calls. Audit entries contain only an HMAC caller key, action, and timestamp; set an organizational audit retention policy. Back up and rotate encryption and identity keys under an operational key-management procedure; losing or changing them without a migration makes existing memory inaccessible.

The token-aware splitter downloads the `cl100k_base` tokenizer data on first use; pre-cache it in offline deployments. The semantic chunker is supplied by the archived `langchain-experimental` package; keep it isolated to offline ingestion and review its dependency security before deployment.
