"""Exercise real CSRF tokens, signed cookies and revocable database sessions."""

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy.exc import OperationalError
from werkzeug.security import generate_password_hash

from flask import g

from app import app
from models import BrowserSession, OwnerLogin
from services.database.connection import db
from services.web.sessions import consume_login_attempt, token_hash
from support import ApiTestCase, PostgreSQLTestCase


def csrf_token(client):
    page = client.get("/login")
    return re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))[1]


class LoginSafetyTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(app.config, {"SECRET_KEY": "a" * 64, "SESSION_COOKIE_SECURE": False}))

    def test_missing_secret_fails_closed(self):
        for key in (None, "short-key"):
            with patch.dict(app.config, {"SECRET_KEY": key}):
                self.assertEqual(self.client.get("/login").status_code, 503)
                self.assertEqual(self.client.get("/dashboard").status_code, 503)

    def test_forms_reject_missing_csrf_before_database_access(self):
        with patch.object(db.session, "get") as get, patch("routes.login.consume_login_attempt") as attempts:
            for path in ("/login", "/logout"):
                response = self.client.post(path, data={"username": "owner", "password": "secret"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            get.assert_not_called()
            attempts.assert_not_called()
            self.assertEqual(self.client.get("/logout").status_code, 405)

    def test_login_database_error_has_no_private_details(self):
        with patch.object(db.session, "get", side_effect=OperationalError("private", {}, Exception("password"))), self.assertLogs("toms.errors"):
            response = self.client.get("/login")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("password", response.get_data(as_text=True))


class OwnerLoginFixture(PostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(app.config, {"SECRET_KEY": "a" * 64, "SESSION_COOKIE_SECURE": False}))
        # The database fixture holds an app context across requests. Real HTTP
        # requests have fresh g state; clear extension caches to reproduce that.
        def clear_request_caches():
            for key in ("_login_user", "csrf_token", "csrf_valid"):
                g.pop(key, None)
        app.before_request_funcs.setdefault(None, []).insert(0, clear_request_caches)
        self.addCleanup(app.before_request_funcs[None].remove, clear_request_caches)
        # Tests own a rollback-only transaction; do not alter the real owner.
        self.session.execute(db.delete(OwnerLogin))
        self.session.execute(db.delete(BrowserSession))
        self.session.execute(db.text("DELETE FROM toms.login_attempts"))
        self.password = "a sufficiently long test password"
        self.session.add(OwnerLogin(id=1, username="owner", password_hash=generate_password_hash(self.password)))
        self.session.flush()

    def sign_in(self, password=None, username="owner"):
        return self.client.post("/login?next=https://evil.example", data={
            "username": username, "password": password or self.password,
            "csrf_token": csrf_token(self.client),
        })


class OwnerLoginTests(OwnerLoginFixture):
    def test_login_cookie_dashboard_logout_and_stolen_cookie_revocation(self):
        with patch.dict(app.config, {"SESSION_COOKIE_SECURE": True}):
            response = self.sign_in()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        cookie = response.headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Lax"):
            self.assertIn(flag, cookie)
        self.assertEqual(self.client.get("/transactions").status_code, 401)
        stolen_cookie = self.client.get_cookie("session").value
        with patch.object(db, "paginate", return_value=type("Page", (), {"items": [], "total": 0, "pages": 0})()):
            self.assertEqual(self.client.get("/dashboard").status_code, 200)
        with self.client.session_transaction() as saved:
            token = saved["_user_id"]
            self.assertNotIn(self.password, str(dict(saved)))
        self.assertIsNotNone(self.session.get(BrowserSession, token_hash(token)))
        csrf = csrf_token(self.client)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": csrf}).status_code, 302)
        self.assertIsNone(self.session.get(BrowserSession, token_hash(token)))
        self.client.set_cookie("session", stolen_cookie)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
        self.assertEqual(self.client.get("/dashboard/balances").status_code, 401)
        # A browser session never authenticates the API endpoints.
        self.assertEqual(self.client.get("/transactions").status_code, 401)

    def test_wrong_credentials_and_shared_login_throttle(self):
        for username in ("owner", "unknown"):
            response = self.sign_in(password="wrong", username=username)
            self.assertEqual(response.status_code, 401)
            self.assertIn("Incorrect username or password", response.get_data(as_text=True))
        for _ in range(18):
            self.assertTrue(consume_login_attempt())
        response = self.sign_in()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "900")
        self.session.execute(db.text("UPDATE toms.login_attempts SET window_started_at = now() - interval '16 minutes'"))
        self.assertEqual(self.sign_in().status_code, 302)

    def test_idle_and_absolute_expiry(self):
        for expired_field, age in (("last_seen_at", timedelta(minutes=31)), ("expires_at", timedelta(seconds=1))):
            self.assertEqual(self.sign_in().status_code, 302)
            with self.client.session_transaction() as saved:
                token = saved["_user_id"]
            browser = self.session.get(BrowserSession, token_hash(token))
            setattr(browser, expired_field, datetime.now(timezone.utc) - age)
            self.session.flush()
            self.assertEqual(self.client.get("/dashboard").status_code, 302)
            self.assertIsNone(self.session.get(BrowserSession, token_hash(token)))

    def test_owner_password_command_revokes_sessions_and_stores_only_hash(self):
        self.assertEqual(self.sign_in().status_code, 302)
        result = app.test_cli_runner().invoke(args=["owner-password"], input="new-owner\na new sufficiently long password\na new sufficiently long password\n")
        self.assertEqual(result.exit_code, 0, result.output)
        owner = self.session.get(OwnerLogin, 1)
        self.assertTrue(owner.password_hash.startswith("scrypt:"))
        self.assertNotIn("a new sufficiently long password", owner.password_hash)
        self.assertEqual(owner.username, "new-owner")
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
