"""Browser authentication. The API's bearer keys remain separate."""

import hashlib
from datetime import datetime, timedelta, timezone

from flask import current_app, jsonify, redirect, request, url_for
from flask_login import LoginManager, UserMixin, current_user
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import text

from models import BrowserSession
from services.database.connection import db
from services.web.auth import require_api_key

login_manager = LoginManager()
csrf = CSRFProtect()
IDLE_TIMEOUT = timedelta(minutes=30)
SESSION_LIFETIME = timedelta(hours=8)


class LoggedInOwner(UserMixin):
    def __init__(self, token):
        self.id = token


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@login_manager.user_loader
def load_owner(token):
    if not isinstance(token, str) or len(token) != 64:
        return None
    saved = db.session.get(BrowserSession, token_hash(token))
    now = datetime.now(timezone.utc)
    if saved is None:
        return None
    if saved.expires_at <= now or saved.last_seen_at + IDLE_TIMEOUT <= now:
        db.session.delete(saved)
        db.session.commit()
        return None
    saved.last_seen_at = now
    db.session.commit()
    return LoggedInOwner(token)


def require_dashboard_login():
    # Only explicitly supplied bearer credentials can bypass browser sessions.
    auth = request.authorization
    if auth is not None and auth.type == "bearer":
        return require_api_key()
    if not current_app.secret_key or len(current_app.secret_key) < 32:
        return jsonify(error="Dashboard login requires a random SECRET_KEY of at least 32 characters. See the setup guide."), 503
    if not current_user.is_authenticated:
        if request.endpoint == "dashboard.account_balances":
            return jsonify(error="Please sign in again."), 401
        return redirect(url_for("login.sign_in"))
    csrf.protect()


def consume_login_attempt():
    """Count attempts atomically: at most 20 per fifteen minutes, across workers."""
    count = db.session.execute(text("""
        INSERT INTO toms.login_attempts (id, window_started_at, attempts)
        VALUES (1, now(), 1)
        ON CONFLICT (id) DO UPDATE SET
            window_started_at = CASE
                WHEN login_attempts.window_started_at <= now() - interval '15 minutes'
                THEN now() ELSE login_attempts.window_started_at END,
            attempts = CASE
                WHEN login_attempts.window_started_at <= now() - interval '15 minutes'
                THEN 1 ELSE least(login_attempts.attempts + 1, 21) END
        RETURNING attempts
    """)).scalar_one()
    db.session.commit()
    return count <= 20
