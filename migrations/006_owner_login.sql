-- One owner for this personal application; no public registration.
CREATE TABLE toms.owner_login (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE toms.browser_sessions (
    token_hash TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
-- A global limit works even behind Serve, where requests share a proxy address.
CREATE TABLE toms.login_attempts (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    window_started_at TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL
);
