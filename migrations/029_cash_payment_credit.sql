ALTER TABLE toms.cash_payments ADD COLUMN tax_credit_minor BIGINT NOT NULL DEFAULT 0
 CHECK(tax_credit_minor >= 0 AND tax_credit_minor <= amount_minor);
ALTER TABLE IF EXISTS toms_demo.cash_payments ADD COLUMN tax_credit_minor BIGINT NOT NULL DEFAULT 0
 CHECK(tax_credit_minor >= 0 AND tax_credit_minor <= amount_minor);
