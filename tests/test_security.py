from services.database.migration_connection import migration_engine
"""Adversarial security checks. All bank calls are blocked or mocked.

Database scenarios inherit rollback-only fixtures. Concurrency scenarios use a
fresh temporary PostgreSQL schema, never the real owner's committed tables.
"""

import base64
import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import scoped_session, sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash

from app import app
from models import BrowserSession, OwnerLogin, OwnerSetup
from services.database.connection import db
from services.web.sessions import consume_login_attempt, token_hash
from test_login import OwnerLoginFixture, csrf_token
from support import ApiTestCase


class SecurityBoundaryTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(app.config, {"SECRET_KEY": "a" * 64, "SESSION_COOKIE_SECURE": False}))
        self.enterContext(patch.object(db, "session", Mock()))
        self.enterContext(patch("services.banking.client.acquire_slot"))
        self.bank = self.enterContext(patch("services.banking.client.http_client.send", side_effect=AssertionError("No live bank access")))

    def test_every_api_route_rejects_missing_malformed_and_wrong_credentials_first(self):
        invalid_headers = ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer"},
                           {"Authorization": "Basic !!!!"}, {"Authorization": "Digest anything"},
                           {"Authorization": "Basic " + base64.b64encode(b"other:test-key").decode()})
        calls = []
        with patch.object(db, "paginate") as paginate, patch.object(db.session, "execute") as execute, \
                patch.object(db.session, "get") as get, patch.object(db.session, "scalars") as scalars, \
                patch.object(db.session, "scalar") as scalar:
            for rule in app.url_map.iter_rules():
                if rule.endpoint.split(".")[0] not in {"starling", "sync", "transactions", "reports"} and rule.rule != "/health/db":
                    continue
                path = re.sub(r"<[^>]+>", "11111111-1111-4111-8111-111111111111", rule.rule)
                for method in sorted(rule.methods - {"OPTIONS", "HEAD"}):
                    for headers in invalid_headers:
                        with self.subTest(path=path, method=method, auth=headers):
                            response = self.client.open(path, method=method, headers=headers)
                            self.assertEqual(response.status_code, 401)
                            self.assertEqual(response.headers["Cache-Control"], "no-store")
                            calls.append((method, path))
            for mocked in (paginate, execute, get, scalars, scalar, self.bank):
                mocked.assert_not_called()
        self.assertGreater(len(calls), 100)

    def test_browser_security_policy_also_covers_errors_and_static_files(self):
        for path in ('/login', '/does-not-exist', '/static/css/dashboard.css'):
            response = self.client.get(path)
            policy = response.headers['Content-Security-Policy']
            for directive in ("script-src 'self'", "base-uri 'none'", "frame-ancestors 'none'", "form-action 'self'", "object-src 'none'"):
                self.assertIn(directive, policy)
            self.assertNotIn('unsafe-inline', policy)
            self.assertNotIn('unsafe-eval', policy)
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(response.headers['Permissions-Policy'], 'camera=(), microphone=(), geolocation=()')
            response.close()

    def test_browser_cached_basic_credentials_cannot_authorize_state_changes(self):
        basic = "Basic " + base64.b64encode(b"api:test-key").decode()
        with patch.object(db.session, "execute") as execute:
            for method, path in (("POST", "/sync/transactions"), ("POST", "/starling/test"),
                                 ("PUT", "/transactions/11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111/classification")):
                with self.subTest(method=method, path=path):
                    response = self.client.open(path, method=method, headers={"Authorization": basic, "Origin": "https://attacker.example"})
                    self.assertEqual(response.status_code, 401)
                    self.assertIn("Bearer", response.headers["WWW-Authenticate"])
            execute.assert_not_called()
        self.bank.assert_not_called()

    def test_forged_cookie_and_spoofed_proxy_identity_never_authenticate(self):
        serializer = app.session_interface.get_signing_serializer(app)
        cookie = serializer.dumps({"_user_id": "f" * 64, "_fresh": True})
        # An attacker knows the format, but not our signing secret.
        payload, signature = cookie.rsplit(".", 1)
        # Change significant signature bits; the final base64 character may
        # contain unused padding bits and decode to the original signature.
        tampered = payload + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
        for value in ("garbage", tampered):
            with self.subTest(cookie=value), patch.object(db.session, "get") as get:
                self.client.set_cookie("session", value)
                response = self.client.get("/dashboard", headers={
                    "Tailscale-User-Login": "owner@example.test", "X-Forwarded-User": "owner",
                    "X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "https",
                })
                self.assertEqual(response.status_code, 302)
                get.assert_not_called()
        self.bank.assert_not_called()

    def test_signed_unknown_or_invalid_session_identifiers_fail_closed(self):
        for token in ("f" * 64, "short", 123, [], {}, None):
            with self.subTest(token=token), patch.object(db.session, "get", return_value=None) as get:
                with self.client.session_transaction() as saved:
                    saved["_user_id"] = token
                self.assertEqual(self.client.get("/dashboard").status_code, 302)
                if not isinstance(token, str) or len(token) != 64:
                    get.assert_not_called()

    def test_query_parameters_and_cookies_cannot_supply_api_keys(self):
        self.client.set_cookie("APP_API_KEY", "test-key")
        with patch.object(db, "paginate") as paginate:
            for query in ("APP_API_KEY=test-key", "api_key=test-key", "access_token=test-key"):
                self.assertEqual(self.client.get("/transactions?" + query).status_code, 401)
            paginate.assert_not_called()

    def test_bank_supplied_text_is_escaped_in_dashboard(self):
        attack = '<img src=x onerror="alert(1)">'
        account = SimpleNamespace(account_uid="11111111-1111-4111-8111-111111111111", name=attack, currency="GBP")
        payment = SimpleNamespace(account_uid=account.account_uid, category_uid=account.account_uid, feed_item_uid=account.account_uid, transaction_time=datetime.now(timezone.utc), counterparty_name=attack,
                                  reference=attack, amount_minor=100, currency="GBP", direction="IN",
                                  source="FASTER_PAYMENTS_IN", status=attack, classification=None)
        db.session.scalars.return_value = [account]
        with patch.object(db, "paginate", return_value=SimpleNamespace(items=[payment], total=1, pages=0)):
            response = self.client.get("/dashboard", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)
        self.assertNotIn("test-key", html)

    def test_login_and_logout_reject_json_and_large_bodies_without_work(self):
        with patch("routes.login.consume_login_attempt") as attempts, patch.object(db.session, "get") as get:
            self.assertEqual(self.client.post("/login", json={"username": "owner", "password": "password"}).status_code, 400)
            response = self.client.post("/login", data=b"x" * (1024 * 1024 + 1), content_type="application/x-www-form-urlencoded")
            self.assertEqual(response.status_code, 413)
            attempts.assert_not_called()
            get.assert_not_called()

    def test_database_failure_loading_sessions_returns_safe_503(self):
        with self.client.session_transaction() as saved:
            saved["_user_id"] = "f" * 64
        error = OperationalError("sensitive query", {"secret": "do-not-disclose"}, Exception("private-password"))
        for path in ("/dashboard", "/dashboard/balances"):
            with self.subTest(path=path), patch.object(db.session, "get", side_effect=error), self.assertLogs("toms.errors") as logs:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                for secret in ("sensitive query", "do-not-disclose", "private-password"):
                    self.assertNotIn(secret, response.get_data(as_text=True))
                    self.assertNotIn(secret, "\n".join(logs.output))


