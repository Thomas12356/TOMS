-- Existing forecasts remain unchanged; irregular streams opt into shift totals.
ALTER TABLE toms.income_streams ADD COLUMN income_mode text NOT NULL DEFAULT 'forecast'
    CHECK (income_mode IN ('forecast', 'shifts'));
CREATE TABLE toms.income_shifts (
    id uuid PRIMARY KEY,
    income_stream_id uuid NOT NULL REFERENCES toms.income_streams(id),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    unpaid_break_minutes integer NOT NULL DEFAULT 0 CHECK (unpaid_break_minutes >= 0),
    payment_mode text NOT NULL CHECK (payment_mode IN ('hourly','total')),
    hourly_rate_minor bigint,
    gross_minor bigint NOT NULL CHECK (gross_minor > 0),
    currency text NOT NULL CHECK (currency IN ('GBP','EUR','USD')),
    notes text NOT NULL DEFAULT '' CHECK (char_length(notes) <= 1000),
    CHECK (ends_at > starts_at AND ends_at - starts_at <= interval '24 hours'),
    CHECK (unpaid_break_minutes * interval '1 minute' < ends_at - starts_at),
    CHECK ((payment_mode = 'hourly' AND hourly_rate_minor IS NOT NULL AND hourly_rate_minor > 0)
        OR (payment_mode = 'total' AND hourly_rate_minor IS NULL)),
    UNIQUE (id, income_stream_id)
);
CREATE INDEX income_shifts_stream_time ON toms.income_shifts(income_stream_id, starts_at);
ALTER TABLE toms.expense_deductions ADD COLUMN shift_id uuid,
    ADD CONSTRAINT expense_shift_stream FOREIGN KEY (shift_id, income_stream_id)
    REFERENCES toms.income_shifts(id, income_stream_id);
ALTER TABLE toms.mileage_entries ADD COLUMN shift_id uuid,
    ADD CONSTRAINT mileage_shift_stream FOREIGN KEY (shift_id, income_stream_id)
    REFERENCES toms.income_shifts(id, income_stream_id);
CREATE INDEX expense_deductions_shift ON toms.expense_deductions(shift_id);
CREATE INDEX mileage_entries_shift ON toms.mileage_entries(shift_id);
