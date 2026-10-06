-- Zero keeps existing forecasts unchanged until unpaid time off is entered.
ALTER TABLE toms.income_streams
    ADD COLUMN unpaid_holiday_weeks NUMERIC(4,2) NOT NULL DEFAULT 0,
    ADD CONSTRAINT income_stream_holiday_range CHECK (unpaid_holiday_weeks BETWEEN 0 AND 52);
