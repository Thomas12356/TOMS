-- Existing shift records keep their original meaning. Only explicitly added
-- overtime is added on top of a regular forecast.
ALTER TABLE toms.income_shifts ADD COLUMN is_overtime boolean NOT NULL DEFAULT false;
