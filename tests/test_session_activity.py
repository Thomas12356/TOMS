"""Background polling must not turn an idle tab into an active owner."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from time import time
from sqlalchemy.exc import OperationalError

from models import BrowserSession
from services.web.sessions import token_hash
from test_login import OwnerLoginFixture, csrf_token


class SessionActivityTests(OwnerLoginFixture):
    def browser(self):
        with self.client.session_transaction() as saved:
            return self.session.get(BrowserSession, token_hash(saved['_user_id']))

    def test_background_requests_do_not_renew_idle_session(self):
        self.sign_in()
        browser = self.browser()
        original = datetime.now(timezone.utc) - timedelta(minutes=29)
        browser.last_seen_at = original
        self.session.flush()
        with patch('routes.dashboard.latest_sync', return_value=None):
            self.assertEqual(self.client.get('/dashboard/sync-status').status_code, 200)
        self.session.expire_all()
        self.assertEqual(self.browser().last_seen_at, original)

    def test_activity_requires_owner_and_csrf_and_keeps_absolute_expiry(self):
        self.sign_in()
        csrf = csrf_token(self.client)
        browser = self.browser()
        original = datetime.now(timezone.utc) - timedelta(minutes=20)
        browser.last_seen_at = original
        absolute = browser.expires_at
        self.session.flush()
        self.assertEqual(self.client.post('/session/activity').status_code, 400)
        self.assertEqual(self.client.post('/session/activity', headers=self.headers,
                                         data={'csrf_token': csrf}).status_code, 401)
        self.assertEqual(self.client.post('/session/activity', data={'csrf_token': csrf}).status_code, 204)
        self.session.expire_all()
        browser = self.browser()
        self.assertGreater(browser.last_seen_at, original)
        self.assertEqual(browser.expires_at, absolute)

    def test_user_navigation_renews_but_script_navigation_does_not(self):
        self.sign_in()
        browser = self.browser()
        original = datetime.now(timezone.utc) - timedelta(minutes=20)
        browser.last_seen_at = original
        self.session.flush()
        with patch('routes.dashboard.latest_sync', return_value=None):
            self.client.get('/dashboard/sync-status', headers={'Sec-Fetch-Mode': 'navigate'})
            self.assertEqual(self.browser().last_seen_at, original)
            self.client.get('/dashboard/sync-status', headers={
                'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-User': '?1'})
        self.assertGreater(self.browser().last_seen_at, original)

    def test_activity_cannot_resurrect_expired_or_revoked_session(self):
        for field in ('last_seen_at', 'expires_at'):
            self.sign_in()
            csrf = csrf_token(self.client)
            browser = self.browser()
            setattr(browser, field, datetime.now(timezone.utc) - timedelta(minutes=31))
            self.session.flush()
            self.assertEqual(self.client.post('/session/activity', data={'csrf_token': csrf}).status_code, 401)
        self.sign_in()
        csrf = csrf_token(self.client)
        self.session.delete(self.browser())
        self.session.flush()
        self.assertEqual(self.client.post('/session/activity', data={'csrf_token': csrf}).status_code, 401)

    def test_activity_database_failure_is_private_and_retryable(self):
        self.sign_in()
        csrf = csrf_token(self.client)
        with patch('routes.login.renew_owner_activity', side_effect=OperationalError(
                'private SQL', {}, Exception('private password'))), self.assertLogs('toms.errors'):
            response = self.client.post('/session/activity', data={'csrf_token': csrf})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertNotIn('private', response.get_data(as_text=True))

    def test_activity_refreshes_csrf_for_long_lived_forms(self):
        self.sign_in()
        original = csrf_token(self.client)
        timestamp = int(time())
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=timestamp + 3500):
            response = self.client.post('/session/activity', data={'csrf_token': original})
        self.assertEqual(response.status_code, 204)
        fresh = response.headers['X-CSRFToken']
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=timestamp + 3700):
            self.assertEqual(self.client.post('/session/activity', data={'csrf_token': original}).status_code, 400)
            self.assertEqual(self.client.post('/session/activity', data={'csrf_token': fresh}).status_code, 204)
