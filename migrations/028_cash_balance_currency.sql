-- Preserve the units of each observation independently of future account updates.
DO $$
DECLARE target_schema TEXT;
BEGIN
 FOREACH target_schema IN ARRAY ARRAY['toms','toms_demo'] LOOP
  IF to_regclass(format('%%I.cash_balances',target_schema)) IS NOT NULL THEN
   EXECUTE format('ALTER TABLE %%I.cash_balances ADD COLUMN recorded_currency TEXT',target_schema);
   EXECUTE format('UPDATE %%I.cash_balances b SET recorded_currency=a.currency FROM %%I.accounts a WHERE a.account_uid=b.account_uid',target_schema,target_schema);
   EXECUTE format('ALTER TABLE %%I.cash_balances ALTER COLUMN recorded_currency SET NOT NULL',target_schema);
   EXECUTE format('ALTER TABLE %%I.cash_balances ADD CHECK (recorded_currency ~ ''^[A-Z]{3}$'')',target_schema);
  END IF;
 END LOOP;
END $$;
