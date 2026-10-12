-- Payment corrections must invalidate observations even after a clock regression.
ALTER TABLE toms.cash_balances ADD COLUMN payment_fingerprint TEXT;
ALTER TABLE IF EXISTS toms_demo.cash_balances ADD COLUMN payment_fingerprint TEXT;
