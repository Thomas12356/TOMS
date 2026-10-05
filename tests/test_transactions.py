"""Validation tests and opt-in rollback-only PostgreSQL browsing tests."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from app import app
from models import Account, Category, Transaction
from services.database import db
from support import ApiTestCase, PostgreSQLTestCase


class TransactionProtectionTests(ApiTestCase):
    def test_authentication_precedes_database_access(self):
        with patch.object(db, "paginate") as paginate:
            self.assertEqual(self.client.get("/transactions").status_code, 401)
            self.assertEqual(self.client.get("/transactions", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            with patch.dict(app.config, {"APP_API_KEY": ""}):
                self.assertEqual(self.client.get("/transactions", headers=self.headers).status_code, 503)
            paginate.assert_not_called()

    def test_invalid_filters_never_query_database(self):
        queries = (
            "page=0", "page=-1", "page=1.5", "page=999999999999999999999",
            "per_page=0", "per_page=101", "per_page=2&per_page=3", "page=",
            "accountUid=invalid", "direction=SIDEWAYS", "status=", "status=" + "x" * 101,
            "start=2026-02-30", "end=not-a-date", "start=20260601", "start=2026-6-1",
            "start=2026-07-01&end=2026-06-30", "start=2026-06-01&start=2026-06-02",
            "unknown=value",
        )
        with patch.object(db, "paginate") as paginate:
            for query in queries:
                with self.subTest(query=query):
                    response = self.client.get("/transactions?" + query, headers=self.headers)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.get_json())
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
            paginate.assert_not_called()

    def test_database_errors_are_safe(self):
        error = OperationalError("query", {}, Exception("private bank data"))
        with patch.object(db, "paginate", side_effect=error):
            response = self.client.get("/transactions", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private bank data", response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_missing_paths_have_privacy_headers(self):
        response = self.client.get("/transactions/missing", headers=self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


class PostgreSQLTransactionTests(PostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.account, self.other_account, self.category = str(uuid4()), str(uuid4()), str(uuid4())
        for account in (self.account, self.other_account):
            self.session.add(Account(account_uid=account, default_category_uid=self.category,
                                     currency="GBP", raw_payload={}))
        self.session.flush()
        for account in (self.account, self.other_account):
            self.session.add(Category(account_uid=account, category_uid=self.category, kind="main"))
        self.session.flush()
        # The two newest transactions have identical times to exercise tie-breaking.
        self.items = [
            self.add_item(self.account, "2026-06-01T00:00:00+00:00", "IN", "SETTLED"),
            self.add_item(self.account, "2026-06-30T23:59:59.999999+00:00", "OUT", "SETTLED"),
            self.add_item(self.account, "2026-07-01T00:00:00+00:00", "OUT", "PENDING"),
            self.add_item(self.account, "2026-07-02T12:00:00+00:00", "OUT", "SETTLED"),
            self.add_item(self.account, "2026-07-02T12:00:00+00:00", "IN", "CUSTOM_STATUS"),
        ]
        self.add_item(self.other_account, "2026-06-15T12:00:00+00:00", "OUT", "SETTLED")
        self.session.flush()
        self.session.expire_all()

    def add_item(self, account, when, direction, status):
        item_uid = str(uuid4())
        self.session.add(Transaction(account_uid=account, category_uid=self.category,
            feed_item_uid=item_uid, amount_minor=1250, currency="GBP", direction=direction,
            status=status, transaction_time=datetime.fromisoformat(when),
            source_updated_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
            counterparty_name="Test shop", reference="Test reference",
            raw_payload={"private": "Must never appear in browser results"}))
        return item_uid

    def get(self, **options):
        response = self.client.get("/transactions", query_string={"accountUid": self.account, **options},
                                   headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.bank.assert_not_called()
        return response.get_json()

    def test_default_response_is_private_and_read_only(self):
        report = self.get()
        self.assertEqual(report["pagination"], {
            "page": 1, "per_page": 50, "total": 5, "pages": 1, "has_next": False, "has_prev": False})
        self.assertEqual(len(report["transactions"]), 5)
        item = report["transactions"][0]
        self.assertEqual(item["amount_minor"], 1250)
        self.assertEqual(item["counterparty_name"], "Test shop")
        self.assertTrue(item["transaction_time"].endswith("+00:00"))
        self.assertNotIn("raw_payload", item)
        self.assertNotIn("Must never appear", str(report))
        # A fully bounded final page is accepted.
        self.assertEqual(self.get(per_page=100)["pagination"]["per_page"], 100)

    def test_pagination_is_newest_first_and_breaks_ties(self):
        all_ids = []
        for number in (1, 2, 3):
            report = self.get(page=number, per_page=2)
            self.assertEqual(report["pagination"]["pages"], 3)
            self.assertEqual(report["pagination"]["has_next"], number < 3)
            all_ids.extend(item["feed_item_uid"] for item in report["transactions"])
        self.assertEqual(all_ids, sorted(self.items[3:]) + [self.items[2], self.items[1], self.items[0]])
        self.assertEqual(len(set(all_ids)), 5)
        self.assertEqual(self.get(page=1, per_page=2)["transactions"][0]["feed_item_uid"], all_ids[0])
        beyond = self.get(page=4, per_page=2)
        self.assertEqual(beyond["transactions"], [])
        self.assertEqual(beyond["pagination"]["total"], 5)

    def test_dates_include_entire_utc_end_day(self):
        report = self.get(start="2026-06-01", end="2026-06-30")
        self.assertEqual([item["feed_item_uid"] for item in report["transactions"]], [self.items[1], self.items[0]])
        same_day = self.get(start="2026-07-01", end="2026-07-01")
        self.assertEqual([item["feed_item_uid"] for item in same_day["transactions"]], [self.items[2]])
        self.assertEqual(self.get(end="2026-06-30")["pagination"]["total"], 2)
        self.assertEqual(self.get(start="2026-07-01")["pagination"]["total"], 3)

    def test_filters_combine_and_unknown_accounts_return_empty(self):
        report = self.get(start="2026-06-01", end="2026-06-30", direction="OUT", status="SETTLED")
        self.assertEqual([item["feed_item_uid"] for item in report["transactions"]], [self.items[1]])
        self.assertEqual(self.get(status="CUSTOM_STATUS")["pagination"]["total"], 1)
        self.assertEqual(self.get(status="NOT_PRESENT")["transactions"], [])
        self.assertEqual(self.get(accountUid=str(uuid4()))["pagination"]["total"], 0)
        self.assertEqual(self.get(accountUid=self.other_account)["pagination"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
