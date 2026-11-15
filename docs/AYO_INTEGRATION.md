# Ayo integration into Fortune's pipeline: study and plan

*Branch `ayo-integration` from `origin/main` @ `26b1048` (PR #8). Written Nov 14 2026.*

## Status (Nov 14 2026)

**Built and tested (107 passed, 3 skipped):**
- OpenAI Realtime + Bukola engine behind `VOICE_ENGINE=openai` (`app/voice/openai_realtime.py`), tool converter, settings, `.env.example`, Compose.
- Ayo's prompt in `app/prompts/ayo_prompt.md` (public, Naomi's call Nov 14; `AYO_PROMPT_PATH` overrides), with Ayo's `{RUNTIME_CONTEXT}` slot filled by the caller briefing; consent sentence untouched.
- Bukola greeting from `AYO_GREETING_AUDIO` plays at stream start (Twilio `<Say>` dropped when set).
- Caller audio spoken during the voice check is replayed to the engine so Ayo answers the first words.
- Silence check-in (8 s, once per silence) in the engine; `[softly]`/`[whispers]` exempted from the prompt's "no brackets" rule.
- Branding renamed to Ayo (title, root, logs `ayo.*`, fallback prompt). DB, Compose service and env names unchanged.
- `scripts/live_engine_check.py`: runs a recorded clip through the real bridge prompt + resampler.

- **Voice check no longer hangs up** (Nov 14, Naomi: voice is the identity layer). `verify_call()` sets `identity_state`: `known` (matched), `new` (just enrolled), `unknown` (different voice), `unsure` (too little speech within 12 s, database down, check failed). Only a forged caller token or a hang-up before the stream starts ends the call. Nothing acts on the state yet (no questions, no alerts, Ayo is not told). Memory still only reads for `matched`.

