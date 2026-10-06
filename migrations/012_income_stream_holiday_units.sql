-- Keep old forecasts in weeks; preserve partial working days exactly (5 days/week).
ALTER TABLE toms.income_streams
    ALTER COLUMN unpaid_holiday_weeks TYPE NUMERIC(6,4),
    ADD COLUMN unpaid_holiday_unit TEXT NOT NULL DEFAULT 'weeks',
    ADD CONSTRAINT income_stream_holiday_unit CHECK (unpaid_holiday_unit IN ('days', 'weeks'));
