"""Monthly report protections and rollback-only PostgreSQL aggregation checks."""

import os
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import app
from models import Account, Category, Transaction
from services.database import db


class ReportProtectionTests(unittest.TestCase):
    def setUp(self):
        config = patch.dict(app.config, {"APP_API_KEY": "test-key"})
        config.start()
        self.addCleanup(config.stop)
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer test-key"}

    def test_authentication_precedes_database_access(self):
        with patch.object(db.session, "execute") as execute:
            response = self.client.get("/reports/monthly?month=2000-02")
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(self.client.get("/reports/monthly?month=2000-02", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            with patch.dict(app.config, {"APP_API_KEY": ""}):
                self.assertEqual(self.client.get("/reports/monthly?month=2000-02", headers=self.headers).status_code, 503)
            execute.assert_not_called()

    def test_invalid_options_do_not_query_database(self):
        queries = ("", "month=", "month=2000-13", "month=2000-00", "month=0000-01",
                   "month=9999-12", "month=200002", "month=2000-2", "month=2000-02-01",
                   "month=2000-02&month=2000-03", "month=2000-02&accountUid=invalid",
                   "month=2000-02&accountUid=", "month=2000-02&status=PENDING")
        with patch.object(db.session, "execute") as execute:
            for query in queries:
                with self.subTest(query=query):
                    response = self.client.get("/reports/monthly?" + query, headers=self.headers)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.get_json())
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
            execute.assert_not_called()

    def test_database_error_is_safe(self):
        error = OperationalError("query", {}, Exception("private bank data"))
        with patch.object(db.session, "execute", side_effect=error):
            response = self.client.get("/reports/monthly?month=2000-02", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private bank data", response.get_data(as_text=True))

    def test_unknown_report_has_privacy_headers(self):
        response = self.client.get("/reports/missing", headers=self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


@unittest.skipUnless(os.getenv("RUN_POSTGRES_TESTS") == "1", "Set RUN_POSTGRES_TESTS=1 for rollback-only database tests.")
class PostgreSQLReportTests(unittest.TestCase):
    def setUp(self):
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)
        connection = db.engine.connect()
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
        bank = patch("services.starling.http_client.send", side_effect=AssertionError("Reports must not call Starling"))
        self.bank = bank.start()
        self.addCleanup(bank.stop)
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer test-key"}
        self.account, self.other, self.main, self.space = (str(uuid4()) for _ in range(4))
        self.session.add_all([
            Account(account_uid=self.account, default_category_uid=self.main, currency="GBP", raw_payload={}),
            Account(account_uid=self.other, default_category_uid=self.main, currency="EUR", raw_payload={}),
        ])
        self.session.flush()
        self.session.add_all([
            Category(account_uid=self.account, category_uid=self.main, kind="main"),
            Category(account_uid=self.account, category_uid=self.space, kind="spending"),
            Category(account_uid=self.other, category_uid=self.main, kind="main"),
        ])
        self.session.flush()
        self.add(200000, "IN", "INCOME", when="2000-02-01T00:00:00+00:00")
        self.add(200, "IN", "GROCERIES", source="MASTER_CARD")
        self.add(1250, "OUT", "GROCERIES")
        self.add(2750, "OUT", "GROCERIES", space=True)
        self.add(500, "OUT", None, source=None)
        self.add(300, "OUT", "NONE")
        self.add(200, "OUT", "")
        self.add(10000, "OUT", "BILLS_AND_SERVICES", when="2000-02-29T23:59:59.999999+00:00")
        # Personal-transfer labels alone must not cause external payments to disappear.
        self.add(3000, "OUT", "PERSONAL_TRANSFERS", source="FASTER_PAYMENTS_OUT")
        self.add(10000, "OUT", "SAVING", source="INTERNAL_TRANSFER")
        self.add(10000, "IN", "SAVING", source="INTERNAL_TRANSFER", space=True)
        for status in ("PENDING", "REVERSED", "DECLINED", "REFUNDED"):
            self.add(9000, "OUT", "GROCERIES", status=status)
        self.add(50000, "IN", "INCOME", account=self.other, currency="EUR")
        self.add(5000, "OUT", "GROCERIES", account=self.other, currency="EUR")
        self.add(999, "OUT", "GROCERIES", when="2000-01-31T23:59:59.999999+00:00")
        self.add(999, "OUT", "GROCERIES", when="2000-03-01T00:00:00+00:00")
        self.session.flush()

    def add(self, amount, direction, category, *, source="MASTER_CARD", status="SETTLED",
            when="2000-02-15T12:00:00+00:00", account=None, currency="GBP", space=False):
        self.session.add(Transaction(account_uid=account or self.account,
            category_uid=self.space if space else self.main, feed_item_uid=str(uuid4()),
            amount_minor=amount, currency=currency, direction=direction, status=status,
            transaction_time=datetime.fromisoformat(when), source_updated_at=datetime.fromisoformat(when),
            source=source, spending_category=category,
            raw_payload={"private": "Do not include in report"}))

    def get(self, **options):
        response = self.client.get("/reports/monthly", query_string={"month": "2000-02", **options}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.bank.assert_not_called()
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        return response.get_json()

    def test_totals_categories_and_internal_transfer_exclusion(self):
        report = self.get(accountUid=self.account)
        self.assertEqual(report["period_start"], "2000-02-01T00:00:00+00:00")
        self.assertEqual(report["period_end"], "2000-03-01T00:00:00+00:00")
        self.assertEqual(report["timezone"], "UTC")
        total = report["currencies"][0]
        self.assertEqual(total["income_minor"], 200200)
        self.assertEqual(total["income_by_type"], [{"income_type": "unclassified",
            "tax_treatment": "unknown", "net_received_minor": 200200, "transaction_count": 2}])
        self.assertEqual(total["spending_minor"], 18000)
        self.assertEqual(total["net_minor"], 182200)
        self.assertEqual(total["transaction_count"], 9)
        self.assertEqual(total["spending_by_category"], [
            {"category": "BILLS_AND_SERVICES", "amount_minor": 10000, "transaction_count": 1},
            {"category": "GROCERIES", "amount_minor": 4000, "transaction_count": 2},
            {"category": "PERSONAL_TRANSFERS", "amount_minor": 3000, "transaction_count": 1},
            {"category": "UNCATEGORISED", "amount_minor": 1000, "transaction_count": 3},
        ])
        self.assertEqual(total["excluded_internal_transfers"], {
            "transaction_count": 2, "incoming_minor": 10000, "outgoing_minor": 10000})
        self.assertNotIn("Do not include", str(report))

    def test_currencies_are_separate_and_account_filter_is_applied(self):
        report = self.get()
        self.assertEqual([total["currency"] for total in report["currencies"]], ["EUR", "GBP"])
        eur = report["currencies"][0]
        self.assertEqual((eur["income_minor"], eur["spending_minor"], eur["net_minor"]), (50000, 5000, 45000))
        self.assertEqual([total["currency"] for total in self.get(accountUid=self.other)["currencies"]], ["EUR"])

    def test_empty_and_internal_only_results(self):
        self.assertEqual(self.get(accountUid=str(uuid4()))["currencies"], [])
        self.assertEqual(self.get(month="1990-01", accountUid=self.account)["currencies"], [])
        self.add(500, "OUT", "SAVING", source="INTERNAL_TRANSFER", when="2000-04-01T12:00:00+00:00")
        self.session.flush()
        total = self.get(month="2000-04", accountUid=self.account)["currencies"][0]
        self.assertEqual((total["income_minor"], total["spending_minor"], total["net_minor"]), (0, 0, 0))
        self.assertEqual(total["transaction_count"], 0)
        self.assertEqual(total["income_by_type"], [])
        self.assertEqual(total["spending_by_category"], [])
        self.assertEqual(total["excluded_internal_transfers"]["outgoing_minor"], 500)

    def test_december_rollover_and_timezone_boundaries(self):
        self.add(123, "OUT", "GROCERIES", when="2000-12-31T23:30:00-01:00")
        self.add(456, "OUT", "GROCERIES", when="2001-01-01T00:30:00+01:00")
        self.session.flush()
        december = self.get(month="2000-12", accountUid=self.account)
        self.assertEqual(december["period_end"], "2001-01-01T00:00:00+00:00")
        self.assertEqual(december["currencies"][0]["spending_minor"], 456)
        january = self.get(month="2001-01", accountUid=self.account)
        self.assertEqual(january["currencies"][0]["spending_minor"], 123)


if __name__ == "__main__":
    unittest.main()
