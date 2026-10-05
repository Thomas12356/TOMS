"""Invalid input, bounded request bodies and private error diagnostics."""

import io
import unittest
from datetime import timezone
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app import app
from services.database import db
from services.error_logging import log_failure
from services.transaction_sync import SyncError, run_sync
from services.validation import timestamp
from support import ApiTestCase
from test_transaction_sync import MemoryStore, NOW


ACCOUNT = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
CATEGORY = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
ITEM = "cccccccc-cccc-4ccc-cccc-cccccccccccc"
TRANSACTION = f"/transactions/{ACCOUNT}/{CATEGORY}/{ITEM}"


class InputValidationTests(ApiTestCase):
    def test_out_of_range_utc_timestamps_are_validation_errors(self):
        invalid = ("0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00")
        with patch("routes.sync.SyncStore") as store, patch("routes.starling.starling_request") as bank:
            for value in invalid:
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        timestamp(value)
                    response = self.client.post("/sync/transactions", headers=self.headers, json={"start": value})
                    self.assertEqual(response.status_code, 400)
                    response = self.client.get(f"/starling/feed/account/{ACCOUNT}/category/{CATEGORY}",
                        headers=self.headers, query_string={"changesSince": value})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.get_json())
            store.assert_not_called()
            bank.assert_not_called()
        self.assertEqual(timestamp("0001-01-01T01:00:00+01:00").year, 1)
        self.assertEqual(timestamp("9999-12-31T22:59:59-01:00").year, 9999)
        self.assertEqual(timestamp("2026-10-04T12:00:00+01:00").hour, 11)
        self.assertEqual(timestamp("2026-10-04T12:00:00+01:00").tzinfo, timezone.utc)

    def test_invalid_text_never_reaches_database(self):
        with patch("routes.transactions.saved_transaction") as lookup:
            for field in ("source_name", "adjustment_notes"):
                for value in ("hello\x00world", "broken\ud800"):
                    with self.subTest(field=field, value=repr(value)):
                        response = self.client.put(TRANSACTION + "/income", headers=self.headers,
                            json={"income_type": "roofing", field: value})
                        self.assertEqual(response.status_code, 400)
                        self.assertIn(field, response.get_json()["error"])
            for value in ("hello\x00world", "broken\ud800"):
                response = self.client.put(TRANSACTION + "/classification", headers=self.headers,
                    json={"type": "income", "notes": value})
                self.assertEqual(response.status_code, 400)
                self.assertIn("notes", response.get_json()["error"])
            lookup.assert_not_called()
        with patch.object(db, "paginate") as paginate:
            response = self.client.get("/transactions?status=bad%00status", headers=self.headers)
            self.assertEqual(response.status_code, 400)
            self.assertIn("status", response.get_json()["error"])
            paginate.assert_not_called()

    def test_exact_size_streamed_body_is_not_rejected_or_truncated(self):
        body = b'{"mode":"history"}'
        with patch.dict(app.config, {"MAX_CONTENT_LENGTH": len(body)}), \
                patch("routes.sync.SyncStore"), \
                patch("routes.sync.run_sync", return_value={"status": "completed"}) as run:
            for streamed in (False, True):
                if streamed:
                    response = self.client.post("/sync/transactions", headers=self.headers,
                        content_type="application/json", environ_overrides={"CONTENT_LENGTH": "",
                            "wsgi.input_terminated": True, "wsgi.input": io.BytesIO(body)})
                else:
                    response = self.client.post("/sync/transactions", headers=self.headers,
                                                content_type="application/json", data=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(run.call_args.args[1], {"mode": "history"})

    def test_request_size_cap_rejects_declared_and_streamed_bodies(self):
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 1024 * 1024)
        with patch.dict(app.config, {"MAX_CONTENT_LENGTH": 128}), \
                patch("routes.sync.SyncStore") as store, \
                patch("routes.transactions.saved_transaction") as lookup, \
                patch("routes.starling.starling_request") as bank:
            body = b'{"padding":"' + b"x" * 200 + b'"}'
            for method, path in (("POST", "/sync/transactions"), ("POST", "/starling/test"),
                                 ("PUT", TRANSACTION + "/income"),
                                 ("PUT", TRANSACTION + "/classification"),
                                 ("PUT", f"/starling/feed/account/{ACCOUNT}/category/{CATEGORY}/{ITEM}/receipt")):
                with self.subTest(path=path):
                    response = self.client.open(path, method=method, data=body,
                        headers=self.headers, content_type="application/json")
                    self.assertEqual(response.status_code, 413)
                    self.assertEqual(response.get_json()["max_bytes"], 128)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
            response = self.client.post("/sync/transactions", headers=self.headers, content_type="application/json",
                environ_overrides={"CONTENT_LENGTH": "", "wsgi.input_terminated": True,
                                   "wsgi.input": io.BytesIO(body)})
            self.assertEqual(response.status_code, 413)
            response = self.client.put(TRANSACTION + "/income", data=body, content_type="application/json")
            self.assertEqual(response.status_code, 401)
            store.assert_not_called()
            lookup.assert_not_called()
            bank.assert_not_called()


