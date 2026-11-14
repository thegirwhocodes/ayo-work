# Local async emergency subagent

The FastAPI app owns each call's emergency context and runs the emergency tools.
The main Deep Agents harness launches an `emergency` async subagent through a
local Agent Protocol server. The server graph receives only an opaque, one-use
job capability; it calls back to the application, which rechecks the caller's
consent and runs the requested tool. The app sends the actual result to Gemini
Live as a later message. Phone numbers, message text, and consent are not passed
in the subagent task description.

For local development, install `requirements.txt` and
`requirements-agent-server.txt`, then run both processes on the same host:

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/bin/langgraph dev --config langgraph.json --host 127.0.0.1 --port 2024 --no-browser --n-jobs-per-worker 10
```

Set `AGENT_SERVER_URL` if the Agent Protocol server is on a different local
port, and `EMERGENCY_CALLBACK_URL` for the app callback if needed. Both default
to loopback URLs. The callback only accepts a one-use capability and only from
loopback. Run a single application worker: active job capabilities live in
that process's memory and are lost on restart. Disable external tracing for
these processes unless its handling of short-lived capabilities is approved.

`langgraph dev` is an in-memory development server, not a production server.
For a VPS deployment, use a production Agent Protocol server with authentication,
private networking, durable infrastructure, and a shared authorized job store;
the development configuration does not provide those guarantees. The outbound
phone-call tool is still a stub. A pending or submitted result is not proof of
delivery or dispatch.
