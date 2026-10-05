ALTER TABLE toms.transaction_income
    ADD COLUMN adjustment_minor BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN adjustment_notes TEXT,
    ADD COLUMN recorded_currency TEXT,
    ADD COLUMN needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    ADD CONSTRAINT income_adjustment_explained CHECK (
        adjustment_minor = 0 OR (adjustment_notes IS NOT NULL AND char_length(trim(adjustment_notes)) > 0)),
    ADD CONSTRAINT income_adjustment_notes_length CHECK (char_length(adjustment_notes) <= 2000);

-- Existing entries predate reconciliation checks and currency provenance.
-- Preserve them and require review rather than guessing a correction.
UPDATE toms.transaction_income AS income
SET recorded_currency = transaction.currency, needs_review = TRUE
FROM toms.transactions AS transaction
WHERE income.account_uid = transaction.account_uid
  AND income.category_uid = transaction.category_uid
  AND income.feed_item_uid = transaction.feed_item_uid;

ALTER TABLE toms.transaction_income ALTER COLUMN recorded_currency SET NOT NULL;
