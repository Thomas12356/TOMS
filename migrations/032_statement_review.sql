CREATE TABLE toms.statement_mappings (
 account_uid UUID NOT NULL REFERENCES toms.accounts(account_uid), signature TEXT NOT NULL,
 mapping JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(account_uid,signature)
);
CREATE TABLE toms.statement_changes (
 id UUID PRIMARY KEY, account_uid UUID NOT NULL REFERENCES toms.accounts(account_uid),
 kind TEXT NOT NULL, before JSONB NOT NULL, after JSONB NOT NULL, reason TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
GRANT SELECT,INSERT,UPDATE,DELETE ON toms.statement_mappings,toms.statement_changes TO toms_app;
