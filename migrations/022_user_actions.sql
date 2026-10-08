-- Successful browser actions only; no passwords, tokens, bank payloads or form bodies.
CREATE TABLE toms.user_actions (
    id UUID PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    action TEXT NOT NULL CHECK (char_length(action) BETWEEN 1 AND 80),
    sample_data BOOLEAN NOT NULL DEFAULT false,
    record_id TEXT NOT NULL DEFAULT '' CHECK (char_length(record_id) <= 160)
);
CREATE INDEX user_actions_recent ON toms.user_actions (occurred_at DESC, id DESC);
REVOKE ALL ON TABLE toms.user_actions FROM PUBLIC, toms_app;
GRANT SELECT, INSERT ON TABLE toms.user_actions TO toms_app;
