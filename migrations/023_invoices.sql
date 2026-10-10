CREATE TABLE toms.invoices (
    id UUID PRIMARY KEY,
    income_stream_id UUID NOT NULL REFERENCES toms.income_streams(id),
    stream_name TEXT NOT NULL,
    number TEXT UNIQUE,
    state TEXT NOT NULL DEFAULT 'draft' CHECK (state IN ('draft','issued','cancelled')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    client_name TEXT NOT NULL CHECK (char_length(trim(client_name)) BETWEEN 1 AND 200),
    client_address TEXT NOT NULL DEFAULT '' CHECK (char_length(client_address) <= 2000),
    reference TEXT NOT NULL DEFAULT '' CHECK (char_length(reference) <= 200),
    issue_date DATE NOT NULL,
    due_date DATE NOT NULL CHECK (due_date >= issue_date),
    currency TEXT NOT NULL CHECK (currency IN ('GBP','EUR','USD')),
    total_minor BIGINT NOT NULL CHECK (total_minor > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    issued_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    CHECK (state <> 'draft' OR (number IS NULL AND issued_at IS NULL)),
    CHECK (state <> 'issued' OR (number IS NOT NULL AND issued_at IS NOT NULL)),
    CHECK (state <> 'cancelled' OR cancelled_at IS NOT NULL)
);
CREATE INDEX invoices_recent ON toms.invoices (created_at DESC, id);
CREATE INDEX invoices_stream ON toms.invoices (income_stream_id);
CREATE TABLE toms.invoice_items (
    id UUID PRIMARY KEY,
    invoice_id UUID NOT NULL REFERENCES toms.invoices(id) ON DELETE CASCADE,
    shift_id UUID REFERENCES toms.income_shifts(id) ON DELETE SET NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    description TEXT NOT NULL,
    amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
    source_version TEXT NOT NULL,
    shift_snapshot JSONB NOT NULL,
    CHECK (NOT active OR shift_id IS NOT NULL)
);
CREATE INDEX invoice_items_invoice ON toms.invoice_items (invoice_id);
CREATE UNIQUE INDEX invoice_items_active_shift ON toms.invoice_items (shift_id) WHERE active;
CREATE TABLE toms.invoice_payments (
    id UUID PRIMARY KEY,
    invoice_id UUID NOT NULL REFERENCES toms.invoices(id),
    account_uid UUID NOT NULL,
    category_uid UUID NOT NULL,
    feed_item_uid UUID NOT NULL,
    cash_minor BIGINT NOT NULL CHECK (cash_minor >= 0),
    tax_minor BIGINT NOT NULL DEFAULT 0 CHECK (tax_minor >= 0),
    ni_minor BIGINT NOT NULL DEFAULT 0 CHECK (ni_minor >= 0),
    payment_version TEXT NOT NULL,
    payment_label TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (account_uid,category_uid,feed_item_uid) REFERENCES toms.transactions(account_uid,category_uid,feed_item_uid),
    CHECK (cash_minor::numeric + tax_minor::numeric + ni_minor::numeric > 0)
);
CREATE INDEX invoice_payments_invoice ON toms.invoice_payments (invoice_id);
CREATE INDEX invoice_payments_transaction ON toms.invoice_payments (account_uid,category_uid,feed_item_uid);
CREATE TABLE toms.invoice_sequences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_number BIGINT NOT NULL CHECK (last_number >= 0)
);
