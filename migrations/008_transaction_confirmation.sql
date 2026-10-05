-- Existing and newly imported payments require explicit owner confirmation.
ALTER TABLE toms.transactions ADD COLUMN confirmed_at TIMESTAMPTZ;
CREATE INDEX transactions_unconfirmed_time ON toms.transactions
    (transaction_time DESC, account_uid, category_uid, feed_item_uid)
    WHERE confirmed_at IS NULL;
