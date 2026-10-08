-- Preserve old adjustments: they may include NI, fees or pensions and cannot be inferred.
ALTER TABLE toms.transaction_income ADD COLUMN ni_deducted_minor BIGINT
    CHECK (ni_deducted_minor >= 0);
ALTER TABLE toms.transaction_income ADD CONSTRAINT income_total_deductions
    CHECK (COALESCE(tax_deducted_minor, 0)::numeric + COALESCE(ni_deducted_minor, 0)::numeric <= gross_minor);
