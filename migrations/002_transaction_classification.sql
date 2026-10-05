CREATE TABLE toms.transaction_classifications (
    account_uid UUID NOT NULL,
    category_uid UUID NOT NULL,
    feed_item_uid UUID NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('income', 'expense', 'internal_transfer', 'refund', 'other')),
    notes TEXT CHECK (char_length(notes) <= 2000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_uid, category_uid, feed_item_uid),
    FOREIGN KEY (account_uid, category_uid, feed_item_uid)
        REFERENCES toms.transactions(account_uid, category_uid, feed_item_uid) ON DELETE CASCADE
);