class ErrorLoggingTests(unittest.TestCase):
    def database_error(self):
        class DriverError(Exception):
            sqlstate = "23514"
        return OperationalError("private SQL statement", {"token": "private SQL parameter"},
                                DriverError("private driver credentials"))

    def test_diagnostic_metadata_excludes_sql_values_and_exception_messages(self):
        error = self.database_error()
        with self.assertLogs("toms.errors", level="ERROR") as logs:
            try:
                raise error
            except OperationalError as caught:
                log_failure("test.database", caught)
        output = "\n".join(logs.output)
        self.assertIn("exception=OperationalError", output)
        self.assertIn("sqlstate=23514", output)
        self.assertIn("test_validation_and_errors.py:", output)
        for sensitive in ("private SQL statement", "private SQL parameter", "private driver credentials"):
            self.assertNotIn(sensitive, output)
        self.assertIsNone(logs.records[0].exc_info)

    def test_database_handlers_log_private_metadata_and_keep_responses_safe(self):
        error = self.database_error()
        headers = {"Authorization": "Bearer test-key"}
        with patch.dict(app.config, {"APP_API_KEY": "test-key"}):
            client = app.test_client()
            for target, path in (("app.check_database", "/health/db"),
                                 ("routes.transactions.db.paginate", "/transactions"),
                                 ("routes.reports.db.session.execute", "/reports/monthly?month=2000-02"),
                                 ("routes.sync.SyncStore", "/sync/runs")):
                with self.subTest(path=path), patch(target, side_effect=error), self.assertLogs("toms.errors") as logs:
                    response = client.get(path, headers=headers)
                    self.assertEqual(response.status_code, 503)
                    self.assertIn("sqlstate=23514", "\n".join(logs.output))
                    self.assertNotIn("private", response.get_data(as_text=True))
                    self.assertNotIn("private", "\n".join(logs.output))

    def test_sync_failures_record_diagnostics_and_release_lock(self):
        for error in (self.database_error(), RuntimeError("private upstream payload")):
            with self.subTest(error_type=type(error).__name__):
                store = MemoryStore()
                with patch("services.transaction_sync.discover_accounts", side_effect=error), \
                        self.assertLogs("toms.errors") as logs:
                    with self.assertRaises(SyncError) as raised:
                        run_sync(store, {}, now=NOW)
                self.assertEqual(raised.exception.status_code, 503 if isinstance(error, OperationalError) else 500)
                self.assertEqual(store.status, "failed")
                self.assertTrue(store.unlocked)
                self.assertIn("run_uid=" + raised.exception.run_uid, "\n".join(logs.output))
                self.assertNotIn("private", "\n".join(logs.output))
                self.assertNotIn("private", str(raised.exception))

    def test_sync_recording_and_unlock_failures_are_logged(self):
        store = MemoryStore()
        with patch("services.transaction_sync.discover_accounts", side_effect=RuntimeError("private payload")), \
                patch.object(store, "finish_run", side_effect=self.database_error()), \
                patch.object(store, "unlock", side_effect=self.database_error()), \
                self.assertLogs("toms.errors") as logs:
            with self.assertRaises(SyncError):
                run_sync(store, {}, now=NOW)
        output = "\n".join(logs.output)
        self.assertIn("operation=sync.record_failure", output)
        self.assertIn("operation=sync.unlock", output)
        self.assertNotIn("private", output)
