# Agent guidelines

## Rules

- **Tool definitions live in `harness/tools/`, each in its own file.** This covers Gemini Live function declarations (see `harness/tools/declarations.py`), Deep Agents/LangChain tool implementations, and any helper that defines what the model may call. Do not define tools inline in `app/` or elsewhere; import them from `harness.tools`. Handlers that need call state (for example `relay_to_harness`) may stay on the call bridge, but their declarations must be in `harness/tools/`.
- Run the tests with `.venv/bin/python -m pytest -q`.
- **Keep `.env.example` current.** Whenever you add, rename or remove an environment variable (in `settings.py`, `docker-compose.yml`, or anywhere code reads the environment), update `.env.example` in the same change. Include every variable needed for setup, with a short comment on where to get it or how to generate it, and use safe placeholder or default values. Never put real secrets in it.
