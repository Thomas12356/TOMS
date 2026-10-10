-- Existing saved claims remain deductions; new forms explicitly choose treatment.
ALTER TABLE toms.expense_deductions ADD COLUMN is_tax_deductible BOOLEAN NOT NULL DEFAULT TRUE;

CREATE TABLE toms.account_reserves (
    account_uid UUID PRIMARY KEY REFERENCES toms.accounts(account_uid) ON DELETE CASCADE,
    tax_minor BIGINT NOT NULL DEFAULT 0 CHECK (tax_minor >= 0),
    ni_minor BIGINT NOT NULL DEFAULT 0 CHECK (ni_minor >= 0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    CHECK (tax_minor::numeric + ni_minor::numeric <= 9223372036854775807)
);

-- Store acknowledgements only. Notices are derived from current bank/income data,
-- so corrections cannot leave a stale calculated amount in a notification table.
CREATE TABLE toms.income_notice_reads (
    account_uid UUID NOT NULL,
    category_uid UUID NOT NULL,
    feed_item_uid UUID NOT NULL,
    payment_version TEXT NOT NULL CHECK (char_length(payment_version) = 64),
    PRIMARY KEY (account_uid, category_uid, feed_item_uid),
    FOREIGN KEY (account_uid, category_uid, feed_item_uid)
        REFERENCES toms.transactions(account_uid, category_uid, feed_item_uid) ON DELETE CASCADE
);
