import httpx
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app import app
from services.banking.client import starling_request


ACCOUNT = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
CATEGORY = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
ITEM = "cccccccc-cccc-4ccc-cccc-cccccccccccc"


class StarlingTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        config = patch.dict(app.config, {"APP_API_KEY": "test-app-key"})
        config.start()
        self.addCleanup(config.stop)
        self.client.environ_base["HTTP_AUTHORIZATION"] = "Bearer test-app-key"
        limiter = patch("services.banking.client.acquire_slot")
        limiter.start()
        self.addCleanup(limiter.stop)
        self.token = patch.dict("os.environ", {"STARLING_ACCESS_TOKEN": "fake-token"})
        self.token.start()
        self.addCleanup(self.token.stop)

    def test_all_read_routes_match_the_expected_upstream_paths(self):
        # Representative calls for every requested read permission, including
        # formats that must preserve bytes instead of parsing them as JSON.
        cases = [
            ("/accounts", "account-list:read", "application/json", ""),
            (f"/accounts/{ACCOUNT}/balance", "balance:read", "application/json", ""),
            (f"/accounts/{ACCOUNT}/confirmation-of-funds", "confirmation-of-funds:read", "application/json", "?targetAmountInMinorUnits=1250"),
            ("/direct-debit/mandates", "mandate:read", "application/json", ""),
            ("/payees", "payee:read", "application/json", ""),
            (f"/payees/{ITEM}/image", "payee-image:read", "image/png", ""),
            (f"/payees/{ITEM}/account/{ACCOUNT}/payments", "payee-transaction:read", "application/json", "?since=2026-01-01"),
            (f"/payments/local/payment-order/{ITEM}", "pay-local:read", "application/json", ""),
            (f"/feed/account/{ACCOUNT}/category/{CATEGORY}/{ITEM}/receipts", "receipts:read", "application/json", ""),
            (f"/account/{ACCOUNT}/savings-goals", "savings-goal:read", "application/json", ""),
            (f"/account/{ACCOUNT}/savings-goals/{ITEM}/recurring-transfer", "savings-goal-transfer:read", "application/json", ""),
            (f"/payees/{ITEM}/account/{ACCOUNT}/scheduled-payments", "scheduled-payment:read", "application/json", ""),
            (f"/account/{ACCOUNT}/spaces", "space:read", "application/json", ""),
            (f"/payments/local/account/{ACCOUNT}/category/{CATEGORY}/standing-orders", "standing-order:read", "application/json", ""),
            (f"/accounts/{ACCOUNT}/statement/pdf", "statement-pdf:read", "application/pdf", "?yearMonth=2026-09"),
            (f"/accounts/{ACCOUNT}/statement/csv", "statement-csv:read", "text/csv", "?yearMonth=2026-09"),
            (f"/accounts/{ACCOUNT}/feed-export", "feed-export-csv:read", "text/csv", "?start=2026-01-01&end=2026-09-30"),
            (f"/feed/account/{ACCOUNT}/category/{CATEGORY}", "transaction:read", "application/json", "?changesSince=2026-01-01T00:00:00Z"),
        ]
        for path, scope, media_type, query in cases:
            with self.subTest(scope=scope):
                payload = b'{"test":true}' if media_type == "application/json" else b"\x00test\xff"
                with patch("services.banking.client.http_client.send", return_value=httpx.Response(200, content=payload)) as send:
                    response = self.client.get("/starling" + path + query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, media_type)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                if media_type != "application/json":
                    self.assertEqual(response.data, payload)
                    self.assertIn("attachment", response.headers["Content-Disposition"])
                upstream = send.call_args.args[0]
                expected = path
                if "/statement/" in path:
                    expected = path.rsplit("/", 1)[0] + "/download"
                self.assertEqual(urlparse(str(upstream.url)).path, "/api/v2" + expected)
                self.assertEqual(parse_qs(urlparse(str(upstream.url)).query), parse_qs(query[1:]))
                self.assertEqual(upstream.headers["Authorization"], "Bearer fake-token")
                self.assertEqual(upstream.headers["Accept"], media_type)
                self.assertEqual(upstream.extensions["timeout"]["read"], 10)

    def test_invalid_query_parameters_do_not_call_starling(self):
        queries = [
            (f"/accounts/{ACCOUNT}/confirmation-of-funds", ""),
            (f"/accounts/{ACCOUNT}/confirmation-of-funds", "?targetAmountInMinorUnits=-1"),
            (f"/accounts/{ACCOUNT}/confirmation-of-funds", "?targetAmountInMinorUnits=1.5"),
            (f"/accounts/{ACCOUNT}/statement/pdf", "?yearMonth=2026-13"),
            (f"/accounts/{ACCOUNT}/feed-export", "?start=2026-02-30"),
            (f"/accounts/{ACCOUNT}/feed-export", "?start=2026-09-30&end=2026-01-01"),
            (f"/accounts/{ACCOUNT}/feed-export", "?start=2026-01-01&start=2026-01-02"),
            (f"/accounts/{ACCOUNT}/feed-export", "?start=2026-01-01&url=https://example.com"),
            (f"/feed/account/{ACCOUNT}/category/{CATEGORY}", "?changesSince=2026-01-01"),
        ]
        with patch("services.banking.client.http_client.send") as send:
            for path, query in queries:
                with self.subTest(path=path, query=query):
                    response = self.client.get("/starling" + path + query)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.get_json())
            send.assert_not_called()

    def test_create_and_edit_receipt_use_put_and_json_body(self):
        body = {"metadataSource": "CUSTOMER", "receiptIdentifier": "test-receipt",
                "totalAmount": 12.5, "receiptMerchant": {"identifier": "Test shop"},
                "items": [{"description": "Coffee", "amount": 12.5}],
                "paymentMethods": [{"description": "Card", "amount": 12.5}]}
        path = f"/starling/feed/account/{ACCOUNT}/category/{CATEGORY}/{ITEM}/receipt"
        for edit in (False, True):
            with self.subTest(edit=edit):
                payload = dict(body)
                if edit:
                    payload["receiptUid"] = ITEM
                with patch("services.banking.client.http_client.send", return_value=httpx.Response(200, json={"receiptUid": "test"})) as send:
                    response = self.client.put(path, json=payload)
                self.assertEqual(response.status_code, 200)
                upstream = send.call_args.args[0]
                self.assertEqual(upstream.method, "PUT")
                self.assertEqual(json.loads(upstream.content), payload)
                self.assertEqual(upstream.headers["Content-Type"], "application/json")

    def test_invalid_receipts_do_not_call_starling(self):
        path = f"/starling/feed/account/{ACCOUNT}/category/{CATEGORY}/{ITEM}/receipt"
        with patch("services.banking.client.http_client.send") as send:
            self.assertEqual(self.client.put(path, json={}).status_code, 400)
            self.assertEqual(self.client.put(path, data="not JSON").status_code, 400)
            send.assert_not_called()

    def test_missing_token_and_upstream_failures(self):
        path = f"/starling/accounts/{ACCOUNT}/balance"
        with patch.dict("os.environ", {"STARLING_ACCESS_TOKEN": ""}):
            with patch("services.banking.client.http_client.send") as send:
                self.assertEqual(self.client.get(path).status_code, 503)
                send.assert_not_called()
        for upstream_status, local_status in [(401, 502), (403, 502), (404, 404), (429, 429), (500, 502)]:
            with self.subTest(status=upstream_status):
                error = httpx.Response(upstream_status, text="fake-token")
                with patch("services.banking.client.http_client.send", return_value=error):
                    response = self.client.get(path)
                self.assertEqual(response.status_code, local_status)
                self.assertNotIn("fake-token", response.get_data(as_text=True))
        with patch("services.banking.client.http_client.send", side_effect=httpx.ConnectError("fake-token")):
            self.assertEqual(self.client.get(path).status_code, 502)

    def test_empty_and_invalid_json_responses(self):
        path = f"/starling/accounts/{ACCOUNT}/balance"
        with patch("services.banking.client.http_client.send", return_value=httpx.Response(200)):
            self.assertEqual(self.client.get(path).status_code, 204)
        with patch("services.banking.client.http_client.send", return_value=httpx.Response(200, content=b"not JSON")):
            self.assertEqual(self.client.get(path).status_code, 502)

    def test_only_starling_api_paths_are_accepted(self):
        with self.assertRaises(ValueError):
            starling_request("https://example.com")

    def test_original_name_endpoint_still_works(self):
        with patch("services.banking.client.http_client.send", return_value=httpx.Response(200, json={"accountHolderName": "Test User"})):
            response = self.client.get("/starling/account-holder/name")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"accountHolderName": "Test User"})


if __name__ == "__main__":
    unittest.main()
