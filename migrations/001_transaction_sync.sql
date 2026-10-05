CREATE TABLE toms.accounts (
    account_uid UUID PRIMARY KEY,
    default_category_uid UUID NOT NULL,
    currency TEXT NOT NULL,
    name TEXT,
    account_type TEXT,
    opened_at TIMESTAMPTZ,
    raw_payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE toms.categories (
    account_uid UUID NOT NULL REFERENCES toms.accounts(account_uid),
    category_uid UUID NOT NULL,
    name TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('main', 'savings', 'spending', 'manual')),
    history_from TIMESTAMPTZ,
    history_through TIMESTAMPTZ,
    changes_through TIMESTAMPTZ,
    PRIMARY KEY (account_uid, category_uid)
);

CREATE TABLE toms.transactions (
    account_uid UUID NOT NULL,
    category_uid UUID NOT NULL,
    feed_item_uid UUID NOT NULL,
    amount_minor BIGINT NOT NULL CHECK (amount_minor >= 0),
    currency TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
    status TEXT NOT NULL,
    transaction_time TIMESTAMPTZ NOT NULL,
    source_updated_at TIMESTAMPTZ NOT NULL,
    settlement_time TIMESTAMPTZ,
    source TEXT,
    spending_category TEXT,
    counterparty_name TEXT,
    reference TEXT,
    source_amount_minor BIGINT CHECK (source_amount_minor >= 0),
    source_currency TEXT,
    raw_payload JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_uid, category_uid, feed_item_uid),
    FOREIGN KEY (account_uid, category_uid) REFERENCES toms.categories(account_uid, category_uid)
);
CREATE INDEX transactions_account_time ON toms.transactions(account_uid, transaction_time);
CREATE INDEX transactions_status ON toms.transactions(status);

CREATE TABLE toms.sync_runs (
    run_uid UUID PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    requested_options JSONB NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    pages_committed INTEGER NOT NULL DEFAULT 0,
    items_received INTEGER NOT NULL DEFAULT 0,
    rows_changed INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE toms.sync_targets (
    run_uid UUID NOT NULL REFERENCES toms.sync_runs(run_uid),
    account_uid UUID NOT NULL,
    category_uid UUID NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('history', 'incremental')),
    requested_start TIMESTAMPTZ NOT NULL,
    requested_end TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    pages_committed INTEGER NOT NULL DEFAULT 0,
    items_received INTEGER NOT NULL DEFAULT 0,
    rows_changed INTEGER NOT NULL DEFAULT 0,
    earliest_received TIMESTAMPTZ,
    latest_received TIMESTAMPTZ,
    FOREIGN KEY (account_uid, category_uid) REFERENCES toms.categories(account_uid, category_uid),
    PRIMARY KEY (run_uid, account_uid, category_uid)
);
