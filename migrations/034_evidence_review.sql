-- Additional document needs are explicit; existing records gain no requirement.
ALTER TABLE toms.expense_deductions ADD COLUMN evidence_needed_reason TEXT
 CHECK (evidence_needed_reason IS NULL OR char_length(trim(evidence_needed_reason)) BETWEEN 1 AND 1000);
ALTER TABLE toms.mileage_entries ADD COLUMN evidence_needed_reason TEXT
 CHECK (evidence_needed_reason IS NULL OR char_length(trim(evidence_needed_reason)) BETWEEN 1 AND 1000);
-- Preserve edited sample records when upgrading existing installations.
ALTER TABLE IF EXISTS toms_demo.expense_deductions ADD COLUMN evidence_needed_reason TEXT
 CHECK (evidence_needed_reason IS NULL OR char_length(trim(evidence_needed_reason)) BETWEEN 1 AND 1000);
ALTER TABLE IF EXISTS toms_demo.mileage_entries ADD COLUMN evidence_needed_reason TEXT
 CHECK (evidence_needed_reason IS NULL OR char_length(trim(evidence_needed_reason)) BETWEEN 1 AND 1000);
