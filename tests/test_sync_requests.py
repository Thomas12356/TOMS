"""Sync request bodies must be read even without Content-Length."""

import io
import unittest
from unittest.mock import patch

from app import app


class SyncRequestTests(unittest.TestCase):
    def setUp(self):
        config = patch.dict(app.config, {"APP_API_KEY": "test-key"})
        config.start()
        self.addCleanup(config.stop)
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer test-key"}

    def streamed_post(self, body, *, content_type="application/json", headers=None):
        return self.client.post("/sync/transactions", headers=self.headers if headers is None else headers,
            content_type=content_type, environ_overrides={"CONTENT_LENGTH": "", "wsgi.input_terminated": True,
                                                        "wsgi.input": io.BytesIO(body)})

    def test_streamed_options_are_forwarded_and_empty_body_uses_defaults(self):
        with patch("routes.sync.store"), patch("routes.sync.run_sync", return_value={"status": "completed"}) as run:
            response = self.streamed_post(b'{"start":"2026-09-01","mode":"history"}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(run.call_args.args[1], {"start": "2026-09-01", "mode": "history"})
            for streamed in (False, True):
                response = self.streamed_post(b"") if streamed else self.client.post("/sync/transactions", headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(run.call_args.args[1], {})

    def test_invalid_streamed_bodies_never_start_sync(self):
        with patch("routes.sync.store") as store, patch("routes.sync.run_sync") as run:
            for body, content_type in ((b'{"start":"2026-09-01"}', "text/plain"),
                                       (b"{broken", "application/json"),
                                       (b"null", "application/json"),
                                       (b"[]", "application/json"),
                                       (b'{"unexpected":true}', "application/json")):
                with self.subTest(body=body, content_type=content_type):
                    self.assertEqual(self.streamed_post(body, content_type=content_type).status_code, 400)
            self.assertEqual(self.streamed_post(b'{"mode":"history"}', headers={}).status_code, 401)
            store.assert_not_called()
            run.assert_not_called()
