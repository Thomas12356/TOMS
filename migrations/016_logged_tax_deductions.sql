-- Forecast withholding assumptions are retired. Logged payment tax is preserved.
ALTER TABLE toms.income_streams DROP COLUMN withholding_mode CASCADE,
    DROP COLUMN expected_tax_deducted_minor CASCADE;

CREATE TABLE toms.expense_deductions (
    account_uid uuid NOT NULL, category_uid uuid NOT NULL, feed_item_uid uuid NOT NULL,
    income_stream_id uuid NOT NULL REFERENCES toms.income_streams(id),
    amount_minor bigint NOT NULL CHECK (amount_minor > 0),
    purpose text NOT NULL CHECK (char_length(trim(purpose)) BETWEEN 1 AND 1000),
    category text NOT NULL CHECK (category IN ('general','vehicle_running','parking_tolls')),
    vehicle_key text NOT NULL DEFAULT '' CHECK (char_length(vehicle_key) <= 40),
    recorded_amount_minor bigint NOT NULL, recorded_currency text NOT NULL,
    recorded_time timestamptz NOT NULL,
    PRIMARY KEY (account_uid,category_uid,feed_item_uid),
    FOREIGN KEY (account_uid,category_uid,feed_item_uid)
        REFERENCES toms.transactions(account_uid,category_uid,feed_item_uid) ON DELETE CASCADE,
    CHECK (amount_minor <= recorded_amount_minor),
    CHECK (category <> 'vehicle_running' OR char_length(trim(vehicle_key)) > 0)
);
CREATE INDEX expense_deductions_stream ON toms.expense_deductions(income_stream_id);
CREATE TABLE toms.mileage_entries (
    id uuid PRIMARY KEY, income_stream_id uuid NOT NULL REFERENCES toms.income_streams(id),
    journey_date date NOT NULL CHECK (journey_date BETWEEN '2026-04-06' AND '2027-04-05'),
    location text NOT NULL CHECK (location IN ('england','wales','northern_ireland','scotland')),
    vehicle_type text NOT NULL CHECK (vehicle_type IN ('car_van','motorcycle','bicycle')),
    vehicle_key text NOT NULL CHECK (char_length(trim(vehicle_key)) BETWEEN 1 AND 40),
    miles numeric(9,2) NOT NULL CHECK (miles > 0 AND miles <= 100000),
    purpose text NOT NULL CHECK (char_length(trim(purpose)) BETWEEN 1 AND 1000),
    start_postcode text NOT NULL CHECK (char_length(trim(start_postcode)) BETWEEN 1 AND 12),
    end_postcode text NOT NULL CHECK (char_length(trim(end_postcode)) BETWEEN 1 AND 12),
    reimbursed_minor bigint NOT NULL DEFAULT 0 CHECK (reimbursed_minor >= 0),
    mileage_group text NOT NULL DEFAULT '' CHECK (char_length(mileage_group) <= 80)
);
CREATE INDEX mileage_entries_stream_date ON toms.mileage_entries(income_stream_id, journey_date);
