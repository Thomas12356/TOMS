ALTER TABLE toms.transaction_income
    DROP CONSTRAINT transaction_income_income_type_check,
    DROP CONSTRAINT transaction_income_tax_treatment_check,
    ADD CONSTRAINT transaction_income_income_type_check CHECK (
        income_type IN ('roofing', 'amazon_flex', 'employment', 'other', 'personal_gift',
                        'inheritance', 'loan_received', 'loan_repayment', 'tax_refund',
                        'personal_item_sale', 'tax_free_benefit')),
    ADD CONSTRAINT transaction_income_tax_treatment_check CHECK (
        tax_treatment IN ('unknown', 'no_tax_deducted', 'cis', 'paye', 'other_deduction', 'non_taxable')),
    ADD CONSTRAINT income_non_taxable_zero_tax CHECK (
        tax_treatment <> 'non_taxable' OR tax_deducted_minor IS NULL OR tax_deducted_minor = 0);
