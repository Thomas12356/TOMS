-- Keep existing streams independent until the owner identifies related work.
ALTER TABLE toms.income_streams ADD COLUMN mileage_pool_id uuid NOT NULL DEFAULT gen_random_uuid();
CREATE INDEX income_streams_mileage_pool ON toms.income_streams(mileage_pool_id);