class SecuritySessionTests(OwnerLoginFixture):
    def test_csrf_tokens_are_bound_to_browser_and_expire(self):
        token = csrf_token(self.client)
        attacker = app.test_client()
        csrf_token(attacker)
        for invalid in (token + "broken", "invalid"):
            self.assertEqual(self.client.post("/login", data={"csrf_token": invalid}).status_code, 400)
        self.assertEqual(attacker.post("/login", data={"csrf_token": token}).status_code, 400)
        with patch.dict(app.config, {"WTF_CSRF_TIME_LIMIT": -1}):
            self.assertEqual(self.client.post("/login", data={"csrf_token": token}).status_code, 400)
        self.assertEqual(self.session.scalar(text("SELECT count(*) FROM toms.login_attempts")), 0)

    def test_login_rotates_cookie_identity_and_clears_prelogin_state(self):
        old_csrf = csrf_token(self.client)
        prelogin_cookie = self.client.get_cookie("session").value
        with self.client.session_transaction() as saved:
            saved["untrusted_state"] = "discard-me"
        self.assertEqual(self.sign_in().status_code, 302)
        cookie = self.client.get_cookie("session").value
        self.assertNotEqual(cookie, prelogin_cookie)
        with self.client.session_transaction() as saved:
            self.assertNotIn("untrusted_state", saved)
            self.assertNotIn("csrf_token", saved)
            token = saved["_user_id"]
        self.assertEqual(len(token), 64)
        record = self.session.get(BrowserSession, token_hash(token))
        self.assertIsNotNone(record)
        self.assertNotEqual(record.token_hash, token)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": old_csrf}).status_code, 400)
        other = app.test_client()
        other.set_cookie("session", prelogin_cookie)
        self.assertEqual(other.get("/dashboard").status_code, 302)

    def test_logout_without_csrf_does_not_revoke_legitimate_session(self):
        self.sign_in()
        with self.client.session_transaction() as saved:
            token = saved["_user_id"]
        self.assertEqual(self.client.post("/logout").status_code, 400)
        self.assertIsNotNone(self.session.get(BrowserSession, token_hash(token)))

    def test_active_session_cannot_access_any_api_or_bank_route(self):
        self.sign_in()
        for path, method in (("/transactions", "GET"), ("/reports/monthly", "GET"),
                             ("/starling/accounts", "GET"), ("/sync/transactions", "POST"), ("/health/db", "GET")):
            with self.subTest(path=path):
                self.assertEqual(self.client.open(path, method=method).status_code, 401)
        self.bank.assert_not_called()

    def test_failed_session_commit_cannot_issue_authenticated_cookie(self):
        csrf = csrf_token(self.client)
        error = OperationalError("insert with private credentials", {}, Exception("private-password"))
        with patch("routes.login.consume_login_attempt", return_value=True), \
                patch.object(db.session, "commit", side_effect=error), self.assertLogs("toms.errors") as logs:
            response = self.client.post("/login", data={"username": "owner", "password": self.password, "csrf_token": csrf})
        self.assertEqual(response.status_code, 503)
        with self.client.session_transaction() as saved:
            self.assertNotIn("_user_id", saved)
        self.assertNotIn(self.password, response.get_data(as_text=True))
        self.assertNotIn("private-password", "\n".join(logs.output))

    def test_password_injection_and_overlength_input_cannot_create_sessions(self):
        for username, password in (("' OR 1=1 --", self.password), ("owner", "' OR 1=1 --"),
                                   ("owner", "x" * 1025), ("x" * 101, self.password),
                                   ("owner", ""), ("<script>alert(1)</script>", self.password)):
            with self.subTest(username=username):
                response = self.client.post("/login", data={"csrf_token": csrf_token(self.client), "username": username, "password": password})
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("<script>", response.get_data(as_text=True))
        self.assertEqual(self.session.scalar(db.select(db.func.count()).select_from(BrowserSession)), 0)

    def test_browser_session_cannot_override_wrong_bearer_credentials(self):
        self.sign_in()
        for path in ("/dashboard", "/dashboard/balances"):
            self.assertEqual(self.client.get(path, headers={"Authorization": "Bearer wrong"}).status_code, 401)

    def test_wrong_and_unknown_usernames_perform_password_verification(self):
        with patch("routes.login.check_password_hash", wraps=check_password_hash) as verify:
            self.assertEqual(self.sign_in(username="unknown", password="incorrect").status_code, 401)
            unknown_hash = verify.call_args.args[0]
            self.assertEqual(self.sign_in(username="owner", password="incorrect").status_code, 401)
            owner_hash = verify.call_args.args[0]
        self.assertEqual(verify.call_count, 2)
        self.assertTrue(unknown_hash.startswith("scrypt:"))
        self.assertTrue(owner_hash.startswith("scrypt:"))
        self.assertNotEqual(unknown_hash, owner_hash)

    def test_security_headers_cover_success_and_failures(self):
        for response in (self.client.get("/login"), self.client.post("/login"),
                         self.client.get("/dashboard/balances"), self.client.get("/dashboard/missing")):
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertEqual(response.headers["Referrer-Policy"], "same-origin")
            self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_repeated_login_does_not_reset_attempt_budget(self):
        for _ in range(19):
            consume_login_attempt()
        self.assertEqual(self.sign_in().status_code, 302)
        self.assertEqual(self.sign_in().status_code, 429)

    def test_session_activity_updates_idle_time_but_not_absolute_expiry(self):
        self.sign_in()
        with self.client.session_transaction() as saved:
            token = saved["_user_id"]
        record = self.session.get(BrowserSession, token_hash(token))
        absolute_expiry = record.expires_at
        record.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=29)
        self.session.flush()
        with patch.object(db, "paginate", return_value=SimpleNamespace(items=[], total=0, pages=0)):
            self.assertEqual(self.client.get("/dashboard").status_code, 200)
        self.assertLess(datetime.now(timezone.utc) - record.last_seen_at, timedelta(seconds=5))
        self.assertEqual(record.expires_at, absolute_expiry)

    def test_logout_revokes_only_current_device(self):
        second = app.test_client()
        token = csrf_token(second)
        self.assertEqual(second.post("/login", data={"username": "owner", "password": self.password, "csrf_token": token}).status_code, 302)
        self.sign_in()
        self.client.post("/logout", data={"csrf_token": csrf_token(self.client)})
        with patch.object(db, "paginate", return_value=SimpleNamespace(items=[], total=0, pages=0)):
            self.assertEqual(second.get("/dashboard").status_code, 200)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_failed_password_change_does_not_modify_owner_or_sessions(self):
        self.sign_in()
        owner = self.session.get(OwnerLogin, 1)
        old_hash = owner.password_hash
        for password in ("short", "x" * 1025):
            result = app.test_cli_runner().invoke(args=["owner-password", "--username", "owner", "--password", password])
            self.assertNotEqual(result.exit_code, 0)
            self.assertEqual(self.session.get(OwnerLogin, 1).password_hash, old_hash)
        self.assertTrue(check_password_hash(old_hash, self.password))
        self.assertEqual(self.session.scalar(db.select(db.func.count()).select_from(BrowserSession)), 1)


