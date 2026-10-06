-- Existing streams have an unknown forecast until the owner enters one.
ALTER TABLE toms.income_streams
    ADD COLUMN expected_gross_minor BIGINT,
    ADD COLUMN expected_gross_period TEXT,
    ADD COLUMN expected_gross_currency TEXT,
    ADD CONSTRAINT income_stream_forecast_complete CHECK (
        (expected_gross_minor IS NULL AND expected_gross_period IS NULL AND expected_gross_currency IS NULL)
        OR (expected_gross_minor IS NOT NULL AND expected_gross_minor >= 0
            AND expected_gross_period IS NOT NULL AND expected_gross_period IN ('weekly', 'monthly', 'yearly')
            AND expected_gross_currency IS NOT NULL AND expected_gross_currency IN ('GBP', 'EUR', 'USD'))
    );
