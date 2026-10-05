"""Login and logout forms for the single owner. No signup or recovery emails."""

import secrets
from datetime import datetime, timezone

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from models import BrowserSession, OwnerLogin
from services.database.connection import db
from services.error_logging import log_failure
from services.web.sessions import (
    LoggedInOwner, SESSION_LIFETIME, consume_login_attempt, csrf, token_hash,
)

login = Blueprint("login", __name__)
# Checking an unknown username still performs the same expensive password hash.
DUMMY_HASH = generate_password_hash(secrets.token_hex(32), method="scrypt")


@login.before_request
def protect_forms():
    if not current_app.secret_key or len(current_app.secret_key) < 32:
        return render_template("login.html", error="Set a random SECRET_KEY of at least 32 characters before using dashboard login.", ready=False), 503
    csrf.protect()


@login.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure("login.database", error)
    return render_template("login.html", error="Login is unavailable. Check PostgreSQL and run flask db-upgrade.", ready=False), 503


@login.route("/login", methods=["GET", "POST"])
def sign_in():
    # 1. Bound login attempts before querying or hashing credentials.
    if request.method == "POST" and not consume_login_attempt():
        return render_template("login.html", error="Too many attempts. Try again in fifteen minutes.", ready=True), 429, {"Retry-After": "900"}

    # 2. Check the one owner record; never reveal which credential was wrong.
    # Lock during POST verification/session creation so a concurrent password
    # reset waits, then revokes the new session along with all previous ones.
    owner = db.session.get(OwnerLogin, 1, with_for_update=request.method == "POST")
    if owner is None:
        return render_template("login.html", error="Create your owner account with flask owner-password first.", ready=False), 503
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        valid_input = len(username) <= 100 and len(password) <= 1024
        password_ok = check_password_hash(
            owner.password_hash if valid_input and username == owner.username else DUMMY_HASH,
            password if valid_input else "",
        )
        if not valid_input or username != owner.username or not password_ok:
            return render_template("login.html", error="Incorrect username or password.", ready=True), 401

        # 3. Create a revocable session. Only its random token goes in the signed cookie.
        now = datetime.now(timezone.utc)
        token = secrets.token_hex(32)
        db.session.execute(db.delete(BrowserSession).where(BrowserSession.expires_at <= now))
        db.session.add(BrowserSession(token_hash=token_hash(token), created_at=now,
                                     last_seen_at=now, expires_at=now + SESSION_LIFETIME))
        db.session.commit()
        session.clear()
        session.permanent = True
        login_user(LoggedInOwner(token), remember=False)
        # A fixed destination avoids untrusted redirect URLs.
        return redirect(url_for("dashboard.transactions_page"))
    return render_template("login.html", error=None, ready=True)


@login.post("/logout")
def sign_out():
    if current_user.is_authenticated:
        saved = db.session.get(BrowserSession, token_hash(current_user.get_id()))
        if saved is not None:
            db.session.delete(saved)
            db.session.commit()
    logout_user()
    session.clear()
    return redirect(url_for("login.sign_in"))
