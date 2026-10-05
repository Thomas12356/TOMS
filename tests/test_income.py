"""Income annotations, validation and rollback-only persistence checks."""

import os
import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from app import app
from models import Transaction
from services.database import db
from services.starling_feed import normalize_feed_item
from services.sync_store import SyncStore
import test_classification as classification_tests


class IncomeProtectionTests(unittest.TestCase):
    def setUp(self):
        config = patch.dict(app.config, {"APP_API_KEY": "test-key"})
        config.start()
        self.addCleanup(config.stop)
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer test-key"}
        self.path = "/transactions/" + "/".join(str(uuid4()) for _ in range(3)) + "/income"

    def test_auth_and_discovery(self):
        with patch("routes.transactions.saved_transaction") as lookup:
            for method in ("GET", "PUT", "DELETE"):
                response = self.client.open(self.path, method=method, json={"income_type": "roofing"})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            lookup.assert_not_called()
        self.assertEqual(self.client.get("/transactions/income-types").status_code, 401)
        result = self.client.get("/transactions/income-types", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        types = {item["type"] for item in result.get_json()["income_types"]}
        self.assertTrue({"roofing", "amazon_flex", "personal_gift", "inheritance", "loan_received",
                         "loan_repayment", "tax_refund", "personal_item_sale", "tax_free_benefit"} <= types)
        self.assertIn("non_taxable", {item["type"] for item in result.get_json()["tax_treatments"]})

    def test_invalid_input_rejected_before_database(self):
        invalid = [[], {}, {"income_type": []}, {"income_type": "unknown"},
                   {"income_type": "roofing", "tax_treatment": []},
                   {"income_type": "roofing", "evidence": "ignored"},
                   {"income_type": "roofing", "source_name": "x" * 201}]
        for field in ("gross_minor", "tax_deducted_minor"):
            invalid += [{"income_type": "roofing", field: value} for value in (-1, True, 1.5, "100", 2**63)]
        invalid += [{"income_type": "roofing", "tax_treatment": "cis", "gross_minor": 100, "tax_deducted_minor": 101},
                    {"income_type": "roofing", "tax_deducted_minor": 100},
                    {"income_type": "roofing", "tax_treatment": "no_tax_deducted", "tax_deducted_minor": 100}]
        invalid += [{"income_type": "roofing", "adjustment_minor": value} for value in (True, 1.5, "100", None, 2**63, -(2**63)-1)]
        invalid += [{"income_type": "roofing", "adjustment_minor": 100},
                    {"income_type": "roofing", "adjustment_minor": 100, "adjustment_notes": "  "},
                    {"income_type": "roofing", "adjustment_notes": 1},
                    {"income_type": "roofing", "adjustment_notes": "x" * 2001},
                    {"income_type": "roofing", "needs_review": False}]
        with patch("routes.transactions.saved_transaction") as lookup:
            for body in invalid:
                with self.subTest(body=body):
                    self.assertEqual(self.client.put(self.path, json=body, headers=self.headers).status_code, 400)
            self.assertEqual(self.client.put(self.path, data="text", headers=self.headers).status_code, 400)
            lookup.assert_not_called()
        with patch.object(db, "paginate") as paginate:
            for query in ("income_type=wrong", "tax_treatment=wrong", "income_type=roofing&income_type=other"):
                self.assertEqual(self.client.get("/transactions?" + query, headers=self.headers).status_code, 400)
            paginate.assert_not_called()


@unittest.skipUnless(os.getenv("RUN_POSTGRES_TESTS") == "1", "Set RUN_POSTGRES_TESTS=1 for rollback-only tests.")
class PostgreSQLIncomeTests(unittest.TestCase):
    setUp = classification_tests.PostgreSQLClassificationTests.setUp

    def path(self, item=None):
        return f"/transactions/{self.account}/{self.category}/{item or self.incoming}/income"

    def call(self, method="GET", body=None, item=None, expected=200):
        response = self.client.open(self.path(item), method=method, json=body, headers=self.headers)
        self.assertEqual(response.status_code, expected, response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.bank.assert_not_called()
        return response.get_json()

    def classify(self, kind="income", method="PUT", expected=200):
        response = self.client.open(self.path().replace("/income", "/classification"),
                                    method=method, json={"type": kind}, headers=self.headers)
        self.assertEqual(response.status_code, expected, response.get_data(as_text=True))

    def test_unknown_amounts_crud_filter_and_classification_guards(self):
        self.assertIsNone(self.call()["income"])
        self.call("PUT", {"income_type": "roofing"}, expected=400)  # own transfer
        self.classify()
        value = self.call("PUT", {"income_type": "roofing", "source_name": "  Contractor  "})["income"]
        self.assertEqual(value["tax_treatment"], "unknown")
        self.assertIsNone(value["gross_minor"])
        self.assertIsNone(value["tax_deducted_minor"])
        self.assertEqual(value["net_received_minor"], 2500)
        self.assertEqual(value["source_name"], "Contractor")
        self.assertEqual(self.call()["income"], value)
        report = self.client.get("/reports/monthly", query_string={"month": "2000-02", "accountUid": self.account}, headers=self.headers)
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.get_json()["currencies"][0]["income_by_type"], [{
            "income_type": "roofing", "tax_treatment": "unknown", "net_received_minor": 2500,
            "transaction_count": 1}])
        self.classify("internal_transfer", expected=400)
        self.classify(method="DELETE", expected=400)
        response = self.client.get("/transactions", query_string={"accountUid": self.account,
            "income_type": "roofing", "tax_treatment": "unknown", "classification": "income"}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["pagination"]["total"], 1)
        self.assertEqual(response.get_json()["transactions"][0]["income"], value)
        self.call("PUT", {"income_type": "amazon_flex", "tax_treatment": "no_tax_deducted", "tax_deducted_minor": 0})
        updated = self.call()["income"]
        self.assertIsNone(updated["source_name"])
        self.assertEqual(updated["tax_deducted_minor"], 0)
        self.call("DELETE")
        self.call("DELETE")
        self.classify("refund")
        report = self.client.get("/reports/monthly", query_string={"month": "2000-02", "accountUid": self.account}, headers=self.headers)
        totals = report.get_json()["currencies"][0]
        self.assertEqual(totals["income_by_type"], [])
        self.assertEqual(totals["income_minor"], 2500)
        self.classify(method="DELETE")
        self.assertIsNotNone(self.session.get(Transaction, (self.account, self.category, self.incoming)))

    def test_confirmed_amounts_and_outgoing_missing_rejection(self):
        self.classify()
        result = self.call("PUT", {"income_type": "roofing", "tax_treatment": "cis",
                          "gross_minor": 3125, "tax_deducted_minor": 625})["income"]
        self.assertEqual(result["gross_minor"], 3125)
        self.assertEqual(result["tax_deducted_minor"], 625)
        self.call("PUT", {"income_type": "roofing", "gross_minor": 100}, expected=400)
        self.assertEqual(self.call()["income"], result)
        self.call("PUT", {"income_type": "roofing"}, item=self.outgoing, expected=400)
        for method in ("GET", "PUT", "DELETE"):
            self.call(method, {"income_type": "other"} if method == "PUT" else None,
                      item=str(uuid4()), expected=404)

    def test_non_taxable_categories_require_explicit_treatment_and_preserve_cash_flow(self):
        self.classify()
        kinds = ("personal_gift", "inheritance", "loan_received", "loan_repayment", "tax_refund",
                 "personal_item_sale", "tax_free_benefit")
        for kind in kinds:
            with self.subTest(kind=kind):
                unknown = self.call("PUT", {"income_type": kind})["income"]
                self.assertEqual(unknown["tax_treatment"], "unknown")
                confirmed = self.call("PUT", {"income_type": kind, "tax_treatment": "non_taxable",
                                              "tax_deducted_minor": 0})["income"]
                self.assertEqual(confirmed["tax_treatment"], "non_taxable")
                self.call("PUT", {"income_type": kind, "tax_treatment": "non_taxable",
                                  "tax_deducted_minor": 100}, expected=400)
        response = self.client.get("/transactions", query_string={"accountUid": self.account,
            "tax_treatment": "non_taxable"}, headers=self.headers)
        self.assertEqual(response.get_json()["pagination"]["total"], 1)
        report = self.client.get("/reports/monthly", query_string={"month": "2000-02", "accountUid": self.account}, headers=self.headers)
        total = report.get_json()["currencies"][0]
        self.assertEqual(total["income_minor"], 2500)
        self.assertEqual(total["income_by_type"][0]["tax_treatment"], "non_taxable")

    def test_sync_preserves_income_annotations(self):
        self.classify()
        before = self.call("PUT", {"income_type": "roofing"})["income"]
        store = SyncStore(self.session, self.engine)
        run = str(uuid4())
        store.start_run(run, {}, self.now)
        store.start_target(run, self.account, self.category, "history", self.now, self.now)
        item = normalize_feed_item({"feedItemUid": self.incoming, "categoryUid": self.category,
            "amount": {"minorUnits": 2600, "currency": "GBP"}, "direction": "IN", "status": "SETTLED",
            "transactionTime": self.now.isoformat(), "updatedAt": (self.now + timedelta(hours=1)).isoformat(),
            "source": "FASTER_PAYMENTS_IN"}, self.account, self.category)
        store.save_page(run, self.account, self.category, [item])
        after = self.call()["income"]
        self.assertEqual(after.pop("net_received_minor"), 2600)
        before.pop("net_received_minor")
        self.assertTrue(after.pop("needs_review"))
        self.assertFalse(before.pop("needs_review"))
        after.pop("updated_at")
        before.pop("updated_at")
        self.assertEqual(after, before)


    def test_reconciliation_rejects_impossible_payments_and_supports_explained_adjustments(self):
        self.classify()
        base = {"income_type": "roofing", "tax_treatment": "cis", "gross_minor": 3000,
                "tax_deducted_minor": 1000}
        self.call("PUT", base, expected=400)  # £20 expected, £25 received
        result = self.call("PUT", {**base, "adjustment_minor": 500,
                          "adjustment_notes": "  Additional reimbursed cost  "})["income"]
        self.assertEqual(result["adjustment_minor"], 500)
        self.assertEqual(result["adjustment_notes"], "Additional reimbursed cost")
        self.call("PUT", {**base, "adjustment_minor": 400, "adjustment_notes": "Wrong amount"}, expected=400)
        self.assertEqual(self.call()["income"], result)
        deductions = self.call("PUT", {**base, "gross_minor": 4000, "adjustment_minor": -500,
                              "adjustment_notes": "Other deduction"})["income"]
        self.assertEqual(deductions["adjustment_minor"], -500)
        self.call("PUT", {"income_type": "roofing", "gross_minor": 3000})  # tax unknown
        self.call("PUT", {"income_type": "roofing", "gross_minor": 3000,
                          "tax_treatment": "no_tax_deducted"}, expected=400)
        self.call("PUT", {"income_type": "roofing", "gross_minor": 2500,
                          "tax_treatment": "no_tax_deducted"})

    def update_bank(self, **changes):
        transaction = self.session.get(Transaction, (self.account, self.category, self.incoming))
        item = {column.name: getattr(transaction, column.name) for column in Transaction.__table__.columns
                if column.name != "fetched_at"}
        item.update(source_updated_at=transaction.source_updated_at + timedelta(hours=1), **changes)
        item["raw_payload"] = {"version": item["source_updated_at"].isoformat()}
        store = SyncStore(self.session, self.engine)
        run = str(uuid4())
        store.start_run(run, {}, self.now)
        store.start_target(run, self.account, self.category, "history", self.now, self.now)
        store.save_page(run, self.account, self.category, [item])
        return store, run, item

    def test_bank_correction_flags_review_preserves_currency_and_requires_valid_reconfirmation(self):
        self.classify()
        body = {"income_type": "roofing", "tax_treatment": "cis", "gross_minor": 3125,
                "tax_deducted_minor": 625}
        before = self.call("PUT", body)["income"]
        self.update_bank(amount_minor=3500)
        after = self.call()["income"]
        self.assertTrue(after["needs_review"])
        self.assertEqual(after["gross_minor"], before["gross_minor"])
        self.assertEqual(after["tax_deducted_minor"], before["tax_deducted_minor"])
        report = self.client.get("/reports/monthly", query_string={"month": "2000-02", "accountUid": self.account}, headers=self.headers)
        self.assertEqual(report.get_json()["currencies"][0]["income_needing_review"], 1)
        self.call("PUT", body, expected=400)
        self.assertTrue(self.call()["income"]["needs_review"])
        self.call("PUT", {**body, "gross_minor": 4125})
        self.assertFalse(self.call()["income"]["needs_review"])
        self.update_bank(currency="EUR")
        corrected = self.call()["income"]
        self.assertTrue(corrected["needs_review"])
        self.assertEqual(corrected["recorded_currency"], "GBP")
        self.assertEqual(corrected["currency"], "EUR")
        confirmed = self.call("PUT", {**body, "gross_minor": 4125})["income"]
        self.assertFalse(confirmed["needs_review"])
        self.assertEqual(confirmed["recorded_currency"], "EUR")

    def test_review_flags_relevant_changes_only_and_remain_until_reconfirmed(self):
        self.classify()
        self.call("PUT", {"income_type": "roofing"})
        self.update_bank(status="PENDING")
        self.assertFalse(self.call()["income"]["needs_review"])
        for changes in ({"reference": "Corrected reference"}, {"counterparty_name": "Corrected payer"},
                        {"transaction_time": self.now + timedelta(days=1)}, {"direction": "OUT"},
                        {"direction": "IN", "source": "FASTER_PAYMENTS_IN"}):
            with self.subTest(changes=changes):
                self.update_bank(**changes)
                self.assertTrue(self.call()["income"]["needs_review"])
                if changes.get("direction") != "OUT":
                    self.call("PUT", {"income_type": "roofing"})
        store, run, item = self.update_bank(amount_minor=3500)
        stale = {**item, "amount_minor": 100, "source_updated_at": self.now}
        store.save_page(run, self.account, self.category, [stale])
        self.assertEqual(self.call()["income"]["net_received_minor"], 3500)
        store.save_page(run, self.account, self.category, [item])
        self.assertTrue(self.call()["income"]["needs_review"])
