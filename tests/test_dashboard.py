"""The dashboard must protect private data and render saved payments safely."""

import base64
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app import app
from services.database.connection import db
from support import ApiTestCase


def saved_payment(**changes):
    values = {
        "transaction_time": datetime(2026, 10, 5, 12, tzinfo=timezone.utc),
        "counterparty_name": "Example shop", "reference": "Weekly supplies",
        "amount_minor": 1250, "currency": "GBP", "direction": "OUT",
        "status": "SETTLED", "source": "MASTER_CARD", "classification": None,
        "raw_payload": {"private": "Never display raw bank data"},
    }
    values.update(changes)
    return SimpleNamespace(**values)


def saved_page(items, *, number=1, total=1):
    pages = (total + 49) // 50
    return SimpleNamespace(items=items, page=number, total=total, pages=pages,
        has_prev=number > 1, prev_num=number - 1,
        has_next=number < pages, next_num=number + 1)


class DashboardTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.accounts = self.enterContext(patch.object(db.session, "scalars", return_value=[]))
        self.bank = self.enterContext(patch("services.banking.client.http_client.send",
            side_effect=AssertionError("The dashboard must not call Starling")))

    def test_authentication_precedes_database_access(self):
        with patch.object(db, "paginate", return_value=saved_page([])) as paginate:
            with patch.dict(app.config, {"SECRET_KEY": "a" * 64}):
                response = self.client.get("/dashboard")
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].endswith("/login"))
                basic = base64.b64encode(b"api:test-key").decode()
                self.assertEqual(self.client.get("/dashboard", headers={"Authorization": "Basic " + basic}).status_code, 302)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(self.client.get("/dashboard", headers={"Authorization": "Bearer wrong"}).status_code, 401)
            with patch.dict(app.config, {"APP_API_KEY": ""}):
                self.assertEqual(self.client.get("/dashboard", headers=self.headers).status_code, 503)
            paginate.assert_not_called()
            self.assertEqual(self.client.get("/dashboard", headers=self.headers).status_code, 200)

    def test_invalid_pages_do_not_query_database(self):
        with patch.object(db, "paginate") as paginate:
            for query in ("page=0", "page=-1", "page=no", "page=1.5", "page=", "page=2147483648",
                          "page=1&page=2", "unknown=value"):
                with self.subTest(query=query):
                    response = self.client.get("/dashboard?" + query, headers=self.headers)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.mimetype, "text/html")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
            paginate.assert_not_called()

    def test_renders_escaped_payments_and_pagination_without_raw_bank_data(self):
        payment = saved_payment(counterparty_name="<script>alert('test')</script>")
        with patch.object(db, "paginate", return_value=saved_page([payment], total=51)) as paginate:
            response = self.client.get("/dashboard", headers=self.headers)
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("05 Oct 2026", html)
        self.assertIn("−GBP 12.50", html)
        self.assertIn("Expense", html)
        self.assertIn("Automatic", html)
        self.assertIn('/dashboard?page=2', html)
        self.assertIn('/static/css/dashboard.css', html)
        self.assertNotIn("Never display raw bank data", html)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(paginate.call_args.kwargs["per_page"], 50)
        self.bank.assert_not_called()

    def test_empty_database_and_page_beyond_results_have_different_messages(self):
        with patch.object(db, "paginate", return_value=saved_page([], total=0)):
            response = self.client.get("/dashboard", headers=self.headers)
        self.assertIn("Import your Starling transactions", response.get_data(as_text=True))
        with patch.object(db, "paginate", return_value=saved_page([], number=2)) as paginate:
            response = self.client.get("/dashboard?page=2", headers=self.headers)
        self.assertIn("No transactions on this page", response.get_data(as_text=True))
        self.assertEqual(paginate.call_args.kwargs["page"], 2)

    def test_large_amounts_and_other_currency_units_are_preserved(self):
        payments = [saved_payment(direction="IN", amount_minor=2**63 - 1),
                    saved_payment(amount_minor=1234, currency="JPY")]
        with patch.object(db, "paginate", return_value=saved_page(payments, total=2)):
            html = self.client.get("/dashboard", headers=self.headers).get_data(as_text=True)
        self.assertIn("+GBP 92,233,720,368,547,758.07", html)
        self.assertIn("JPY 1,234 minor units", html)

    def test_database_errors_are_safe_and_missing_pages_have_privacy_headers(self):
        error = OperationalError("private SQL", {}, Exception("private connection details"))
        with patch.object(db, "paginate", side_effect=error), self.assertLogs("toms.errors") as logs:
            response = self.client.get("/dashboard", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Check PostgreSQL", response.get_data(as_text=True))
        self.assertNotIn("private", response.get_data(as_text=True))
        self.assertNotIn("private", "\n".join(logs.output))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        missing = self.client.get("/dashboard/missing", headers=self.headers)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.headers["Cache-Control"], "no-store")

    def test_balances_require_authentication_and_head_does_not_call_bank(self):
        with patch.object(db.session, "scalars") as accounts, patch("routes.dashboard.starling_request") as bank:
            self.assertEqual(self.client.get("/dashboard/balances").status_code, 503)
            self.assertEqual(self.client.head("/dashboard/balances", headers=self.headers).status_code, 200)
            accounts.assert_not_called()
            bank.assert_not_called()

    def test_live_balances_preserve_separate_accounts_zero_and_negative_amounts(self):
        accounts = [SimpleNamespace(account_uid="first", name="Current account", currency="GBP"),
                    SimpleNamespace(account_uid="second", name="Euro account", currency="EUR")]
        replies = [{"effectiveBalance": {"minorUnits": -123, "currency": "GBP"}},
                   {"effectiveBalance": {"minorUnits": 0, "currency": "EUR"}}]
        with patch.object(db.session, "scalars", return_value=accounts), \
                patch("routes.dashboard.starling_request", side_effect=replies) as bank:
            response = self.client.get("/dashboard/balances", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["balances"], [
            {"name": "Current account", "amount": "−GBP 1.23", "error": None},
            {"name": "Euro account", "amount": "EUR 0.00", "error": None},
        ])
        bank.assert_any_call("/api/v2/accounts/first/balance", scope="balance:read")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_invalid_balance_is_unavailable_rather_than_zero(self):
        account = SimpleNamespace(account_uid="first", name="Current account", currency="GBP")
        for payload in (None, {}, {"effectiveBalance": {"minorUnits": True, "currency": "GBP"}},
                        {"effectiveBalance": {"minorUnits": 500, "currency": "EUR"}}):
            with self.subTest(payload=payload), patch.object(db.session, "scalars", return_value=[account]), \
                    patch("routes.dashboard.starling_request", return_value=payload):
                response = self.client.get("/dashboard/balances", headers=self.headers)
                balance = response.get_json()["balances"][0]
                self.assertIsNone(balance["amount"])
                self.assertIn("invalid account balance", balance["error"])

    def test_balance_rate_limit_stops_further_requests_and_database_errors_are_safe(self):
        from services.banking.client import StarlingError

        accounts = [SimpleNamespace(account_uid=str(i), name="Account", currency="GBP") for i in range(2)]
        with patch.object(db.session, "scalars", return_value=accounts), \
                patch("routes.dashboard.starling_request", side_effect=StarlingError("Try later.", 429)) as bank:
            response = self.client.get("/dashboard/balances", headers=self.headers)
        self.assertEqual(bank.call_count, 1)
        self.assertTrue(all(card["amount"] is None for card in response.get_json()["balances"]))
        error = OperationalError("private SQL", {}, Exception("private credentials"))
        with patch.object(db.session, "scalars", side_effect=error), self.assertLogs("toms.errors"):
            response = self.client.get("/dashboard/balances", headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.mimetype, "application/json")
        self.assertNotIn("private", response.get_data(as_text=True))

    def test_account_selection_filters_transactions_and_preserves_pagination(self):
        account_uid = "11111111-1111-4111-8111-111111111111"
        self.accounts.return_value = [SimpleNamespace(account_uid=account_uid, name="Household", currency="GBP")]
        with patch.object(db, "paginate", return_value=saved_page([], total=51)) as paginate:
            response = self.client.get("/dashboard?account=" + account_uid, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Household / GBP", html)
        self.assertIn("account=" + account_uid + "&amp;page=2", html)
        query = paginate.call_args.args[0]
        self.assertIn("toms.transactions.account_uid =", str(query))
        self.assertIn(account_uid, query.compile().params.values())
        self.assertEqual(paginate.call_args.kwargs["page"], 1)

    def test_invalid_and_unavailable_accounts_do_not_load_transactions_or_balances(self):
        for account in ("bad-id", "11111111-1111-4111-8111-111111111111"):
            with self.subTest(account=account), patch.object(db, "paginate") as paginate, \
                    patch("routes.dashboard.starling_request") as bank:
                response = self.client.get("/dashboard?account=" + account, headers=self.headers)
                self.assertEqual(response.status_code, 400)
                response = self.client.get("/dashboard/balances?account=" + account, headers=self.headers)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.mimetype, "application/json")
                paginate.assert_not_called()
                bank.assert_not_called()

    def test_selected_balance_queries_only_the_selected_account(self):
        account_uid = "11111111-1111-4111-8111-111111111111"
        self.accounts.return_value = [SimpleNamespace(account_uid=account_uid, name="Household", currency="GBP")]
        with patch("routes.dashboard.starling_request", return_value={"effectiveBalance": {"minorUnits": 1250, "currency": "GBP"}}) as bank:
            response = self.client.get("/dashboard/balances?account=" + account_uid, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        query = self.accounts.call_args.args[0]
        self.assertIn("toms.accounts.account_uid =", str(query))
        self.assertIn(account_uid, query.compile().params.values())
        bank.assert_called_once_with("/api/v2/accounts/" + account_uid + "/balance", scope="balance:read")
