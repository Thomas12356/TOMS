CREATE TABLE toms.transaction_income (
    account_uid UUID NOT NULL,
    category_uid UUID NOT NULL,
    feed_item_uid UUID NOT NULL,
    income_type TEXT NOT NULL CHECK (income_type IN ('roofing', 'amazon_flex', 'employment', 'other')),
    tax_treatment TEXT NOT NULL CHECK (tax_treatment IN ('unknown', 'no_tax_deducted', 'cis', 'paye', 'other_deduction')),
    source_name TEXT CHECK (char_length(source_name) <= 200),
    gross_minor BIGINT CHECK (gross_minor >= 0),
    tax_deducted_minor BIGINT CHECK (tax_deducted_minor >= 0),
    CHECK (tax_deducted_minor <= gross_minor),
    CHECK (tax_treatment <> 'unknown' OR tax_deducted_minor IS NULL),
    CHECK (tax_treatment <> 'no_tax_deducted' OR tax_deducted_minor IS NULL OR tax_deducted_minor = 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_uid, category_uid, feed_item_uid),
    FOREIGN KEY (account_uid, category_uid, feed_item_uid)
        REFERENCES toms.transactions(account_uid, category_uid, feed_item_uid) ON DELETE CASCADE
);
