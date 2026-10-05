"""Freshness, authentication and CSRF for dashboard-triggered imports."""
from unittest.mock import Mock, patch

from services.transactions.sync import run_sync
from test_login import OwnerLoginFixture
from test_transaction_sync import NOW
import unittest

from datetime import datetime, timedelta
from uuid import uuid4
from models import SyncRun, SyncTarget
from services.database.connection import db
from services.transactions.automatic_sync import latest_sync
from services.transactions.store import SyncStore
from support import PostgreSQLTestCase


class SyncFreshnessTests(unittest.TestCase):
    def test_recent_success_skips_bank_requests_and_releases_lock(self):
        store = Mock()
        store.recently_synced.return_value = True
        with patch('services.transactions.sync.discover_accounts') as bank:
            self.assertEqual(run_sync(store, {}, now=NOW, minimum_age_seconds=60), {'status': 'fresh'})
        store.recently_synced.assert_called_once_with(NOW, 60)
        store.start_run.assert_not_called()
        store.unlock.assert_called_once()
        bank.assert_not_called()

    def test_stale_success_runs_import_under_lock(self):
        store = Mock()
        store.recently_synced.return_value = False
        with patch('services.transactions.sync.discover_accounts', return_value=[]):
            run_sync(store, {}, now=NOW, minimum_age_seconds=60)
        store.start_run.assert_called_once()
        store.finish_run.assert_called_once()
        store.unlock.assert_called_once()


class DashboardSyncTests(OwnerLoginFixture):
    def test_sync_requires_browser_login_and_csrf(self):
        with patch('routes.dashboard.start_dashboard_sync') as start:
            self.assertEqual(self.client.post('/dashboard/sync').status_code, 302)
            self.assertEqual(self.client.post('/dashboard/sync', headers=self.headers).status_code, 401)
            self.sign_in()
            self.assertEqual(self.client.post('/dashboard/sync').status_code, 400)
            start.assert_not_called()

    def test_page_load_and_button_pass_correct_freshness_policy(self):
        from test_login import csrf_token
        self.sign_in()
        with patch('routes.dashboard.start_dashboard_sync', return_value=True) as start:
            for force in ('0', '1'):
                token = csrf_token(self.client)
                response = self.client.post('/dashboard/sync?force=' + force, headers={'X-CSRFToken': token})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(start.call_args.kwargs, {'force': force == '1'})
            response = self.client.post('/dashboard/sync?force=bad', headers={'X-CSRFToken': token})
            self.assertEqual(response.status_code, 400)


class SyncStatusTests(PostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.session.execute(db.delete(SyncTarget))
        self.session.execute(db.delete(SyncRun))

    def add_run(self, status, age, options=None):
        when = NOW - timedelta(seconds=age)
        run = SyncRun(run_uid=str(uuid4()), status=status, requested_options=options or {},
                      snapshot_at=when, started_at=when, finished_at=when)
        self.session.add(run)
        self.session.flush()
        return run

    def test_freshness_boundary_ignores_failed_and_filtered_runs(self):
        full = self.add_run('completed', 60)
        self.add_run('completed', 1, {'accountUid': '11111111-1111-4111-8111-111111111111'})
        self.add_run('failed', 0)
        store = SyncStore(session=self.session, engine=self.engine)
        self.assertFalse(store.recently_synced(NOW, 60))
        full.finished_at = NOW - timedelta(seconds=59)
        self.session.flush()
        self.assertTrue(store.recently_synced(NOW, 60))

    def test_status_keeps_last_success_after_failed_run(self):
        full = self.add_run('completed', 120)
        self.add_run('failed', 1)
        status = latest_sync()
        self.assertEqual(status['status'], 'failed')
        self.assertEqual(datetime.fromisoformat(status['last_success_at']), full.finished_at)
