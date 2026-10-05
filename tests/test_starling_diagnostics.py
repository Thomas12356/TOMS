import httpx
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from app import app
from services.starling_diagnostics import CHECKS


ACCOUNT = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
CATEGORY = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
ITEM = "cccccccc-cccc-4ccc-cccc-cccccccccccc"
PAYEE_ACCOUNT = "dddddddd-dddd-4ddd-dddd-dddddddddddd"


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        config = patch.dict(app.config, {"APP_API_KEY": "test-app-key"})
        config.start()
        self.addCleanup(config.stop)
        self.client.environ_base["HTTP_AUTHORIZATION"] = "Bearer test-app-key"
        token = patch.dict("os.environ", {"STARLING_ACCESS_TOKEN": "fake-token",
                                          "STARLING_ACCOUNT_UID": ACCOUNT,
                                          "STARLING_CATEGORY_UID": CATEGORY})
        token.start()
        self.addCleanup(token.stop)
        limiter = patch("services.starling.acquire_slot")
        self.limiter = limiter.start()
        self.addCleanup(limiter.stop)

    def fake_response(self, request, **kwargs):
        path = urlparse(str(request.url)).path
        if path == "/api/v2/accounts":
            data = {"accounts": [{"accountUid": ACCOUNT, "defaultCategory": CATEGORY}]}
        elif path == "/api/v2/payees":
            data = {"payees": [{"payeeUid": ITEM, "accounts": [{"payeeAccountUid": PAYEE_ACCOUNT}]}]}
        elif path == "/api/v2/direct-debit/mandates":
            data = {"mandates": [{"uid": ITEM}]}
        elif path.endswith("/savings-goals"):
            data = {"savingsGoalList": [{"savingsGoalUid": ITEM}]}
        elif path.endswith("/standing-orders"):
            data = {"standingOrders": [{"paymentOrderUid": ITEM}]}
        elif path == f"/api/v2/feed/account/{ACCOUNT}/category/{CATEGORY}":
            data = {"feedItems": [{"feedItemUid": ITEM}]}
        elif path.endswith("/account-holder/name"):
            data = {"accountHolderName": "Private name not included in report"}
        elif request.headers["Accept"] != "application/json":
            return httpx.Response(200, content=b"test download bytes")
        else:
            data = {"ok": True}
        return httpx.Response(200, json=data)

    def test_discovers_ids_and_exercises_all_reads_without_writes(self):
        with patch("services.starling.http_client.send", side_effect=self.fake_response) as send:
            response = self.client.post("/starling/test")
        self.assertEqual(response.status_code, 200)
        report = response.get_json()
        self.assertEqual(report["summary"], {"total": 20, "passed": 19, "failed": 0, "skipped": 1})
        self.assertFalse(report["allPassed"])
        self.assertEqual(len(send.call_args_list), 19)
        self.assertTrue(all(call.args[0].method == "GET" for call in send.call_args_list))
        urls = [str(call.args[0].url) for call in send.call_args_list]
        self.assertTrue(any(f"/payees/{ITEM}/account/{PAYEE_ACCOUNT}/payments" in url for url in urls))
        self.assertTrue(any(urlparse(url).path == "/api/v2/accounts" for url in urls))
        self.assertEqual(self.limiter.call_count, 19)
        self.assertNotIn("Private name", response.get_data(as_text=True))
        self.assertNotIn("fake-token", response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_missing_resources_are_skipped_and_errors_do_not_abort(self):
        def upstream(request, **kwargs):
            if str(request.url).endswith("/accounts"):
                return httpx.Response(403)
            return httpx.Response(200, json={})
        with patch("services.starling.http_client.send", side_effect=upstream):
            response = self.client.post("/starling/test", json={})
        report = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(report["summary"]["failed"], 2)  # discovery denied and malformed name
        self.assertGreater(report["summary"]["skipped"], 0)
        self.assertFalse(report["allPassed"])

    def test_browser_get_runs_read_checks(self):
        with patch("services.starling.http_client.send", side_effect=self.fake_response) as send:
            response = self.client.get("/starling/test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["summary"]["passed"], 19)
        self.assertTrue(all(call.args[0].method == "GET" for call in send.call_args_list))

    def test_get_cannot_enable_writes_and_head_does_not_call_starling(self):
        with patch("services.starling.http_client.send") as send:
            response = self.client.get("/starling/test", json={"includeMetadataWrite": True})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.client.get("/starling/test?includeMetadataWrite=true").status_code, 400)
            self.assertEqual(self.client.head("/starling/test").status_code, 200)
            send.assert_not_called()

    def test_invalid_options_do_not_call_starling(self):
        with patch("services.starling.http_client.send") as send:
            for options in [[], {"accountUid": "invalid"}, {"receipt": {}},
                            {"includeMetadataWrite": True}, {"includeMetadataWrite": "false"},
                            {"url": "https://example.com"}]:
                with self.subTest(options=options):
                    self.assertEqual(self.client.post("/starling/test", json=options).status_code, 400)
            send.assert_not_called()

    def test_explicit_opt_in_performs_one_metadata_write(self):
        options = {"accountUid": ACCOUNT, "categoryUid": CATEGORY, "feedItemUid": ITEM,
                   "includeMetadataWrite": True,
                   "receipt": {"metadataSource": "CUSTOMER", "receiptIdentifier": "test",
                               "totalAmount": 0, "receiptMerchant": {}, "items": [], "paymentMethods": []}}
        with patch("services.starling.http_client.send", side_effect=self.fake_response) as send:
            response = self.client.post("/starling/test", json=options)
        report = response.get_json()
        self.assertTrue(report["allPassed"])
        writes = [call for call in send.call_args_list if call.args[0].method == "PUT"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(json.loads(writes[0].args[0].content), options["receipt"])

    def test_rate_limit_stops_further_bank_requests(self):
        with patch("services.starling.http_client.send", return_value=httpx.Response(429)) as send:
            report = self.client.post("/starling/test").get_json()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertEqual(report["summary"]["skipped"], 19)

    def test_permission_allowlist_matches_user_list_and_ignores_extra_routes(self):
        expected = {"account-list:read", "account-holder-name:read", "balance:read", "confirmation-of-funds:read",
                    "mandate:read", "metadata:create", "metadata:edit", "payee:read",
                    "payee-image:read", "payee-transaction:read", "pay-local:read", "receipts:read",
                    "savings-goal:read", "savings-goal-transfer:read", "scheduled-payment:read",
                    "space:read", "standing-order:read", "statement-pdf:read", "statement-csv:read",
                    "feed-export-csv:read", "transaction:read"}
        self.assertEqual({scope for scopes in CHECKS.values() for scope in scopes}, expected)
        with patch("services.starling.http_client.send", side_effect=self.fake_response):
            report = self.client.get("/starling/test").get_json()
        self.assertEqual({scope for result in report["results"] for scope in result["scopes"]}, expected)
        self.assertEqual(len(report["results"]), 20)

    def test_token_alone_discovers_account_and_category(self):
        with patch.dict("os.environ", {"STARLING_ACCOUNT_UID": "", "STARLING_CATEGORY_UID": ""}):
            with patch("services.starling.http_client.send", side_effect=self.fake_response) as send:
                report = self.client.get("/starling/test").get_json()
        balance = next(result for result in report["results"] if "balance:read" in result["scopes"])
        self.assertEqual(balance["status"], "passed")
        self.assertTrue(any(urlparse(str(call.args[0].url)).path == "/api/v2/accounts" for call in send.call_args_list))
        self.assertEqual(report["summary"]["passed"], 19)

    def test_discovery_denied_is_reported_and_missing_ids_are_skipped(self):
        def upstream(request, **kwargs):
            if urlparse(str(request.url)).path == "/api/v2/accounts":
                return httpx.Response(403)
            return self.fake_response(request, **kwargs)
        with patch.dict("os.environ", {"STARLING_ACCOUNT_UID": "", "STARLING_CATEGORY_UID": ""}):
            with patch("services.starling.http_client.send", side_effect=upstream):
                report = self.client.get("/starling/test").get_json()
        discovery = next(result for result in report["results"] if "account-list:read" in result["scopes"])
        self.assertEqual(discovery["status"], "failed")
        self.assertIn("account-list:read", discovery["error"])
        balance = next(result for result in report["results"] if "balance:read" in result["scopes"])
        self.assertEqual(balance["status"], "skipped")

    def test_explicit_account_selects_its_own_category(self):
        def upstream(request, **kwargs):
            if urlparse(str(request.url)).path == "/api/v2/accounts":
                return httpx.Response(200, json={"accounts": [
                    {"accountUid": ACCOUNT, "defaultCategory": CATEGORY},
                    {"accountUid": ITEM, "defaultCategory": PAYEE_ACCOUNT},
                ]})
            return self.fake_response(request, **kwargs)
        with patch.dict("os.environ", {"STARLING_ACCOUNT_UID": "", "STARLING_CATEGORY_UID": ""}):
            with patch("services.starling.http_client.send", side_effect=upstream):
                report = self.client.post("/starling/test", json={"accountUid": ITEM}).get_json()
        feed = next(result for result in report["results"] if "transaction:read" in result["scopes"])
        self.assertIn(f"/account/{ITEM}/category/{PAYEE_ACCOUNT}", feed["url"])

    def test_account_override_discards_another_accounts_environment_category(self):
        def upstream(request, **kwargs):
            if urlparse(str(request.url)).path == "/api/v2/accounts":
                return httpx.Response(200, json={"accounts": [
                    {"accountUid": ACCOUNT, "defaultCategory": CATEGORY},
                    {"accountUid": ITEM, "defaultCategory": PAYEE_ACCOUNT},
                ]})
            return self.fake_response(request, **kwargs)
        with patch("services.starling.http_client.send", side_effect=upstream):
            report = self.client.post("/starling/test", json={"accountUid": ITEM}).get_json()
        feed = next(result for result in report["results"] if "transaction:read" in result["scopes"])
        self.assertIn(f"/account/{ITEM}/category/{PAYEE_ACCOUNT}", feed["url"])

    def test_explicit_category_overrides_discovery(self):
        with patch("services.starling.http_client.send", side_effect=self.fake_response):
            report = self.client.post("/starling/test", json={"accountUid": ACCOUNT, "categoryUid": ITEM}).get_json()
        feed = next(result for result in report["results"] if "transaction:read" in result["scopes"])
        self.assertIn(f"/account/{ACCOUNT}/category/{ITEM}", feed["url"])


if __name__ == "__main__":
    unittest.main()
