-- Details belong to each invoice and remain fixed after issue.
ALTER TABLE toms.invoices
    ADD COLUMN business_name TEXT NOT NULL DEFAULT '' CHECK (char_length(business_name) <= 200),
    ADD COLUMN business_address TEXT NOT NULL DEFAULT '' CHECK (char_length(business_address) <= 2000),
    ADD COLUMN business_contact TEXT NOT NULL DEFAULT '' CHECK (char_length(business_contact) <= 500),
    ADD COLUMN payment_instructions TEXT NOT NULL DEFAULT '' CHECK (char_length(payment_instructions) <= 2000);

CREATE TABLE toms.invoice_deliveries (
    id UUID PRIMARY KEY,
    invoice_id UUID NOT NULL REFERENCES toms.invoices(id),
    sent_at TIMESTAMPTZ NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('email', 'post', 'hand', 'other')),
    recipient TEXT NOT NULL CHECK (char_length(trim(recipient)) BETWEEN 1 AND 500),
    notes TEXT NOT NULL DEFAULT '' CHECK (char_length(notes) <= 1000),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX invoice_deliveries_invoice ON toms.invoice_deliveries (invoice_id, sent_at);
GRANT SELECT, INSERT ON TABLE toms.invoice_deliveries TO toms_app;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE toms.invoice_deliveries FROM toms_app;
