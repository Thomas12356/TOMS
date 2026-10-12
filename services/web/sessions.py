"""Browser authentication. The API's bearer keys remain separate."""

import hashlib
from datetime import datetime, timedelta, timezone

from flask import current_app, jsonify, redirect, request, url_for, render_template
from flask_login import LoginManager, UserMixin, current_user
from services.web.signing import ClockTolerantCSRFProtect
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from models import BrowserSession, LoginAttempt
from services.database.connection import db
from services.web.auth import require_api_key

login_manager = LoginManager()
csrf = ClockTolerantCSRFProtect()
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
    # User-activated document navigation also works when JavaScript is disabled.
    # Script reloads and background fetches do not carry Sec-Fetch-User.
    if (request.headers.get('Sec-Fetch-Mode') == 'navigate'
            and request.headers.get('Sec-Fetch-User') == '?1'
            and not renew_owner_activity(token)):
        return None
    return LoggedInOwner(token)


def renew_owner_activity(token):
    """Atomically renew a live session; never recreate a revoked or expired one."""
    now = datetime.now(timezone.utc)
    result = db.session.execute(db.update(BrowserSession).where(
        BrowserSession.token_hash == token_hash(token),
        BrowserSession.expires_at > now,
        BrowserSession.last_seen_at > now - IDLE_TIMEOUT,
    ).values(last_seen_at=db.func.greatest(BrowserSession.last_seen_at, now)))
    db.session.commit()
    return result.rowcount == 1


def require_dashboard_login():
    json_response = request.endpoint in {
        "dashboard.account_balances", "dashboard.trigger_sync", "dashboard.sync_status",
        "review.next_transaction", "review.confirm_transaction",
    }
    # Only explicitly supplied bearer credentials can bypass browser sessions.
    auth = request.authorization
    if auth is not None and auth.type == "bearer":
        return require_api_key()
    if not current_app.secret_key or len(current_app.secret_key) < 32:
        message = "Set a random SECRET_KEY of at least 32 characters in .env, then restart Flask."
        if json_response:
            return jsonify(error=message), 503
        return render_template("login.html", error=message, ready=False), 503
    if not current_user.is_authenticated:
        if json_response:
            return jsonify(error="Please sign in again."), 401
        return redirect(url_for("login.sign_in"))
    csrf.protect()


def consume_login_attempt():
    """Count attempts atomically: at most 20 per fifteen minutes, across workers."""
    now = db.func.now()
    expired = LoginAttempt.window_started_at <= now - timedelta(minutes=15)
    statement = insert(LoginAttempt).values(id=1, window_started_at=now, attempts=1)
    statement = statement.on_conflict_do_update(index_elements=[LoginAttempt.id], set_={
        'window_started_at': db.case((expired, now), else_=LoginAttempt.window_started_at),
        'attempts': db.case((expired, 1), else_=db.func.least(LoginAttempt.attempts + 1, 21)),
    }).returning(LoginAttempt.attempts)
    count = db.session.execute(statement).scalar_one()
    db.session.commit()
    return count <= 20


def lock_owner_setup():
    """Serialize first-owner creation, token rotation and CLI recovery."""
    db.session.execute(text("SELECT pg_advisory_xact_lock(6075157141257144333)"))


def validate_owner_credentials(username, password):
    """Use identical credential requirements in setup, settings and recovery."""
    from services.validation import optional_text

    username = optional_text(username, field="username", maximum=100)
    if not username or not 8 <= len(password) <= 1024:
        raise ValueError("Use a username of 1–100 characters and a password of 8–1024 characters.")
    return username
