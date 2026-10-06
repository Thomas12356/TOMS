-- Forecast deductions are independent of the deductions recorded on bank payments.
ALTER TABLE toms.income_streams
    ADD COLUMN withholding_mode text NOT NULL DEFAULT 'unknown',
    ADD COLUMN expected_tax_deducted_minor bigint,
    ADD CONSTRAINT income_stream_withholding_mode CHECK (withholding_mode IN ('unknown', 'none', 'paye_estimate', 'manual')),
    ADD CONSTRAINT income_stream_paye_employed CHECK (withholding_mode <> 'paye_estimate' OR kind = 'employed'),
    ADD CONSTRAINT income_stream_withholding_amount CHECK (
        (withholding_mode = 'manual' AND expected_tax_deducted_minor IS NOT NULL AND expected_tax_deducted_minor >= 0) OR
        (withholding_mode <> 'manual' AND expected_tax_deducted_minor IS NULL));
