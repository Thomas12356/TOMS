"""Shared test setup. Test classes inherit one of the three fixtures below.

ApiTestCase: authenticated browser client, with no database connection.
PostgreSQLTestCase: adds a database session whose changes are rolled back.
SavedTransactionTestCase: adds an account and two example payments.

In a test's setUp(), super().setUp() runs that fixture before its own setup.
enterContext() and addCleanup() arrange cleanup even when a test fails.
"""

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy.orm import Session

from flask import g

from app import app
from models import Account, Category, Transaction
from services.database.connection import db


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(app.config, {"APP_API_KEY": "test-key"}))
        self.client = app.test_client()
        self.headers = {"Authorization": "Bearer test-key"}


@unittest.skipUnless(os.getenv("RUN_POSTGRES_TESTS") == "1", "Set RUN_POSTGRES_TESTS=1 for rollback-only database tests.")
class PostgreSQLTestCase(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(app.app_context())
        # Real HTTP requests have fresh g state; our held app context needs
        # these extension caches cleared between requests.
        def clear_request_caches():
            for key in ("_login_user", "csrf_token", "csrf_valid"):
                g.pop(key, None)
        app.before_request_funcs.setdefault(None, []).insert(0, clear_request_caches)
        self.addCleanup(app.before_request_funcs[None].remove, clear_request_caches)
        self.engine = db.engine
        self.connection = self.engine.connect()
        self.addCleanup(self.connection.close)
        outer = self.connection.begin()
        self.addCleanup(outer.rollback)
        # Application commits release savepoints; the outer transaction always rolls back.
        self.session = Session(bind=self.connection, join_transaction_mode="create_savepoint")
        self.addCleanup(self.session.close)
        # db.paginate calls the scoped session; other routes use its methods directly.
        session_proxy = Mock(return_value=self.session, wraps=self.session)
        # Nested app contexts must not close the rollback-only fixture session.
        # This fixture owns its lifetime through addCleanup(self.session.close).
        session_proxy.remove = Mock()
        self.enterContext(patch.object(db, "session", session_proxy))
        self.bank = self.enterContext(patch("services.banking.client.http_client.send",
            side_effect=AssertionError("Local database tests must not call Starling")))


class SavedTransactionTestCase(PostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.account, self.category, self.outgoing, self.incoming = (str(uuid4()) for _ in range(4))
        self.now = datetime(2000, 2, 15, 12, tzinfo=timezone.utc)
        self.session.add(Account(account_uid=self.account, default_category_uid=self.category,
                                 currency="GBP", raw_payload={}))
        self.session.flush()
        self.session.add(Category(account_uid=self.account, category_uid=self.category, kind="main"))
        self.session.flush()
        for item, direction, amount, source in ((self.outgoing, "OUT", 1250, "FASTER_PAYMENTS_OUT"),
                                               (self.incoming, "IN", 2500, "INTERNAL_TRANSFER")):
            self.session.add(Transaction(account_uid=self.account, category_uid=self.category,
                feed_item_uid=item, direction=direction, amount_minor=amount, currency="GBP",
                source=source, status="SETTLED", transaction_time=self.now, source_updated_at=self.now,
                spending_category="GROCERIES", raw_payload={"private": "Never return this"}))
        self.session.flush()
