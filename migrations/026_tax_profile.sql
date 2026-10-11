-- Missing profiles remain unconfirmed; never infer residence from bank or journey data.
CREATE TABLE toms.tax_profiles (
    tax_year TEXT PRIMARY KEY CHECK (tax_year = '2026-27'),
    jurisdiction TEXT NOT NULL DEFAULT 'unknown'
        CHECK (jurisdiction IN ('unknown', 'england', 'wales', 'northern_ireland', 'scotland', 'other')),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    confirmed_at TIMESTAMPTZ,
    CHECK ((jurisdiction = 'unknown') = (confirmed_at IS NULL))
);
GRANT SELECT, INSERT, UPDATE, DELETE ON toms.tax_profiles TO toms_app;
