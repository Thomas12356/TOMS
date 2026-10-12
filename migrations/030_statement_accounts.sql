-- Preserve all prior real/sample accounts and edited sample business records.
DO $$
DECLARE target_schema TEXT;
BEGIN
 FOREACH target_schema IN ARRAY ARRAY['toms','toms_demo'] LOOP
  IF to_regclass(format('%%I.accounts',target_schema)) IS NOT NULL THEN
   EXECUTE format('ALTER TABLE %%I.accounts ADD COLUMN provider TEXT NOT NULL DEFAULT ''starling'' CHECK(provider IN (''starling'',''statement'')), ADD COLUMN bank_name TEXT, ADD COLUMN cash_active BOOLEAN NOT NULL DEFAULT TRUE, ADD COLUMN closed_on DATE, ADD COLUMN revision BIGINT NOT NULL DEFAULT 1 CHECK(revision > 0)',target_schema);
  END IF;
 END LOOP;
END $$;
CREATE TABLE toms.account_history (
 account_uid UUID NOT NULL REFERENCES toms.accounts(account_uid), tax_year TEXT NOT NULL CHECK(tax_year='2026-27'),
 from_on DATE NOT NULL, through_on DATE NOT NULL CHECK(through_on >= from_on),
 complete BOOLEAN NOT NULL DEFAULT FALSE, revision BIGINT NOT NULL DEFAULT 1 CHECK(revision > 0), confirmed_at TIMESTAMPTZ,
 PRIMARY KEY(account_uid,tax_year)
);
CREATE TABLE toms.statement_imports (
 id UUID PRIMARY KEY, account_uid UUID NOT NULL REFERENCES toms.accounts(account_uid), account_revision BIGINT NOT NULL CHECK(account_revision > 0),
 file_hash TEXT NOT NULL, mapping_hash TEXT, mapping JSONB, headers JSONB NOT NULL DEFAULT '[]', rows JSONB NOT NULL DEFAULT '[]',
 status TEXT NOT NULL CHECK(status IN ('mapping','preview','imported','discarded')), row_count INTEGER NOT NULL CHECK(row_count BETWEEN 0 AND 1000),
 imported_count INTEGER NOT NULL DEFAULT 0, skipped_count INTEGER NOT NULL DEFAULT 0,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL, imported_at TIMESTAMPTZ,
 UNIQUE(id,account_uid)
);
CREATE INDEX statement_import_account ON toms.statement_imports(account_uid,created_at);
CREATE TABLE toms.statement_import_rows (
 batch_id UUID NOT NULL, row_number INTEGER NOT NULL CHECK(row_number > 0), account_uid UUID NOT NULL,
 category_uid UUID, feed_item_uid UUID, decision TEXT NOT NULL CHECK(decision IN ('import','skip')),
 PRIMARY KEY(batch_id,row_number),
 FOREIGN KEY(batch_id,account_uid) REFERENCES toms.statement_imports(id,account_uid),
 FOREIGN KEY(account_uid,category_uid,feed_item_uid) REFERENCES toms.transactions(account_uid,category_uid,feed_item_uid)
);
GRANT SELECT,INSERT,UPDATE,DELETE ON toms.account_history,toms.statement_imports,toms.statement_import_rows TO toms_app;
