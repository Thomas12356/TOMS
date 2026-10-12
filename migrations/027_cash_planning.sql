-- Independent business tables: preserve existing real and sample records.
CREATE TABLE toms.cash_commitments (
 id UUID PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('bill','hmrc')),
 name TEXT NOT NULL CHECK(char_length(name) BETWEEN 1 AND 200),
 currency TEXT NOT NULL CHECK(currency ~ '^[A-Z]{3}$'), amount_minor BIGINT CHECK(amount_minor >= 0),
 due_date DATE NOT NULL, recurrence TEXT NOT NULL CHECK(recurrence IN ('once','weekly','monthly','yearly')),
 end_date DATE CHECK(end_date IS NULL OR end_date >= due_date),
 tax_year TEXT, tax_component TEXT,
 notes TEXT NOT NULL DEFAULT '', active BOOLEAN NOT NULL DEFAULT TRUE,
 revision BIGINT NOT NULL DEFAULT 1 CHECK(revision > 0),
 CHECK(kind = 'bill' OR (recurrence = 'once' AND currency = 'GBP' AND tax_year IS NOT NULL AND tax_component IS NOT NULL AND tax_year ~ '^[0-9]{4}-[0-9]{2}$'
  AND tax_component IN ('balancing','first_on_account','second_on_account','other'))),
 CHECK(kind = 'hmrc' OR (tax_year IS NULL AND tax_component IS NULL))
);
CREATE TABLE toms.cash_payments (
 id UUID PRIMARY KEY, commitment_id UUID NOT NULL REFERENCES toms.cash_commitments(id),
 occurrence_date DATE NOT NULL, amount_minor BIGINT NOT NULL CHECK(amount_minor > 0),
 paid_on DATE NOT NULL, reference TEXT NOT NULL DEFAULT '', active BOOLEAN NOT NULL DEFAULT TRUE,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(), changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX cash_payments_commitment ON toms.cash_payments(commitment_id, occurrence_date);
CREATE TABLE toms.cash_balances (
 account_uid UUID PRIMARY KEY REFERENCES toms.accounts(account_uid) ON DELETE CASCADE,
 amount_minor BIGINT NOT NULL, observed_at TIMESTAMPTZ NOT NULL,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(), revision BIGINT NOT NULL DEFAULT 1 CHECK(revision > 0)
);
CREATE TABLE toms.cash_coverage (
 id INTEGER PRIMARY KEY CHECK(id = 1), fingerprint TEXT NOT NULL,
 confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON toms.cash_commitments, toms.cash_payments, toms.cash_balances, toms.cash_coverage TO toms_app;
