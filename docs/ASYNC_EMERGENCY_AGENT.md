# Async emergency agent deployment

The application runs one per-call Deep Agents harness and delegates emergency
jobs to a standalone Agent Protocol server. The callback executes the authorized
job through the application and returns the outcome to the active call. The
server receives an opaque one-use capability, not caller details or message
text. A shared PostgreSQL job store permits callbacks to reach a different
application worker. Job payloads and results are encrypted with Fernet and
capabilities are stored as SHA-256 hashes.

## Dispatch: the model is only a courier

Every approved action first needs the caller's own clear yes, enforced in the bridge and again in the
tool (`app/voice/consent.py`): consent is **per action**, **single use** and **expires after 3 minutes**,
and a "no" withdraws it and cancels queued jobs.

The Deep Agents model never composes or authorises anything. It only starts the Agent Server run, so a model
outage must not be able to stop an approved text. With `EMERGENCY_DISPATCH=auto` (the code default) the bridge:

1. creates the encrypted job (if the job store is down it sends directly instead);
2. asks the harness model to hand the token to the Agent Server (8 s limit, including waiting for the
   harness lock);
3. waits up to 10 s for the result;
4. otherwise claims and runs the job itself (`execute_job`). The claim is atomic, so if the Agent Server's
   callback got there first the fallback is refused and the bridge keeps waiting for that run's result.
   Nothing can be sent twice.

With `EMERGENCY_DISPATCH=direct` (the Docker Compose default) none of that infrastructure is used and the
app sends the action itself.

## Internal listener

The worker callback (`POST /internal/emergency-job`) and the voice-enrollment reset
(`DELETE /internal/voice-enrollment`) are served by a **second listener** (`app/internal.py`) on
`INTERNAL_PORT` (default 8001, bound to `INTERNAL_HOST`, default `127.0.0.1`). They are not mounted on the public
app that serves Twilio. Compose never publishes port 8001. The routes also require `EMERGENCY_WORKER_SECRET`.
The listener only starts when that secret is at least 32 characters.

## Configuration

Set these environment variables in the deployment's secret manager or `.env`:

- `EMERGENCY_DISPATCH`: `auto` (Agent Server with a direct fallback) or `direct` (never use it).
- `INTERNAL_HOST` / `INTERNAL_PORT`: the internal listener (see above). Never expose it.
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

`docker compose up --build` runs **only the app**. The Agent Server path is behind the `emergency` profile:
`EMERGENCY_DISPATCH=auto docker compose --profile dev --profile emergency up --build` runs the app, its local
application Postgres, the migration, Redis, and the dedicated Agent Server image. It still requires a separately
provisioned `AGENT_DATABASE_URI` and valid Agent Server credentials. None of these services is needed to start
the app, which falls back to sending directly. For a VPS, provide managed or separately operated databases,
configure TLS and access controls, and run the same migration before app
startup. The agent and Redis services have no published host ports. The
`emergency-migrate` service applies the job schema before the app starts. The
Agent Server and the application callback authenticate each other using separate
shared secrets, and neither Agent Server nor Redis publishes a host port.

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
The outbound phone-call tool remains a stub. Test with the actual provider,
responder organization, and relevant safeguarding experts before relying on it
for real emergency response.
