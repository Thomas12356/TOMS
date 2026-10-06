from services.database.migration_connection import migration_engine
"""Opt-in real PostgreSQL tests; all synthetic rows are rolled back."""

import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import text

from sqlalchemy.orm import Session

from services.database.connection import db
from models import Transaction

from services.database.migrations import upgrade_database
from services.banking.client import StarlingError
from services.banking.feed import normalize_feed_item
from services.transactions.store import LOCK_ID, SyncStore
from services.transactions.sync import SyncError, run_sync
from support import PostgreSQLTestCase
from test_transaction_sync import ACCOUNT, CATEGORY, CURSOR, ITEM, NOW, discovery, feed_item


class PostgreSQLSyncTests(PostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.store = SyncStore(self.session, self.engine)
        self.addCleanup(self.store.unlock)
        self.enterContext(patch("services.transactions.sync.starling_request", side_effect=discovery))

    def import_rows(self, items, options=None, now=NOW):
        with patch("services.transactions.sync.history_pages", return_value=[items]):
            return run_sync(self.store, options or {"mode": "history"}, now=now)

    def test_migration_idempotency_and_duplicate_safe_versioned_upserts(self):
        engine = migration_engine()
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            self.assertEqual(upgrade_database(connection), [])
        pending = normalize_feed_item(feed_item(), ACCOUNT, CATEGORY)
        first = self.import_rows([pending])
        self.assertEqual(first["rows_changed"], 1)
        repeat = self.import_rows([pending])
        self.assertEqual(repeat["rows_changed"], 0)
        later = NOW + timedelta(hours=1)
        settled = normalize_feed_item(feed_item(status="SETTLED", updated=later), ACCOUNT, CATEGORY)
        self.import_rows([settled], now=later)
        self.import_rows([pending], now=later)
        rows = self.session.execute(db.select(Transaction.status, Transaction.amount_minor).where(
            Transaction.account_uid == ACCOUNT)).all()
        self.assertEqual(rows, [("SETTLED", 1250)])

    def test_bank_detail_changes_reopen_confirmation_but_identical_sync_does_not(self):
        item = normalize_feed_item(feed_item(), ACCOUNT, CATEGORY)
        self.import_rows([item])
        transaction = self.session.get(Transaction, (ACCOUNT, CATEGORY, ITEM))
        transaction.confirmed_at = NOW
        self.session.commit()
        self.import_rows([item])
        self.assertIsNotNone(transaction.confirmed_at)
        later = NOW + timedelta(hours=1)
        changed = normalize_feed_item(feed_item(status="SETTLED", updated=later), ACCOUNT, CATEGORY)
        self.import_rows([changed], now=later)
        self.assertIsNone(transaction.confirmed_at)

    def test_failure_retains_page_and_checkpoint_then_rerun_completes(self):
        item = normalize_feed_item(feed_item(), ACCOUNT, CATEGORY)
        def pages(*args):
            yield [item]
            raise StarlingError("Test upstream interruption.")
        with patch("services.transactions.sync.history_pages", side_effect=pages):
            with self.assertRaises(SyncError) as failure:
                run_sync(self.store, {}, now=NOW)
        report = self.store.report(failure.exception.run_uid)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["pages_committed"], 1)
        self.assertIsNone(self.store.category(ACCOUNT, CATEGORY)["changes_through"])
        complete = self.import_rows([item])
        self.assertEqual(complete["status"], "completed")
        self.assertEqual(complete["rows_changed"], 0)
        self.assertEqual(self.store.category(ACCOUNT, CATEGORY)["changes_through"], NOW)

    def test_page_is_atomic_and_checkpoint_unchanged_on_database_error(self):
        first = normalize_feed_item(feed_item(), ACCOUNT, CATEGORY)
        invalid = normalize_feed_item(feed_item(CURSOR), ACCOUNT, CATEGORY)
        invalid["amount_minor"] = -1
        with patch("services.transactions.sync.history_pages", return_value=[[first, invalid]]):
            with self.assertRaises(SyncError): run_sync(self.store, {}, now=NOW)
        count = self.session.scalar(db.select(db.func.count()).select_from(Transaction).where(
            Transaction.account_uid == ACCOUNT))
        self.assertEqual(count, 0)
        self.assertIsNone(self.store.category(ACCOUNT, CATEGORY)["changes_through"])

    def test_incremental_updates_and_historical_query_does_not_advance_watermark(self):
        self.import_rows([normalize_feed_item(feed_item(), ACCOUNT, CATEGORY)])
        later = NOW + timedelta(hours=1)
        settled = normalize_feed_item(feed_item(status="SETTLED", updated=later), ACCOUNT, CATEGORY)
        with patch("services.transactions.sync.changed_items", return_value=[settled]):
            result = run_sync(self.store, {}, now=later)
        self.assertEqual(result["targets"][0]["mode"], "incremental")
        self.assertEqual(self.store.category(ACCOUNT, CATEGORY)["changes_through"], later)
        self.import_rows([], {"start": "2019-01-01", "end": "2020-01-01"}, now=later)
        self.assertEqual(self.store.category(ACCOUNT, CATEGORY)["changes_through"], later)

    def test_database_lock_rejects_concurrent_sync(self):
        self.assertTrue(self.store.lock())
        try:
            with Session(self.engine) as other:
                with self.assertRaises(SyncError) as failure:
                    run_sync(SyncStore(other, self.engine), {}, now=NOW)
                self.assertEqual(failure.exception.status_code, 409)
        finally:
            self.store.unlock()

    def test_repeated_items_and_same_timestamp_correction(self):
        item = normalize_feed_item(feed_item(), ACCOUNT, CATEGORY)
        first = self.import_rows([item, item])
        self.assertEqual(first["items_received"], 2)
        self.assertEqual(first["rows_changed"], 1)
        corrected = normalize_feed_item({**feed_item(), "reference": "Corrected reference"}, ACCOUNT, CATEGORY)
        result = self.import_rows([corrected])
        self.assertEqual(result["rows_changed"], 1)
        stored = self.session.get(Transaction, (ACCOUNT, CATEGORY, ITEM))
        self.assertEqual(stored.reference, "Corrected reference")
        self.assertEqual(self.import_rows([corrected])["rows_changed"], 0)

    def test_lock_survives_page_commit_and_is_released_after_sync(self):
        item = normalize_feed_item(feed_item(), ACCOUNT, CATEGORY)
        query = text("SELECT pg_try_advisory_xact_lock(:key)")
        with self.engine.connect() as contender:
            def pages(*args):
                yield [item]
                # The page has committed when the generator resumes.
                self.assertFalse(contender.scalar(query, {"key": LOCK_ID}))
            with patch("services.transactions.sync.history_pages", side_effect=pages):
                run_sync(self.store, {}, now=NOW)
            self.assertTrue(contender.scalar(query, {"key": LOCK_ID}))

    def test_existing_report_routes_serialize_models_and_uuid_paths(self):
        report = self.import_rows([normalize_feed_item(feed_item(), ACCOUNT, CATEGORY)])
        with patch("routes.sync.SyncStore", return_value=self.store):
            response = self.client.get("/sync/runs/" + report["run_uid"], headers=self.headers)
            recent = self.client.get("/sync/runs", headers=self.headers)
            missing = self.client.get("/sync/runs/" + str(uuid4()), headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), report)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(recent.status_code, 200)
        self.assertIn("runs", recent.get_json())
        self.assertEqual(missing.status_code, 404)

    def test_sql_migrations_apply_once_and_detect_edits(self):
        engine = migration_engine()
        self.addCleanup(engine.dispose)
        connection = engine.connect()
        self.addCleanup(connection.close)
        transaction = connection.begin()
        self.addCleanup(transaction.rollback)
        with TemporaryDirectory() as directory:
            filename = "999_test_" + uuid4().hex + ".sql"
            path = Path(directory) / filename
            path.write_text("CREATE TEMP TABLE migration_probe (id INTEGER); INSERT INTO migration_probe VALUES (1);")
            with patch("services.database.migrations.MIGRATIONS", Path(directory)):
                self.assertEqual(upgrade_database(connection), [filename])
                self.assertEqual(connection.scalar(text("SELECT id FROM migration_probe")), 1)
                self.assertEqual(upgrade_database(connection), [])
                path.write_text("SELECT 2;")
                with self.assertRaisesRegex(RuntimeError, "modified"):
                    upgrade_database(connection)


if __name__ == "__main__":
    unittest.main()
