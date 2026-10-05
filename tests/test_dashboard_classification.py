"""Browser classification edits use real CSRF and rollback-only PostgreSQL."""

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from werkzeug.datastructures import MultiDict
from sqlalchemy.exc import OperationalError

from app import app
from models import BrowserSession, Transaction, TransactionIncome
from services.database.connection import db
from services.web.sessions import token_hash
from support import ApiTestCase, SavedTransactionTestCase


class ClassificationFormProtectionTests(ApiTestCase):
    def test_anonymous_and_missing_csrf_cannot_reach_payment_query(self):
        path = '/dashboard/transactions/' + '/'.join(str(uuid4()) for _ in range(3)) + '/classification'
        with patch.dict(app.config, {"SECRET_KEY": "a" * 64}), patch.object(db.session, "scalar") as query:
            for method in ("GET", "POST"):
                self.assertEqual(self.client.open(path, method=method).status_code, 302)
            query.assert_not_called()


    def test_bearer_edit_form_without_signing_key_fails_closed(self):
        path = '/dashboard/transactions/' + '/'.join(str(uuid4()) for _ in range(3)) + '/classification'
        with patch.dict(app.config, {"SECRET_KEY": None}), patch.object(db.session, "scalar") as query:
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 503)
            query.assert_not_called()


class DashboardClassificationTests(SavedTransactionTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(app.config, {"SECRET_KEY": "a" * 64, "SESSION_COOKIE_SECURE": False}))
        token = "f" * 64
        now = datetime.now(timezone.utc)
        self.session.add(BrowserSession(token_hash=token_hash(token), created_at=now, last_seen_at=now, expires_at=now + timedelta(hours=8)))
        self.session.commit()
        with self.client.session_transaction() as cookie:
            cookie["_user_id"] = token

    def path(self, item=None):
        return f"/dashboard/transactions/{self.account}/{self.category}/{item or self.outgoing}/classification"

    def form_token(self, path=None):
        response = self.client.get(path or self.path())
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))[1]

    def payment(self, item=None):
        self.session.expire_all()
        return self.session.get(Transaction, (self.account, self.category, item or self.outgoing))

    def test_direction_choices_and_transaction_identity(self):
        response = self.client.get(self.path())
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('value="income"', html)
        self.assertIn('value="expense"', html)
        self.assertIn('−GBP 12.50', html)
        html = self.client.get(self.path(self.incoming)).get_data(as_text=True)
        self.assertIn('value="income"', html)
        self.assertNotIn('value="expense"', html)
        self.bank.assert_not_called()

    def test_save_notes_update_and_restore_automatic_with_return_context(self):
        path = self.path() + f"?account={self.account}&page=2"
        response = self.client.post(path, data={"type": "internal_transfer", "notes": "  Other account  ", "action": "save", "csrf_token": self.form_token(path)})
        self.assertEqual(response.status_code, 303)
        self.assertIn('account=' + self.account, response.headers['Location'])
        self.assertIn('page=2', response.headers['Location'])
        self.assertEqual(self.payment().classification.type, "internal_transfer")
        self.assertEqual(self.payment().classification.notes, "Other account")
        response = self.client.post(self.path(), data={"type": "refund", "notes": "", "csrf_token": self.form_token()})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.payment().classification.type, "refund")
        self.assertIsNone(self.payment().classification.notes)
        response = self.client.post(self.path(), data={"action": "automatic", "csrf_token": self.form_token()})
        self.assertEqual(response.status_code, 303)
        self.assertIsNone(self.payment().classification)
        self.assertEqual(self.payment().amount_minor, 1250)
        self.bank.assert_not_called()

    def test_missing_csrf_blocks_save_and_reset(self):
        for action in ("save", "automatic"):
            with patch.object(db.session, "scalar") as query:
                response = self.client.post(self.path(), data={"type": "refund", "action": action})
                self.assertEqual(response.status_code, 400)
                query.assert_not_called()
        self.assertIsNone(self.payment().classification)

    def test_invalid_direction_notes_type_and_actions_leave_record_unchanged(self):
        for changes in ({"type": "income"}, {"type": "unknown"}, {"notes": "x" * 2001},
                        {"notes": "bad\x00text"}, {"action": "delete"}, {"unexpected": "field"}):
            body = {"type": "refund", "notes": "keep me", "action": "save", "csrf_token": self.form_token()}
            body.update(changes)
            with self.subTest(changes=changes):
                response = self.client.post(self.path(), data=body)
                self.assertEqual(response.status_code, 400)
                self.assertIsNone(self.payment().classification)
        body = MultiDict([("type", "refund"), ("type", "expense"), ("csrf_token", self.form_token())])
        self.assertEqual(self.client.post(self.path(), data=body).status_code, 400)
        self.assertIsNone(self.payment().classification)

    def test_income_details_prevent_incompatible_changes_and_reset(self):
        payment = self.payment(self.incoming)
        payment.income = TransactionIncome(income_type="other", tax_treatment="unknown", recorded_currency="GBP")
        self.session.commit()
        path = self.path(self.incoming)
        for body in ({"type": "refund"}, {"action": "automatic"}):
            body["csrf_token"] = self.form_token(path)
            self.assertEqual(self.client.post(path, data=body).status_code, 400)
            self.assertIsNotNone(self.payment(self.incoming).income)
        self.assertEqual(self.client.post(path, data={"type": "income", "csrf_token": self.form_token(path)}).status_code, 303)
        self.assertIsNotNone(self.payment(self.incoming).income)

    def test_missing_payment_and_invalid_return_url_fail_safely(self):
        self.assertEqual(self.client.get(self.path(str(uuid4()))).status_code, 404)
        for query in ('page=0', 'page=1&page=2', 'next=https://evil.example', 'account=bad'):
            self.assertEqual(self.client.get(self.path() + '?' + query).status_code, 400)

    def test_saved_notes_are_escaped_and_validation_preserves_form_values(self):
        notes = '</textarea><script>alert(1)</script>'
        self.client.post(self.path(), data={"type": "refund", "notes": notes, "csrf_token": self.form_token()})
        html = self.client.get(self.path()).get_data(as_text=True)
        self.assertIn('&lt;/textarea&gt;&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        response = self.client.post(self.path(), data={"type": "income", "notes": notes, "csrf_token": self.form_token()})
        self.assertEqual(response.status_code, 400)
        self.assertIn('&lt;/textarea&gt;', response.get_data(as_text=True))
        self.assertEqual(self.payment().classification.type, "refund")

    def test_save_database_failure_does_not_disclose_data(self):
        token = self.form_token()
        # The loader commits idle activity before the route saves the payment.
        original_commit = self.session.commit
        count = 0
        def fail_second_commit():
            nonlocal count
            count += 1
            if count == 2:
                raise OperationalError("private SQL", {}, Exception("private-password"))
            return original_commit()
        with patch.object(db.session, "commit", side_effect=fail_second_commit), self.assertLogs("toms.errors"):
            response = self.client.post(self.path(), data={"type": "refund", "csrf_token": token})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private-password', response.get_data(as_text=True))
        self.assertIsNone(self.payment().classification)