**Still open (needs Naomi):** helplines (kept as is, her call), what Ayo and the alert path do with `unknown`/`unsure` (four-state flow, safe facts, human-first alerts), swap ECAPA for the `voice-id` lane, Nigerian line (Sabi's register handoff + flash callback), `test_async_emergency.py` skips (needs `TEST_EMERGENCY_DATABASE_URL`, an isolated Postgres).

*Original plan below (written before the code).*

**Goal:** turn this pipeline into **Ayo** without breaking any existing behaviour or tests:
- **OpenAI realtime** (`gpt-realtime-2.1`, text mode) as the ears and brain, because it hears tone
- **ElevenLabs Bukola** (`eleven_v4_turbo`) as the voice
- Ayo's prompt, greetings and voice settings

## 1. How the pipeline works today (from the code)

| Step | Where | What happens |
|---|---|---|
| 1 | `app/main.py` `voice_incoming` | Twilio webhook. Validates `X-Twilio-Signature` (when `TWILIO_AUTH_TOKEN` is set), signs the caller number into `caller_token` (`harness/caller_identity.py`, HMAC, 300 s), adds location `<Parameter>`s (`app/voice/caller_location.py`), returns TwiML: **`<Say>` "We use your voice to recognize you on future calls. Please speak for a few seconds."** then `<Connect><Stream>`. |
| 2 | `twilio_stream.py` `run()` → `verify_call()` | Reads Twilio events **before any AI is connected**. On `start`: location + verified caller number. On `media`: collects 5 s of voiced speech (`harness/call_audio.py`, webrtcvad, 30 s limit). Then embeds it (ECAPA, `harness/tools/verify_voice.py`) through a Deep Agents call (`harness/agent.py`) against Postgres (`harness/datastore/voice.py`). **Anything other than `matched`/`enrolled` raises, and the call is closed.** |
| 3 | `_setup_support()` | Optional memory and coordinator (`SUPPORT_MEMORY_ENABLED` / `SUPPORT_GUIDANCE_ENABLED`, both default false). |
| 4 | `_connect_gemini()` | Builds `LiveConfig(system_prompt=build_system_prompt(), voice=GEMINI_VOICE ("Puck"), input_rate=8000, tools=[RELAY_DECLARATION (+ SUPPORT_GUIDANCE_DECLARATION)])` and opens `GeminiLiveSession`. |
| 5 | main loop | Twilio `media` → `mulaw_to_pcm16` → `gemini_session.send_audio(pcm16 @ 8 kHz)`. |
| 6 | `GeminiLiveSession.receive_forever()` | Audio parts → `await on_audio(pcm 24 kHz)` → bridge queue → `Downsample24kTo8k` → `pcm16_to_mulaw` → 20 ms frames to Twilio. `interrupted` → `on_interrupted()` (clears Twilio playback). `turnComplete` → `on_turn_complete(caller_text, model_text)`. `toolCall` → `on_tool_call(name, args)` → `toolResponse`. |
| 7 | `_on_turn_complete` | **Consent logic depends on transcripts.** It sets `consent_question_pending` when Ayo's text contains "may i text the emergency responder with what you shared". The next caller turn must be exactly one of "yes", "yes please", "yes you may", "yes i agree", "i agree", "please do". Memory consent works the same way. It also feeds the coordinator. |
| 8 | `relay_to_harness` | Tool. Checks consent, creates an encrypted one-use job in Postgres, and a Deep Agents async subagent → Agent Server → `/internal/emergency-job` → Photon SMS. It immediately returns `{"status":"pending"}`. The final result is injected with `gemini_session.send_text(...)`. |

`AudioSocket` (Asterisk / Africa's Talking) **hangs up every call**: it has no trusted caller number for voice verification. So this pipeline is **Twilio-only** today.

## 2. The seam: one class swap

The bridge only ever touches the model through this interface:

```
Session(cfg: LiveConfig, *, on_audio, on_interrupted, on_turn_complete, on_tool_call)
  .connect()                    .send_audio(pcm16_8k)      .send_text(text)
  .receive_forever()            .end_audio()               .close()
on_audio(pcm16_24k) · on_interrupted() · on_turn_complete(caller_text, model_text) · on_tool_call(name, args) -> dict
```

**Plan:** add `app/voice/openai_brain.py` with `OpenAIRealtimeSession`, which has **exactly this interface**. `GeminiLiveSession` stays untouched, and a setting picks which one runs. Nothing else in the bridge needs to change for the basic swap.

### What happens inside `OpenAIRealtimeSession` (checked with live probes Nov 14)

**Ears**
- `send_audio(pcm16 8k)` → `pcm16_to_mulaw` (already in `app/voice/audio.py`) → `input_audio_buffer.append` as `audio/pcmu`. OpenAI accepts mu-law natively.
- Turn detection: OpenAI `server_vad`, threshold 0.4, 1200 ms silence. This was tuned on Naomi's clips, so crying pauses aren't cut off, and replies came 0.7–1.3 s after the child stopped.
- Input transcription: `gpt-4o-mini-transcribe`.

**Brain**
- `output_modalities: ["text"]`, model `gpt-realtime-2.1`.

**Voice**
- Each finished sentence of the reply is sent to ElevenLabs Bukola with `output_format=pcm_24000`, streamed, and passed to `await on_audio(pcm24k)`.
- ✅ Probe: Bukola returns 24 kHz PCM. **The bridge's resampler and Twilio framing stay as they are.**

**Barge-in**
- On `input_audio_buffer.speech_started` while Ayo is speaking:
  1. stop the TTS stream
  2. send `response.cancel`
  3. call `await on_interrupted()` (the bridge already clears Twilio)

**Tools**
- Fortune's Gemini declarations are converted to OpenAI's format (lower-case JSON-schema types).
- On `response.function_call_arguments.done`, call `await on_tool_call(name, args)`, then send `function_call_output` and `response.create`.
- ✅ Probe: OpenAI accepted both converted tools and called `relay_to_harness {"action":"request_location"}` in text mode.

**Transcripts for consent and memory**
- `on_turn_complete(caller_text, model_text)` fires at `response.done`.
- In OpenAI, the caller transcription arrives separately (`conversation.item.input_audio_transcription.completed`, keyed by `item_id`), so it can arrive late.
- The session will wait briefly (up to about 1.5 s) for the transcript of the committed item before firing. **Otherwise the consent "yes" could be attached to the wrong turn.**

**Injected status**
- `send_text(text)` (the emergency result) becomes a `system` message item plus `response.create`, the same effect as Gemini's `clientContent` with `turnComplete`.

## 3. Ayo's prompt, greeting and voice

**Prompt**
- `app/prompts/sabi_safe_prompt.md` → Ayo's prompt (`thegirwhocodes/ayo` `config/ayo_prompt.md`).
- `build_system_prompt()` appends three things to it:
  - the location briefing
  - the support/memory instructions
  - the relay/consent block, which includes the exact consent sentence. **That must stay word-for-word, or the consent check breaks.**
- Ayo's `{RUNTIME_CONTEXT}` placeholder gets filled by that location briefing.

**Voice style**
- Ayo's brain hears the crying or whispering, so it can put `[softly]` / `[whispers]` at the start of a reply, which v4 Turbo performs.
- The session strips those tags from the transcript, so the consent and memory text matching isn't affected.

**Greeting**
- The robotic `<Say>` is replaced with Ayo's pre-recorded greeting in Bukola's voice, sent as Twilio media when the stream starts. It sits in `config/`, with the calm settings stability 0.7 and speed 0.92.

**First words**
- During `verify_call()` the child is talking, but no AI is connected yet.
- The bridge will **keep that audio and replay it to Ayo** once she connects, so she answers the child's first words instead of leaving a silence.

## 4. Things that would break or behave badly (to handle on purpose)

1. **The voice-verification gate hangs up** on a failed or unclear match, too little speech (whispering, crying, silence), a missing database, or a shared phone (siblings).
   - "Without breaking the code" means it stays as is for now.
   - ❓ But for a child line this is the biggest risk in the pipeline. See the decisions below.
2. **Postgres is required** for every call (voice enrolment), plus Agent Server, Redis and a LangSmith licence for emergency jobs. This Mac has no Docker and no Postgres, so a local test call needs one of:
   - a local Postgres
   - an opt-in dev switch that skips verification (default stays "on")
3. **The US "child helpline" in `caller_location.py` is 988.** That's the suicide line. The child-abuse line is Childhelp 1-800-422-4453.
   - Ayo's prompt has the correct number.
   - Fixing the table also means updating `test_hotline_routing_by_country`, which asserts 988.
4. **The voice is set by `GEMINI_VOICE` ("Puck")** and is passed into `LiveConfig.voice`. It's irrelevant to the OpenAI path, because Bukola is set by her own settings.
5. **AGENTS.md rules to follow:**
   - Tool definitions stay in `harness/tools/`, so the OpenAI converter goes in `harness/tools/openai_format.py`.
   - `.env.example` must be updated in the same change: `VOICE_ENGINE`, `OPENAI_API_KEY`, `OPENAI_REALTIME_MODEL`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL`.
   - Tests run with `.venv/bin/python -m pytest -q`.
6. **Two voice-ID systems now exist:** this repo's ECAPA (speechbrain) and Naomi's `voice-id` (ReDimNet2+, already an Ayo lane). Pick one later. Naomi will walk through this.

## 5. Renaming to Ayo (proposed)

- **Rename now, safely:**
  - README
  - FastAPI title, description and root text
  - the fallback prompt text
  - the prompt file
  - log names `sabi.*` → `ayo.*`
- **Leave unchanged for now, because changing them could break running setups:**
  - database name, user and password (`sabi_safe`, `sabi`)
  - Compose service and container names
  - env var names
- **Rename later, together with Fortune and Hanif.**

## 6. Order of work (after the decisions below)

1. Baseline: install the test dependencies and run the existing suite on untouched code, then record pass/fail.
2. Add `openai_brain.py`, `harness/tools/openai_format.py`, settings, `.env.example` and Compose env. New tests: interface parity, tool conversion, the transcript-pairing rule, barge-in.
3. Ayo prompt, greeting and first-word replay. Rename the branding.
4. Re-run the full suite and confirm it matches the baseline plus the new tests passing. Run Naomi's 5 clips through the bridge's interface.
5. Open a PR to `main` for Fortune and Hanif to review. Don't push to `main` directly.
