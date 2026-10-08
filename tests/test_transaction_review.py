"""Real PostgreSQL confirmation, stale versions and browser protection."""
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from sqlalchemy.exc import OperationalError
from uuid import uuid4

from app import app
from models import BrowserSession, Transaction, TransactionIncome
from services.database.connection import db
from services.transactions.classification import save_classification
from services.transactions.review import REVIEW_FIELDS, review_version
from services.web.sessions import token_hash
from support import SavedTransactionTestCase


class TransactionReviewTests(SavedTransactionTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(app.config, {'SECRET_KEY': 'a' * 64, 'SESSION_COOKIE_SECURE': False}))
        token = 'c' * 64
        now = datetime.now(timezone.utc)
        self.session.add(BrowserSession(token_hash=token_hash(token), created_at=now, last_seen_at=now, expires_at=now + timedelta(hours=8)))
        self.session.commit()
        with self.client.session_transaction() as cookie:
            cookie['_user_id'] = token
        self.csrf = re.search(r'name="csrf_token" value="([^"]+)"', self.client.get('/dashboard').get_data(as_text=True))[1]

    def pending(self):
        return self.client.get('/dashboard/review?account=' + self.account).get_json()

    def confirm(self, data, *, csrf=True):
        return self.client.post(data['confirm_url'], json={'version': data['version']}, headers={'X-CSRFToken': self.csrf} if csrf else {})

    def test_each_transaction_needs_explicit_confirmation_and_can_be_retried(self):
        before = self.pending()
        self.assertEqual(before['remaining'], 2)
        self.assertNotIn('raw_payload', str(before))
        self.assertEqual(self.pending()['remaining'], 2)  # Opening/closing never confirms.
        item = before['transaction']
        self.assertEqual(self.confirm(item).status_code, 200)
        self.assertEqual(self.confirm(item).status_code, 200)  # Idempotent duplicate click.
        self.assertEqual(self.pending()['remaining'], 1)
        self.assertEqual(self.confirm(self.pending()['transaction']).status_code, 200)
        self.assertEqual(self.pending(), {'remaining': 0, 'transaction': None})
        html = self.client.get('/dashboard?account=' + self.account).get_data(as_text=True)
        self.assertNotIn('is-unconfirmed', html)
        self.assertIn('is-confirmed', html)

    def test_missing_csrf_and_api_key_cannot_confirm(self):
        item = self.pending()['transaction']
        self.assertEqual(self.confirm(item, csrf=False).status_code, 400)
        self.assertEqual(self.client.post(item['confirm_url'], json={'version': item['version']}, headers=self.headers).status_code, 401)
        self.assertEqual(self.pending()['remaining'], 2)

    def test_changed_details_reject_stale_confirmation(self):
        item = self.pending()['transaction']
        payment = self.session.scalar(db.select(Transaction).where(Transaction.account_uid == self.account).order_by(Transaction.feed_item_uid).limit(1))
        # Find the displayed row using the version, not incidental UUID ordering.
        for candidate in self.session.scalars(db.select(Transaction).where(Transaction.account_uid == self.account)):
            if review_version(candidate) == item['version']:
                payment = candidate
                break
        payment.reference = 'Corrected by the bank'
        self.session.commit()
        self.assertEqual(self.confirm(item).status_code, 409)
        self.assertIsNone(payment.confirmed_at)
        self.assertEqual(self.confirm(self.pending()['transaction']).status_code, 200)

    def test_classification_edit_requires_confirmation_again(self):
        self.confirm(self.pending()['transaction'])
        payment = self.session.scalar(db.select(Transaction).where(Transaction.account_uid == self.account, Transaction.confirmed_at.is_not(None)))
        save_classification(payment, 'other', 'Reviewed classification')
        self.session.commit()
        self.assertIsNone(payment.confirmed_at)
        self.assertEqual(self.pending()['remaining'], 2)

    def test_account_filter_and_invalid_confirmation_are_safe(self):
        response = self.client.get('/dashboard/review?account=' + str(uuid4()))
        self.assertEqual(response.get_json(), {'remaining': 0, 'transaction': None})
        item = self.pending()['transaction']
        response = self.client.post(item['confirm_url'], json={'version': 'invalid'}, headers={'X-CSRFToken': self.csrf})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.pending()['remaining'], 2)

    def test_invalid_json_unknown_fields_wrong_versions_and_missing_records(self):
        item = self.pending()['transaction']
        bodies = (None, [], {}, {'version': 42}, {'version': 'a' * 64, 'extra': True}, {'version': 'x' * 65})
        for body in bodies:
            with self.subTest(body=body):
                response = self.client.post(item['confirm_url'], json=body, headers={'X-CSRFToken': self.csrf})
                self.assertEqual(response.status_code, 400)
        for version in ('a' * 64, 'é' * 64):
            self.assertEqual(self.confirm({**item, 'version': version}).status_code, 409)
        missing = f'/dashboard/transactions/{self.account}/{self.category}/{uuid4()}/confirm'
        self.assertEqual(self.client.post(missing, json={'version': item['version']}, headers={'X-CSRFToken': self.csrf}).status_code, 404)
        self.assertEqual(self.pending()['remaining'], 2)

    def test_query_validation_and_return_context(self):
        for query in ('page=0', 'page=bad', 'account=bad', 'account=all&account=all', 'extra=1'):
            with self.subTest(query=query):
                self.assertEqual(self.client.get('/dashboard/review?' + query).status_code, 400)
        item = self.client.get('/dashboard/review?account=' + self.account + '&page=2').get_json()['transaction']
        self.assertIn('account=' + self.account, item['edit_url'])
        self.assertIn('page=2', item['edit_url'])
        response = self.client.get('/dashboard/review?account=' + self.account)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_confirmation_commit_failure_rolls_back_and_does_not_leak_errors(self):
        item = self.pending()['transaction']
        original = self.session.commit
        commits = []
        def commit():
            commits.append(1)
            if len(commits) == 1:  # The browser session loader commits last_seen first.
                return original()
            raise OperationalError('private query', {}, Exception('private credential'))
        with patch.object(db.session, 'commit', side_effect=commit), self.assertLogs('toms.errors'):
            response = self.confirm(item)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', response.get_data(as_text=True))
        self.assertEqual(self.pending()['remaining'], 2)

    def test_income_display_uses_recorded_currency_and_edits_invalidate_versions(self):
        payment = self.session.get(Transaction, (self.account, self.category, self.incoming))
        save_classification(payment, 'income', None)
        payment.income = TransactionIncome(income_type='employment', tax_treatment='paye',
            recorded_currency='EUR', gross_minor=3000, tax_deducted_minor=500, needs_review=True)
        self.session.commit()
        items = self.pending()
        if items['transaction']['version'] != review_version(payment):
            self.confirm(items['transaction'])
            items = self.pending()
        details = dict(items['transaction']['details'])
        self.assertEqual(details['Gross income'], 'EUR 30.00')
        self.assertEqual(details['Tax deducted'], 'EUR 5.00')
        self.assertEqual(details['Income needs review'], 'Yes')
        before = items['transaction']
        payment.income.source_name = 'Changed source'
        self.session.commit()
        self.assertEqual(self.confirm(before).status_code, 409)

    def test_persisted_confirmation_survives_a_new_browser_client(self):
        item = self.pending()['transaction']
        self.confirm(item)
        other = app.test_client()
        with self.client.session_transaction() as original, other.session_transaction() as cookie:
            cookie.update(dict(original))
        response = other.get('/dashboard/review?account=' + self.account)
        self.assertEqual(response.get_json()['remaining'], 1)
        self.assertNotEqual(response.get_json()['transaction']['confirm_url'], item['confirm_url'])

    def test_income_api_save_and_delete_clear_confirmation(self):
        payment = self.session.get(Transaction, (self.account, self.category, self.incoming))
        save_classification(payment, 'income', None)
        self.session.commit()
        path = f'/dashboard/transactions/{self.account}/{self.category}/{self.incoming}/confirm'
        self.assertEqual(self.confirm({'confirm_url': path, 'version': review_version(payment)}).status_code, 200)
        api_path = f'/transactions/{self.account}/{self.category}/{self.incoming}/income'
        response = self.client.put(api_path, headers=self.headers, json={
            'income_type': 'employment', 'tax_treatment': 'no_tax_deducted', 'gross_minor': 2500})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(payment.confirmed_at)
        self.assertEqual(self.confirm({'confirm_url': path, 'version': review_version(payment)}).status_code, 200)
        self.assertEqual(self.client.delete(api_path, headers=self.headers).status_code, 200)
        self.assertIsNone(payment.confirmed_at)

    def test_anonymous_client_cannot_read_or_confirm(self):
        item = self.pending()['transaction']
        anonymous = app.test_client()
        self.assertEqual(anonymous.get('/dashboard/review').status_code, 401)
        self.assertEqual(anonymous.post(item['confirm_url'], json={'version': item['version']}).status_code, 401)
        self.assertEqual(self.pending()['remaining'], 2)

    def test_identical_payments_have_distinct_confirmation_versions(self):
        original = self.session.get(Transaction, (self.account, self.category, self.outgoing))
        duplicate = Transaction(account_uid=self.account, category_uid=self.category, feed_item_uid=str(uuid4()),
            source_updated_at=original.source_updated_at, raw_payload={},
            **{field: getattr(original, field) for field in REVIEW_FIELDS})
        self.session.add(duplicate)
        self.session.commit()
        self.assertNotEqual(review_version(original), review_version(duplicate))
        path = f'/dashboard/transactions/{self.account}/{self.category}/{duplicate.feed_item_uid}/confirm'
        self.assertEqual(self.confirm({'confirm_url': path, 'version': review_version(original)}).status_code, 409)
        self.assertIsNone(duplicate.confirmed_at)

    def test_popup_includes_pending_transactions_when_current_page_is_empty(self):
        response = self.client.get('/dashboard?account=' + self.account + '&page=2')
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="transaction-review"', response.get_data(as_text=True))
        response = self.client.get('/dashboard/review?account=' + self.account + '&page=2')
        self.assertEqual(response.get_json()['remaining'], 2)

    def test_expired_session_gets_json_error_and_never_confirms(self):
        item = self.pending()['transaction']
        session = self.session.get(BrowserSession, token_hash('c' * 64))
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.session.commit()
        response = self.confirm(item)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.mimetype, 'application/json')
        count = self.session.scalar(db.select(db.func.count()).select_from(Transaction).where(
            Transaction.account_uid == self.account, Transaction.confirmed_at.is_not(None)))
        self.assertEqual(count, 0)


    def test_actual_employee_ni_is_shown_before_confirming(self):
        payment = self.session.get(Transaction, (self.account, self.category, self.incoming))
        payment.source = 'FASTER_PAYMENTS_IN'
        payment.transaction_time = datetime.now(timezone.utc)
        payment.income = TransactionIncome(income_type='employment', tax_treatment='paye',
            gross_minor=3500, tax_deducted_minor=700, ni_deducted_minor=300, recorded_currency='GBP')
        self.session.commit()
        item = self.pending()['transaction']
        self.assertIn(['Employee NI deducted', 'GBP 3.00'], item['details'])
        payment.income.ni_deducted_minor = 301
        self.session.commit()
        self.assertEqual(self.confirm(item).status_code, 409)
