import base64
import httpx
import unittest
from unittest.mock import patch

from app import app
from services.rate_limit import RateLimitError


ACCOUNT = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
CATEGORY = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
ITEM = "cccccccc-cccc-4ccc-cccc-cccccccccccc"


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        config = patch.dict(app.config, {"APP_API_KEY": "test-app-key", "TESTING": True})
        config.start()
        self.addCleanup(config.stop)
        token = patch.dict("os.environ", {"STARLING_ACCESS_TOKEN": "fake-bank-token"})
        token.start()
        self.addCleanup(token.stop)
        limiter = patch("services.starling.acquire_slot")
        self.limiter = limiter.start()
        self.addCleanup(limiter.stop)

    def test_unauthenticated_banking_routes_never_call_bank(self):
        with patch("services.starling.http_client.send") as send:
            for method, path in [("GET", "/starling/accounts"), ("GET", "/starling/account-holder/name"),
                                 ("GET", "/starling/test"), ("POST", "/starling/test"),
                                 ("PUT", f"/starling/feed/account/{ACCOUNT}/category/{CATEGORY}/{ITEM}/receipt")]:
                with self.subTest(method=method, path=path):
                    response = self.client.open(path, method=method)
                    self.assertEqual(response.status_code, 401)
                    self.assertIn("Basic", response.headers["WWW-Authenticate"])
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
            send.assert_not_called()
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_bearer_and_browser_basic_auth_work(self):
        basic = base64.b64encode(b"api:test-app-key").decode()
        for header in ("Bearer test-app-key", "Basic " + basic):
            with self.subTest(header=header):
                with patch("services.starling.http_client.send", return_value=httpx.Response(200, json={"accountHolderName": "Test User"})) as send:
                    response = self.client.get("/starling/account-holder/name", headers={"Authorization": header})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(send.call_args.args[0].headers["Authorization"], "Bearer fake-bank-token")

    def test_wrong_key_and_missing_configuration_fail_closed(self):
        with patch("services.starling.http_client.send") as send:
            response = self.client.get("/starling/accounts", headers={"Authorization": "Bearer fake-bank-token"})
            self.assertEqual(response.status_code, 401)
            with patch.dict(app.config, {"APP_API_KEY": ""}):
                self.assertEqual(self.client.get("/starling/accounts").status_code, 503)
            send.assert_not_called()

    def test_missing_route_and_error_responses_disable_caching(self):
        self.assertEqual(self.client.get("/starling/nonexistent").headers["Cache-Control"], "no-store")
        with patch("services.starling.http_client.send", side_effect=httpx.RemoteProtocolError("incomplete response")):
            response = self.client.get(f"/starling/accounts/{ACCOUNT}/balance",
                                       headers={"Authorization": "Bearer test-app-key"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("error", response.get_json())
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_redirects_are_blocked_instead_of_forwarding_credentials(self):
        for destination in ("https://other.example/path", "http://api.starlingbank.com/path",
                            "https://api.starlingbank.com/other-path"):
            with self.subTest(destination=destination):
                requests = []

                def upstream(request):
                    requests.append(request)
                    return httpx.Response(302, headers={"Location": destination})

                # Use the real client send path to verify redirects aren't followed.
                from services.starling import http_client
                with httpx.Client(base_url=http_client.base_url, timeout=http_client.timeout,
                                  follow_redirects=http_client.follow_redirects,
                                  transport=httpx.MockTransport(upstream)) as bank:
                    with patch("services.starling.http_client", bank):
                        response = self.client.get("/starling/accounts",
                                                   headers={"Authorization": "Bearer test-app-key"})
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].url.host, "api.starlingbank.com")
                self.assertEqual(response.status_code, 502)
                self.assertIn("redirect", response.get_json()["error"])
                self.assertNotIn("fake-bank-token", response.get_data(as_text=True))

    def test_large_receipt_amount_returns_400_without_bank_call(self):
        body = {"metadataSource": "CUSTOMER", "receiptIdentifier": "test", "totalAmount": 10**400,
                "receiptMerchant": {}, "items": [], "paymentMethods": []}
        with patch("services.starling.http_client.send") as send:
            response = self.client.put(f"/starling/feed/account/{ACCOUNT}/category/{CATEGORY}/{ITEM}/receipt",
                                       json=body, headers={"Authorization": "Bearer test-app-key"})
            self.assertEqual(response.status_code, 400)
            send.assert_not_called()

    def test_all_routes_use_shared_limiter_and_return_retry_after(self):
        self.limiter.side_effect = RateLimitError(3)
        with patch("services.starling.http_client.send") as send:
            for path in ("/starling/account-holder/name", "/starling/accounts",
                         f"/starling/accounts/{ACCOUNT}/balance"):
                response = self.client.get(path, headers={"Authorization": "Bearer test-app-key"})
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers["Retry-After"], "3")
            send.assert_not_called()
        self.assertEqual(self.limiter.call_count, 3)


if __name__ == "__main__":
    unittest.main()
