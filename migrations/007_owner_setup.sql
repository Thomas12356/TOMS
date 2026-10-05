-- Expiring, hashed token for the single-owner first-run form.
CREATE TABLE toms.owner_setup (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
