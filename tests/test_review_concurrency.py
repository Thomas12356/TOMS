from services.database.migration_connection import migration_engine
"""Real concurrent requests in a disposable schema; no live records are modified."""
import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import scoped_session, sessionmaker

from app import app
from models import Account, BrowserSession, Category, Transaction
from services.database.connection import db
from services.transactions.review import review_version
from services.transactions.store import SyncStore
from services.web.sessions import token_hash
from support import ApiTestCase


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS') == '1', 'Requires PostgreSQL.')
class ReviewConcurrencyTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(app.app_context())
        self.enterContext(patch.dict(app.config, {'SECRET_KEY': 'a' * 64, 'SESSION_COOKIE_SECURE': False}))
        self.engine = db.engine
        self.admin_engine = migration_engine()
        self.addCleanup(self.admin_engine.dispose)
        self.schema = 'review_' + uuid4().hex
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {self.schema}'))
        self.addCleanup(self.drop_schema)
        self.bound = self.engine.execution_options(schema_translate_map={'toms': self.schema})
        db.metadata.create_all(self.admin_engine.execution_options(schema_translate_map={"toms": self.schema}))
        with self.admin_engine.begin() as connection:
            connection.execute(text(f"GRANT USAGE ON SCHEMA {self.schema} TO toms_app"))
            connection.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {self.schema} TO toms_app"))
        self.sessions = scoped_session(sessionmaker(bind=self.bound))
        self.addCleanup(self.sessions.remove)
        self.enterContext(patch.object(db, 'session', self.sessions))
        self.enterContext(patch('services.banking.client.http_client.send', side_effect=AssertionError('No live bank requests')))
        self.account, self.category, self.item = (str(uuid4()) for _ in range(3))
        self.now = datetime.now(timezone.utc)
        self.sessions.add(Account(account_uid=self.account, default_category_uid=self.category, currency='GBP', raw_payload={}))
        self.sessions.commit()
        self.sessions.add(Category(account_uid=self.account, category_uid=self.category, kind='main'))
        self.sessions.commit()
        self.sessions.add(Transaction(account_uid=self.account, category_uid=self.category, feed_item_uid=self.item,
            amount_minor=1000, currency='GBP', direction='IN', status='SETTLED', transaction_time=self.now,
            source_updated_at=self.now, source='FASTER_PAYMENTS_IN', raw_payload={}))
        token = 'd' * 64
        self.sessions.add(BrowserSession(token_hash=token_hash(token), created_at=self.now, last_seen_at=self.now, expires_at=self.now + timedelta(hours=8)))
        self.sessions.commit()
        self.clients = []
        for _ in range(2):
            client = app.test_client()
            with client.session_transaction() as cookie:
                cookie['_user_id'] = token
            with app.app_context():
                page = client.get('/dashboard')
                csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))[1]
                record = client.get('/dashboard/review').get_json()['transaction']
            self.clients.append((client, csrf, record))

    def drop_schema(self):
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {self.schema} CASCADE'))

    def submit(self, index):
        client, csrf, record = self.clients[index]
        try:
            return client.post(record['confirm_url'], json={'version': record['version']}, headers={'X-CSRFToken': csrf}).status_code
        finally:
            self.sessions.remove()

    def test_simultaneous_confirmations_are_idempotent(self):
        barrier = Barrier(2)
        def confirm(index):
            barrier.wait(timeout=10)
            return self.submit(index)
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(confirm, (0, 1))), [200, 200])
        with self.bound.connect() as connection:
            stamp = connection.scalar(db.select(Transaction.confirmed_at))
            self.assertIsNotNone(stamp)
            self.assertEqual(connection.scalar(db.select(db.func.count()).select_from(Transaction)), 1)

    def test_bank_update_waits_for_confirmation_then_reopens_review(self):
        locked, release, updating = Event(), Event(), Event()
        def paused_version(transaction):
            locked.set()
            if not release.wait(timeout=10):
                raise RuntimeError('Timed out waiting for the test')
            return review_version(transaction)
        def update():
            try:
                with app.app_context():
                    store = SyncStore(self.sessions, self.bound)
                    run = str(uuid4())
                    store.start_run(run, {}, self.now)
                    store.start_target(run, self.account, self.category, 'incremental', self.now, self.now)
                    updating.set()
                    store.save_page(run, self.account, self.category, [{
                        'account_uid': self.account, 'category_uid': self.category, 'feed_item_uid': self.item,
                        'amount_minor': 1200, 'currency': 'GBP', 'direction': 'IN', 'status': 'SETTLED',
                        'transaction_time': self.now, 'source_updated_at': self.now + timedelta(minutes=1),
                        'source': 'FASTER_PAYMENTS_IN', 'counterparty_name': None, 'reference': None, 'raw_payload': {'corrected': True},
                    }])
            finally:
                self.sessions.remove()
        with patch('routes.review.review_version', side_effect=paused_version), ThreadPoolExecutor(max_workers=2) as pool:
            confirmation = pool.submit(self.submit, 0)
            self.assertTrue(locked.wait(timeout=10))
            correction = pool.submit(update)
            try:
                self.assertTrue(updating.wait(timeout=10))
                with self.assertRaises(TimeoutError):
                    correction.result(timeout=.2)
            finally:
                release.set()
            self.assertEqual(confirmation.result(timeout=10), 200)
            correction.result(timeout=10)
        with self.bound.connect() as connection:
            self.assertIsNone(connection.scalar(db.select(Transaction.confirmed_at)))
            self.assertEqual(connection.scalar(db.select(Transaction.amount_minor)), 1200)

    def test_simultaneous_income_form_saves_reject_the_stale_edit(self):
        from models import IncomeStream, TransactionIncome
        from services.transactions.classification import save_classification
        stream_id = str(uuid4())
        self.sessions.add(IncomeStream(id=stream_id, name='Example employer', kind='employed'))
        payment = self.sessions.get(Transaction, (self.account, self.category, self.item))
        save_classification(payment, 'income', None)
        self.sessions.commit()
        path = f'/dashboard/transactions/{self.account}/{self.category}/{self.item}/income'
        forms = []
        for client, csrf, record in self.clients:
            with app.app_context():
                html = client.get(path).get_data(as_text=True)
                version = re.search(r'name="version" value="([^"]+)"', html)[1]
                forms.append((client, csrf, version))
        barrier = Barrier(2)
        def save(index):
            client, csrf, version = forms[index]
            try:
                barrier.wait(timeout=10)
                return client.post(path, data={'csrf_token': csrf, 'version': version, 'income_stream_id': stream_id,
                    'tax_treatment': 'unknown', 'gross': '10.00'}).status_code
            finally:
                self.sessions.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(save, (0, 1))), [303, 409])
        with self.bound.connect() as connection:
            self.assertEqual(connection.scalar(db.select(db.func.count()).select_from(TransactionIncome)), 1)
            self.assertEqual(connection.scalar(db.select(TransactionIncome.gross_minor)), 1000)

    def test_activity_uses_connection_schema_and_duplicate_confirmations_log_once(self):
        from models import UserAction
        from services.web.activity import record_action
        with app.test_request_context('/dashboard', method='POST'):
            record_action('confirm', self.item, verified_owner=True)
            self.sessions.commit()
        with self.bound.connect() as connection:
            self.assertEqual(connection.scalar(db.select(db.func.count()).select_from(UserAction).where(UserAction.record_id == self.item)), 1)
        # Reconfirming an already confirmed transaction must not add another event.
        self.assertEqual(self.submit(0), 200)
        self.assertEqual(self.submit(1), 200)
        with self.bound.connect() as connection:
            self.assertEqual(connection.scalar(db.select(db.func.count()).select_from(UserAction).where(UserAction.record_id == self.item)), 2)
