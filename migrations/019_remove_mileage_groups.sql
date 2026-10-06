-- Stop rather than silently change tax totals on installations with old groups.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM toms.mileage_entries WHERE length(trim(mileage_group)) > 0) THEN
        RAISE EXCEPTION 'Mileage groups still exist. Consolidate their journeys into the appropriate income streams before removing groups.'
            USING ERRCODE = '23514';
    END IF;
END $$;
ALTER TABLE toms.mileage_entries DROP COLUMN mileage_group;
