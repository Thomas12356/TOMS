CREATE TABLE toms.evidence_assets (
 sha256 TEXT PRIMARY KEY, byte_count INTEGER NOT NULL CHECK(byte_count BETWEEN 1 AND 10485760),
 mime TEXT NOT NULL, suffix TEXT NOT NULL, data BYTEA NOT NULL, preview BYTEA, pages INTEGER
);
CREATE TABLE toms.evidence_documents (
 id UUID PRIMARY KEY, asset_sha256 TEXT NOT NULL REFERENCES toms.evidence_assets(sha256), kind TEXT NOT NULL,
 account_uid UUID, category_uid UUID, feed_item_uid UUID, mileage_id UUID REFERENCES toms.mileage_entries(id) ON DELETE SET NULL,
 name TEXT NOT NULL CHECK(char_length(name) BETWEEN 1 AND 200), notes TEXT NOT NULL DEFAULT '' CHECK(char_length(notes)<=1000),
 active BOOLEAN NOT NULL DEFAULT TRUE, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), archived_at TIMESTAMPTZ,
 FOREIGN KEY(account_uid,category_uid,feed_item_uid) REFERENCES toms.transactions(account_uid,category_uid,feed_item_uid),
 CHECK((kind='transaction' AND account_uid IS NOT NULL AND category_uid IS NOT NULL AND feed_item_uid IS NOT NULL AND mileage_id IS NULL) OR (kind='mileage' AND account_uid IS NULL AND category_uid IS NULL AND feed_item_uid IS NULL))
);
CREATE INDEX evidence_transaction ON toms.evidence_documents(account_uid,category_uid,feed_item_uid);
CREATE INDEX evidence_mileage ON toms.evidence_documents(mileage_id);
GRANT SELECT,INSERT,UPDATE,DELETE ON toms.evidence_assets,toms.evidence_documents TO toms_app;
