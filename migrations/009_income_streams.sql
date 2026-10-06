-- Streams are created explicitly by the owner; no starter records are seeded.
CREATE TABLE toms.income_streams (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL CHECK (char_length(trim(name)) BETWEEN 1 AND 200),
    kind TEXT NOT NULL CHECK (kind IN ('self_employed', 'employed', 'cis')),
    archived BOOLEAN NOT NULL DEFAULT FALSE
);
ALTER TABLE toms.transaction_income ADD COLUMN income_stream_id UUID
    REFERENCES toms.income_streams(id);
CREATE INDEX income_stream_payments ON toms.transaction_income(income_stream_id);