@unittest.skipUnless(os.getenv("RUN_POSTGRES_TESTS") == "1", "Requires PostgreSQL for real concurrency checks.")
class ConcurrentLoginSecurityTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(app.app_context())
        self.engine = db.engine
        self.admin_engine = migration_engine()
        self.addCleanup(self.admin_engine.dispose)
        self.schema = "security_" + uuid4().hex
        with self.admin_engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {self.schema}"))
            connection.execute(text(f"CREATE TABLE {self.schema}.login_attempts (id integer PRIMARY KEY, window_started_at timestamptz NOT NULL, attempts integer NOT NULL)"))
        self.addCleanup(self.drop_schema)
        self.bound_engine = self.engine.execution_options(schema_translate_map={"toms": self.schema})
        admin_bound = self.admin_engine.execution_options(schema_translate_map={"toms": self.schema})
        OwnerLogin.__table__.create(admin_bound)
        BrowserSession.__table__.create(admin_bound)
        OwnerSetup.__table__.create(admin_bound)
        from models import UserAction
        UserAction.__table__.create(admin_bound)
        with self.admin_engine.begin() as connection:
            connection.execute(text(f"GRANT USAGE ON SCHEMA {self.schema} TO toms_app"))
            connection.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {self.schema} TO toms_app"))
        self.sessions = scoped_session(sessionmaker(bind=self.bound_engine))
        self.addCleanup(self.sessions.remove)
        self.enterContext(patch.object(db, "session", self.sessions))
        # Redirect only this helper's SQL into an isolated, randomly named schema.
        self.enterContext(patch("services.web.sessions.text", side_effect=lambda sql: text(sql.replace("toms.login_attempts", self.schema + ".login_attempts"))))

    def drop_schema(self):
        with self.admin_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))

    def test_concurrent_workers_share_exactly_twenty_allowed_attempts(self):
        barrier = Barrier(8)
        def attempt(_):
            try:
                barrier.wait(timeout=10)
                return consume_login_attempt()
            finally:
                self.sessions.remove()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(32)))
        self.assertEqual(sum(results), 20)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text(f"SELECT attempts FROM {self.schema}.login_attempts")), 21)
        # Recreating a session simulates another worker/restart; allowance persists.
        self.assertFalse(consume_login_attempt())

    def test_password_reset_serializes_with_inflight_login_and_revokes_it(self):
        password = "old password long enough for testing"
        self.sessions.add(OwnerLogin(id=1, username="owner", password_hash=generate_password_hash(password)))
        self.sessions.commit()
        replacement = generate_password_hash("replacement password long enough")
        verified, allow_insert, reset_started, reset_finished = (Event() for _ in range(4))
        self.enterContext(patch.dict(app.config, {"SECRET_KEY": "a" * 64, "SESSION_COOKIE_SECURE": False}))
        client = app.test_client()
        csrf = csrf_token(client)

        def verify_then_pause(stored_hash, supplied_password):
            result = check_password_hash(stored_hash, supplied_password)
            verified.set()
            if not allow_insert.wait(timeout=10):
                raise AssertionError("Login worker timed out")
            return result

        def login_worker():
            return client.post("/login", data={"username": "owner", "password": password, "csrf_token": csrf})

        def reset_worker():
            try:
                with self.admin_engine.begin() as connection:
                    reset_started.set()
                    connection.execute(text(f"UPDATE {self.schema}.owner_login SET password_hash = :hash WHERE id = 1"), {"hash": replacement})
                    connection.execute(text(f"DELETE FROM {self.schema}.browser_sessions"))
                reset_finished.set()
            finally:
                self.sessions.remove()

        with patch("routes.login.check_password_hash", side_effect=verify_then_pause), ThreadPoolExecutor(max_workers=2) as pool:
            signing_in = pool.submit(login_worker)
            self.assertTrue(verified.wait(timeout=10))
            resetting = pool.submit(reset_worker)
            self.assertTrue(reset_started.wait(timeout=10))
            try:
                # Password updates must wait until the verified login inserts its
                # session; then the reset transaction revokes that session too.
                self.assertFalse(reset_finished.wait(timeout=0.2))
            finally:
                allow_insert.set()
                response = signing_in.result(timeout=10)
                resetting.result(timeout=10)
        self.assertEqual(response.status_code, 302)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text(f"SELECT count(*) FROM {self.schema}.browser_sessions")), 0)
        self.assertEqual(client.get("/dashboard").status_code, 302)

    def test_simultaneous_setup_requests_create_exactly_one_owner(self):
        self.enterContext(patch.dict(app.config, {"SECRET_KEY": "a" * 64, "SESSION_COOKIE_SECURE": False}))
        token = "only-the-server-knows-this-setup-token"
        self.sessions.add(OwnerSetup(id=1, token_hash=token_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        self.sessions.commit()
        barrier = Barrier(2)
        def create_owner(username):
            client = app.test_client()
            page = client.get("/setup")
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))[1]
            barrier.wait(timeout=10)
            return client.post("/setup", data={"username": username, "setup_token": token,
                "password": "a long enough owner password", "confirmation": "a long enough owner password",
                "csrf_token": csrf}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create_owner, ("first", "second")))
        self.assertEqual(sorted(results), [302, 303])
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text(f"SELECT count(*) FROM {self.schema}.owner_login")), 1)
            self.assertEqual(connection.scalar(text(f"SELECT count(*) FROM {self.schema}.owner_setup")), 0)
