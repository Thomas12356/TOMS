"""Local transfer classification API and sync/report integration."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy.orm import Session

from app import app
from models import Account, Category, Transaction, TransactionClassification
from services.database import db
from services.starling_feed import normalize_feed_item
from services.sync_store import SyncStore


class ClassificationProtectionTests(unittest.TestCase):
    def setUp(self):
        config = patch.dict(app.config, {"APP_API_KEY": "test-key"})
        config.start()
        self.addCleanup(config.stop)
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer test-key"}
        self.path = "/transactions/" + "/".join(str(uuid4()) for _ in range(3)) + "/classification"

    def test_authentication_precedes_reads_and_writes(self):
        with patch("routes.transactions.saved_transaction") as lookup:
            for method in ("GET", "PUT", "DELETE"):
                response = self.client.open(self.path, method=method, json={"type": "income"})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(self.client.get("/transactions/classification-types").status_code, 401)
            lookup.assert_not_called()

    def test_types_are_discoverable_without_database_or_bank_requests(self):
        response = self.client.get("/transactions/classification-types", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({entry["type"] for entry in response.get_json()["types"]},
                         {"income", "expense", "internal_transfer", "refund", "other"})

    def test_invalid_bodies_and_filters_do_not_query_database(self):
        with patch("routes.transactions.saved_transaction") as lookup:
            for body in ([], {}, {"type": []}, {"type": "unknown"}, {"type": "income", "notes": 1},
                         {"type": "income", "notes": "x" * 2001}, {"type": "income", "extra": True}):
                with self.subTest(body=body):
                    response = self.client.put(self.path, json=body, headers=self.headers)
                    self.assertEqual(response.status_code, 400)
            self.assertEqual(self.client.put(self.path, data="not json", headers=self.headers).status_code, 400)
            lookup.assert_not_called()
        with patch.object(db, "paginate") as paginate:
            self.assertEqual(self.client.get("/transactions?classification=unknown", headers=self.headers).status_code, 400)
            paginate.assert_not_called()


@unittest.skipUnless(os.getenv("RUN_POSTGRES_TESTS") == "1", "Set RUN_POSTGRES_TESTS=1 for rollback-only database tests.")
class PostgreSQLClassificationTests(unittest.TestCase):
    def setUp(self):
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.engine = db.engine
        connection = self.engine.connect()
        self.addCleanup(connection.close)
        outer = connection.begin()
        self.addCleanup(outer.rollback)
        self.session = Session(bind=connection, join_transaction_mode="create_savepoint")
        self.addCleanup(self.session.close)
        session_patch = patch.object(db, "session", Mock(return_value=self.session, wraps=self.session))
        session_patch.start()
        self.addCleanup(session_patch.stop)
        config = patch.dict(app.config, {"APP_API_KEY": "test-key"})
        config.start()
        self.addCleanup(config.stop)
        bank = patch("services.starling.http_client.send", side_effect=AssertionError("Local classifications must not call Starling"))
        self.bank = bank.start()
        self.addCleanup(bank.stop)
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer test-key"}
        self.account, self.category, self.outgoing, self.incoming = (str(uuid4()) for _ in range(4))
        self.now = datetime(2000, 2, 15, 12, tzinfo=timezone.utc)
        self.session.add(Account(account_uid=self.account, default_category_uid=self.category,
                                 currency="GBP", raw_payload={}))
        self.session.flush()
        self.session.add(Category(account_uid=self.account, category_uid=self.category, kind="main"))
        self.session.flush()
        for item, direction, amount, source in ((self.outgoing, "OUT", 1250, "FASTER_PAYMENTS_OUT"),
                                               (self.incoming, "IN", 2500, "INTERNAL_TRANSFER")):
            self.session.add(Transaction(account_uid=self.account, category_uid=self.category,
                feed_item_uid=item, direction=direction, amount_minor=amount, currency="GBP",
                source=source, status="SETTLED", transaction_time=self.now, source_updated_at=self.now,
                spending_category="GROCERIES", raw_payload={"private": "Never return this"}))
        self.session.flush()

    def path(self, item=None):
        return f"/transactions/{self.account}/{self.category}/{item or self.outgoing}/classification"

    def request(self, method="GET", item=None, body=None, expected=200):
        response = self.client.open(self.path(item), method=method, json=body, headers=self.headers)
        self.assertEqual(response.status_code, expected, response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.bank.assert_not_called()
        return response.get_json()

    def report(self):
        response = self.client.get("/reports/monthly", query_string={"month": "2000-02", "accountUid": self.account}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return response.get_json()["currencies"][0]

    def test_manual_type_can_be_created_updated_and_cleared(self):
        self.assertEqual(self.request()["classification"], {
            "type": "expense", "origin": "automatic", "notes": None, "updated_at": None})
        result = self.request("PUT", body={"type": "internal_transfer", "notes": "  My other bank  "})
        self.assertEqual(result["classification"]["origin"], "manual")
        self.assertEqual(result["classification"]["notes"], "My other bank")
        self.assertTrue(result["classification"]["updated_at"].endswith("+00:00"))
        self.assertEqual(self.request()["classification"], result["classification"])
        self.assertEqual(self.request("PUT", body={"type": "refund"})["classification"]["notes"], None)
        self.request("PUT", body={"type": "refund"})
        count = self.session.scalar(db.select(db.func.count()).select_from(TransactionClassification).where(
            TransactionClassification.account_uid == self.account))
        self.assertEqual(count, 1)
        self.assertEqual(self.request("DELETE")["classification"]["type"], "expense")
        self.assertEqual(self.request("DELETE")["classification"]["origin"], "automatic")
        self.assertIsNotNone(self.session.get(Transaction, (self.account, self.category, self.outgoing)))

    def test_direction_rules_and_missing_transaction(self):
        self.request("PUT", body={"type": "income"}, expected=400)
        self.request("PUT", item=self.incoming, body={"type": "expense"}, expected=400)
        for kind in ("income", "refund", "other", "internal_transfer"):
            self.request("PUT", item=self.incoming, body={"type": kind})
        for method in ("GET", "PUT", "DELETE"):
            self.request(method, item=str(uuid4()), body={"type": "other"} if method == "PUT" else None, expected=404)

    def test_browser_filter_and_report_use_manual_overrides(self):
        self.assertEqual(self.report()["spending_minor"], 1250)
        self.assertEqual(self.report()["income_minor"], 0)
        self.request("PUT", body={"type": "internal_transfer"})
        total = self.report()
        self.assertEqual(total["spending_minor"], 0)
        self.assertEqual(total["excluded_internal_transfers"]["transaction_count"], 2)
        self.request("PUT", item=self.incoming, body={"type": "income"})
        self.assertEqual(self.report()["income_minor"], 2500)
        response = self.client.get("/transactions", query_string={"accountUid": self.account, "classification": "internal_transfer"}, headers=self.headers)
        data = response.get_json()
        self.assertEqual(data["pagination"]["total"], 1)
        self.assertEqual(data["transactions"][0]["feed_item_uid"], self.outgoing)
        self.assertEqual(data["transactions"][0]["classification"]["origin"], "manual")
        self.assertNotIn("Never return this", str(data))
        self.request("DELETE")
        self.assertEqual(self.report()["spending_minor"], 1250)
        response = self.client.get("/transactions", query_string={"accountUid": self.account, "classification": "expense"}, headers=self.headers)
        self.assertEqual(response.get_json()["transactions"][0]["classification"]["origin"], "automatic")

    def test_sync_updates_bank_fields_without_changing_classification(self):
        self.request("PUT", body={"type": "internal_transfer", "notes": "Own savings"})
        before = self.request()["classification"]
        store = SyncStore(self.session, self.engine)
        run_uid = str(uuid4())
        store.start_run(run_uid, {}, self.now)
        store.start_target(run_uid, self.account, self.category, "history", self.now, self.now)
        updated = self.now + timedelta(hours=1)
        item = normalize_feed_item({"feedItemUid": self.outgoing, "categoryUid": self.category,
            "amount": {"minorUnits": 1500, "currency": "GBP"}, "direction": "OUT", "status": "SETTLED",
            "transactionTime": self.now.isoformat(), "updatedAt": updated.isoformat(),
            "reference": "Updated by Starling", "source": "FASTER_PAYMENTS_OUT"}, self.account, self.category)
        store.save_page(run_uid, self.account, self.category, [item])
        self.assertEqual(self.request()["classification"], before)
        transaction = self.session.get(Transaction, (self.account, self.category, self.outgoing))
        self.assertEqual(transaction.amount_minor, 1500)
        self.assertEqual(transaction.reference, "Updated by Starling")


if __name__ == "__main__":
    unittest.main()
