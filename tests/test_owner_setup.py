"""First-run ownership cannot be claimed without a server-generated token."""

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from werkzeug.security import check_password_hash

from app import app
from models import BrowserSession, OwnerLogin, OwnerSetup
from services.database.connection import db
from services.web.sessions import consume_login_attempt, token_hash
from test_login import OwnerLoginFixture


class OwnerSetupTests(OwnerLoginFixture):
    def setUp(self):
        super().setUp()
        self.session.execute(db.delete(OwnerSetup))
        self.session.commit()

    def no_owner(self):
        self.session.execute(db.delete(OwnerLogin))
        self.session.commit()

    def generate_token(self):
        result = app.test_cli_runner().invoke(args=["owner-setup-token"])
        self.assertEqual(result.exit_code, 0, result.output)
        return result.output.strip().splitlines()[-1]

    def csrf(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))[1]

    def create(self, token, **changes):
        values = {"setup_token": token, "username": "new-owner", "password": "my new long owner password", "confirmation": "my new long owner password", "csrf_token": self.csrf('/setup')}
        values.update(changes)
        return self.client.post('/setup', data=values)

    def test_root_leads_to_setup_login_or_authenticated_dashboard(self):
        self.assertEqual(self.client.get('/').headers['Location'], '/dashboard')
        response = self.client.get('/', follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request.path, '/login')
        self.no_owner()
        response = self.client.get('/', follow_redirects=True)
        self.assertEqual(response.request.path, '/setup')
        self.assertEqual(response.status_code, 503)
        self.generate_token()
        response = self.client.get('/', follow_redirects=True)
        self.assertEqual(response.request.path, '/setup')
        self.assertEqual(response.status_code, 200)

    def test_first_visit_requires_token_and_setup_does_not_leak_it(self):
        self.no_owner()
        response = self.client.get('/login')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/setup')
        self.assertEqual(self.client.get('/setup').status_code, 503)
        token = self.generate_token()
        record = self.session.get(OwnerSetup, 1)
        self.assertEqual(record.token_hash, token_hash(token))
        self.assertNotEqual(record.token_hash, token)
        html = self.client.get('/setup').get_data(as_text=True)
        self.assertNotIn(token, html)
        self.assertNotIn(record.token_hash, html)

    def test_valid_setup_consumes_token_and_cannot_replace_owner(self):
        self.no_owner()
        token = self.generate_token()
        response = self.create(token)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['Location'], '/login')
        owner = self.session.get(OwnerLogin, 1)
        self.assertEqual(owner.username, 'new-owner')
        self.assertTrue(check_password_hash(owner.password_hash, 'my new long owner password'))
        self.assertIsNone(self.session.get(OwnerSetup, 1))
        self.assertEqual(self.client.get('/setup').status_code, 302)
        csrf = self.csrf('/login')
        response = self.client.post('/setup', data={"setup_token": token, "username": "attacker", "password": self.password, "confirmation": self.password, "csrf_token": csrf})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.session.get(OwnerLogin, 1).username, 'new-owner')
        result = app.test_cli_runner().invoke(args=['owner-setup-token'])
        self.assertNotEqual(result.exit_code, 0)

    def test_wrong_token_password_validation_and_csrf_leave_setup_usable(self):
        self.no_owner()
        token = self.generate_token()
        for changes in ({'setup_token': 'wrong'}, {'password': 'short', 'confirmation': 'short'},
                        {'confirmation': 'does not match'}, {'username': '\x00bad'}):
            with self.subTest(changes=changes):
                response = self.create(token, **changes)
                self.assertEqual(response.status_code, 400)
                self.assertIsNone(self.session.get(OwnerLogin, 1))
                self.assertIsNotNone(self.session.get(OwnerSetup, 1))
                self.assertNotIn(token, response.get_data(as_text=True))
        self.assertEqual(self.client.post('/setup', data={'setup_token': token}).status_code, 400)
        self.assertEqual(self.create(token).status_code, 303)

    def test_expired_and_rotated_tokens_are_rejected(self):
        self.no_owner()
        old = self.generate_token()
        new = self.generate_token()
        self.assertNotEqual(old, new)
        self.assertEqual(self.create(old).status_code, 400)
        csrf = self.csrf('/setup')
        record = self.session.get(OwnerSetup, 1)
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.session.commit()
        # Use the existing signed CSRF token; expiry closes both GET and POST.
        with self.client.session_transaction() as cookie:
            self.assertIn('csrf_token', cookie)
        self.assertEqual(self.client.get('/setup').status_code, 503)
        self.assertEqual(self.client.post('/setup', data={'setup_token': new, 'csrf_token': csrf}).status_code, 503)
        self.assertIsNone(self.session.get(OwnerLogin, 1))
        # Refresh the setup token through the server command to reopen the page.
        fresh = self.generate_token()
        self.assertNotEqual(new, fresh)
        self.assertEqual(self.create(fresh).status_code, 303)

    def test_password_change_requires_session_current_password_and_csrf(self):
        self.assertEqual(self.client.get('/settings/password').status_code, 302)
        self.assertEqual(self.client.get('/settings/password', headers=self.headers).status_code, 302)
        self.sign_in()
        before = self.session.get(OwnerLogin, 1).password_hash
        csrf = self.csrf('/settings/password')
        body = {'current_password': 'wrong', 'password': 'a different long password', 'confirmation': 'a different long password', 'csrf_token': csrf}
        self.assertEqual(self.client.post('/settings/password', data=body).status_code, 400)
        body['current_password'] = self.password
        body['confirmation'] = 'mismatch'
        self.assertEqual(self.client.post('/settings/password', data=body).status_code, 400)
        body.pop('csrf_token')
        self.assertEqual(self.client.post('/settings/password', data=body).status_code, 400)
        self.assertEqual(self.session.get(OwnerLogin, 1).password_hash, before)

    def test_password_change_revokes_all_devices_and_requires_new_password(self):
        self.sign_in()
        stolen = self.client.get_cookie('session').value
        body = {'current_password': self.password, 'password': 'a different long password', 'confirmation': 'a different long password', 'csrf_token': self.csrf('/settings/password')}
        response = self.client.post('/settings/password', data=body)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.session.scalar(db.select(db.func.count()).select_from(BrowserSession)), 0)
        self.client.set_cookie('session', stolen)
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        self.assertEqual(self.sign_in(password=self.password).status_code, 401)
        self.assertEqual(self.sign_in(password='a different long password').status_code, 302)

    def test_setup_and_password_change_share_throttle_and_privacy_headers(self):
        self.no_owner()
        token = self.generate_token()
        csrf = self.csrf('/setup')
        for _ in range(20):
            consume_login_attempt()
        response = self.client.post('/setup', data={'setup_token': token, 'csrf_token': csrf})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '900')
        for path in ('/setup', '/settings/password'):
            response = self.client.get(path)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['X-Frame-Options'], 'DENY')

    def test_cli_recovery_revokes_pending_setup_tokens(self):
        self.no_owner()
        self.generate_token()
        result = app.test_cli_runner().invoke(args=['owner-password', '--username', 'recovered-owner', '--password', self.password])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIsNone(self.session.get(OwnerSetup, 1))
        self.assertEqual(self.client.get('/setup').status_code, 302)

    def test_startup_announces_token_only_before_setup_and_rotates_on_restart(self):
        from services.web.setup import announce_setup
        self.no_owner()
        with patch("services.web.setup.click.echo") as output:
            announce_setup(app)
        token = output.call_args_list[-1].args[0]
        self.assertEqual(self.session.get(OwnerSetup, 1).token_hash, token_hash(token))
        self.assertNotIn(token, self.client.get('/setup').get_data(as_text=True))
        with patch("services.web.setup.click.echo") as output:
            announce_setup(app)
        replacement = output.call_args_list[-1].args[0]
        self.assertNotEqual(token, replacement)
        self.assertEqual(self.create(token).status_code, 400)
        self.assertEqual(self.create(replacement).status_code, 303)
        with patch("services.web.setup.click.echo") as output:
            announce_setup(app)
        output.assert_not_called()
        self.assertIsNone(self.session.get(OwnerSetup, 1))

    def test_startup_failures_do_not_print_or_create_tokens(self):
        from sqlalchemy.exc import OperationalError
        from services.web.setup import announce_setup
        self.no_owner()
        with patch.dict(app.config, {"SECRET_KEY": None}), patch("services.web.setup.create_setup_token") as create, patch("services.web.setup.click.echo") as output:
            announce_setup(app)
            create.assert_not_called()
            self.assertIn('SECRET_KEY', output.call_args.args[0])
        with patch("services.web.setup.create_setup_token", side_effect=OperationalError('private query', {}, Exception('private password'))), patch("services.web.setup.click.echo") as output, self.assertLogs('toms.errors'):
            announce_setup(app)
            self.assertNotIn('private password', output.call_args.args[0])
        self.assertIsNone(self.session.get(OwnerSetup, 1))
