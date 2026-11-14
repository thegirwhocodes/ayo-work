CREATE TABLE IF NOT EXISTS emergency_jobs (
    token_hash text PRIMARY KEY,
    session_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('send_text_message', 'request_location', 'start_phone_call')),
    payload bytea NOT NULL,
    state text NOT NULL CHECK (state IN ('pending', 'processing', 'finished', 'cancelled')),
    result bytea,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS emergency_jobs_session_state_idx ON emergency_jobs (session_id, state);
CREATE INDEX IF NOT EXISTS emergency_jobs_expiry_idx ON emergency_jobs (expires_at);
