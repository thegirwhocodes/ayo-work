# Async emergency agent deployment

The application runs one per-call Deep Agents harness and delegates emergency
jobs to a standalone Agent Protocol server. The callback executes the authorized
job through the application and returns the outcome to the active call. The
server receives an opaque one-use capability, not caller details or message
text. A shared PostgreSQL job store permits callbacks to reach a different
application worker. Job payloads and results are encrypted with Fernet and
capabilities are stored as SHA-256 hashes.

## Configuration

Set these environment variables in the deployment's secret manager or `.env`:

- `DATABASE_URL`: application PostgreSQL database, reachable from the app and
  `emergency-migrate` service.
- `EMERGENCY_JOB_ENCRYPTION_KEY`: Fernet key. Generate with
  `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`.
- `EMERGENCY_WORKER_SECRET`: an independent random secret of at least 32
  characters. Generate with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`.
- `AGENT_SERVER_AUTH_SECRET`: a different random secret of at least 32
  characters for the app's Agent Protocol requests.
- `AGENT_DATABASE_URI`: a **separate** PostgreSQL database for Agent Server's
  threads, runs, and checkpoints. Do not share a database with another Agent
  Server deployment.
- `LANGSMITH_API_KEY` and, when required by your deployment,
  `LANGGRAPH_CLOUD_LICENSE_KEY`: Agent Server credentials. Check your LangSmith
  license and egress requirements.
- `AGENT_REDIS_URI`: Redis URI; Compose defaults to the private `agent-redis`
  service. Use a dedicated Redis database for this deployment.

The existing Gemini, Twilio, Photon and responder configuration is still
required. Keep secrets out of source control; use separate keys per environment.
Losing or rotating the Fernet key without a migration makes outstanding jobs
unreadable. Keep tracing disabled unless its handling of capabilities and
sensitive call context has been reviewed.

The application process never sends traces: `harness/tracing.py` switches LangSmith tracing off at startup, and
the case manager traces only for `scripts/trace_synthetic_call.py`, which uses an invented conversation. This
document's Agent Server runs in its own container, so its tracing is controlled by that container's
`LANGSMITH_TRACING` setting.

`docker compose --profile dev up --build` runs the app, its local application
Postgres, the migration, Redis, and the dedicated Agent Server image. It still
requires a separately provisioned `AGENT_DATABASE_URI` and valid Agent Server
credentials. For a VPS, provide managed or separately operated databases,
configure TLS and access controls, and run the same migration before app
startup. The agent and Redis services have no published host ports. The
`emergency-migrate` service applies the job schema before the app starts. The
Agent Server and the application callback authenticate each other using separate
shared secrets, and neither Agent Server nor Redis publishes a host port.

## Escalation plan

The Agent Server graph (`harness/emergency_subagent.py`, three nodes visible in LangGraph Studio) does not stop
after one action:

1. `first_action` runs the action the voice agent asked for straight away, with **no model call**.
2. `plan_escalation` is a Deep Agent that writes a plan (`write_todos`) and follows the ladder in
   `harness/emergency_plan.py`: if the first channel was not accepted it tries the other one (SMS or phone call),
   then texts the backup responder (`EMERGENCY_BACKUP_RESPONDER_NUMBER`, optional), then waits for the responder
   to acknowledge, has the voice agent check in with the child after `CHECK_IN_AFTER_SECONDS` (180) without an
   acknowledgement, and at the end of `PLAN_WINDOW_SECONDS` (600) has it point the child at the local emergency
   number. Afterwards a deterministic floor (`run_floor`) completes whatever the ladder still calls for, so a
   model outage or a model that stops early never costs the child the next channel.
3. `finish_job` closes the job and stores the combined outcome.

What crosses the model boundary is closed on purpose:

- The application tells a running job only `responder_acknowledged` or `location_received`
  (`harness.emergency_jobs.post_job_event`). The agent reads them with the `wait_for_events` tool through the
  authenticated callback; events are stored encrypted with the job and delivered once. No caller or responder
  words reach the model.
- The agent can ask the voice agent only for `check_in`, `call_emergency_number` or `responder_unreachable`. The
  application words each note (`VOICE_NOTE_TEXT` in `app/voice/twilio_stream.py`); none lets the voice agent
  promise that help is coming. Each note is passed on once per job.
- Each action runs at most once per job, consent is enforced by the application, and the agent cannot write or
  change the message. The built-in file tools are denied to it.

The job capability now lives for the plan window plus two minutes (it used to be two minutes in total). While
the plan runs, the application polls the job and tells the voice agent about each action outcome and each note
as it appears. If the agent server never runs the first action within 90 seconds the job is given up on and
cancelled, as before. Ending the call or withdrawing consent cancels the job, which stops the escalation.

Known limits: nothing in the application produces `responder_acknowledged` or `location_received` yet, because
inbound SMS replies are not processed. Until they are, the agent never sees an acknowledgement, so it will check
in at three minutes and point the child at the local emergency number at ten, which is the conservative
behaviour. If the planning agent fails after a responder channel was accepted, the floor does not wait or
check in. The Agent Server image now installs `deepagents` (pin `>=0.7.38,<0.8`) in addition to the
packages it had; rebuild it.

## Delivery and safety properties

A database-atomic claim prevents concurrent workers from executing the same job
twice. Caller consent is checked before creating a responder job; pending jobs
are cancelled when consent is withdrawn or the call ends. A request already
claimed or submitted to Photon cannot reliably be recalled. A process crash or
timeout after claim may leave the outcome unknown; this system does not retry
that send automatically or claim it was delivered. Completed and cancelled job
payloads are erased; expired records are purged by an application background
loop. A supervisor model or server failure is reported as unavailable/unknown.

Compose on a single VPS does not itself provide high availability, backups,
disaster recovery, zero-downtime draining, delivery receipts, or guaranteed
response. LangChain's tested production deployment path is its maintained
Kubernetes Helm chart; operating standalone Compose requires independently
managing these gaps. Do not treat an accepted SMS as verified emergency dispatch.
Test the outbound phone-call and SMS tools with the actual providers,
responder organization, and relevant safeguarding experts before relying on it
for real emergency response.
