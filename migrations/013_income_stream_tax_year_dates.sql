-- Existing forecasts were entered for the supported current UK tax year.
ALTER TABLE toms.income_streams
    ADD COLUMN forecast_tax_year TEXT NOT NULL DEFAULT '2026-27',
    ADD COLUMN forecast_starts_on DATE,
    ADD COLUMN forecast_ends_on DATE,
    ADD CONSTRAINT income_stream_forecast_year CHECK (forecast_tax_year = '2026-27'),
    ADD CONSTRAINT income_stream_forecast_dates CHECK (
        forecast_starts_on IS NULL OR forecast_ends_on IS NULL OR forecast_starts_on <= forecast_ends_on
    );
