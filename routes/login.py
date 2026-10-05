"""Login and logout forms for the single owner. No signup or recovery emails."""

import secrets
import hmac
from datetime import datetime, timezone

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for, flash
from flask_login import current_user, login_user, logout_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash

from models import BrowserSession, OwnerLogin, OwnerSetup
from services.database.connection import db
from services.error_logging import log_failure
from services.web.sessions import (
    LoggedInOwner, SESSION_LIFETIME, consume_login_attempt, csrf, token_hash,
    lock_owner_setup, validate_owner_credentials,
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
        return redirect(url_for("login.setup_owner"))
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


@login.route("/setup", methods=["GET", "POST"])
def setup_owner():
    # This form is only available before an owner exists; it never replaces one.
    if request.method == "POST":
        if not consume_login_attempt():
            return render_template("owner_settings.html", setup=True, ready=True, error="Too many attempts. Try again in fifteen minutes."), 429, {"Retry-After": "900"}
        lock_owner_setup()
    if db.session.get(OwnerLogin, 1) is not None:
        return redirect(url_for("login.sign_in"))
    setup = db.session.get(OwnerSetup, 1)
    now = datetime.now(timezone.utc)
    if setup is None or setup.expires_at <= now:
        return render_template("owner_settings.html", setup=True, ready=False,
                               error="Restart the server to generate a setup token in its terminal. Tokens expire after one hour."), 503
    error = None
    if request.method == "POST":
        supplied = request.form.get("setup_token", "")
        if len(supplied) > 200 or not hmac.compare_digest(token_hash(supplied), setup.token_hash):
            error = "Setup token is incorrect or expired."
        else:
            try:
                if any(len(request.form.getlist(key)) != 1 for key in request.form):
                    raise ValueError("Supply each form field once.")
                password = request.form.get("password", "")
                username = validate_owner_credentials(request.form.get("username", ""), password)
                if password != request.form.get("confirmation", ""):
                    raise ValueError("Passwords do not match.")
            except ValueError as invalid:
                error = str(invalid)
            else:
                db.session.add(OwnerLogin(id=1, username=username,
                                          password_hash=generate_password_hash(password, method="scrypt")))
                db.session.delete(setup)
                db.session.execute(db.delete(BrowserSession))
                db.session.commit()
                session.clear()
                flash("Owner account created. Sign in with your new password.")
                return redirect(url_for("login.sign_in"), code=303)
        db.session.rollback()
    return render_template("owner_settings.html", setup=True, ready=True, error=error), 400 if error else 200


@login.route("/settings/password", methods=["GET", "POST"])
def change_password():
    # API keys cannot change the owner's browser password.
    if not current_user.is_authenticated:
        return redirect(url_for("login.sign_in"))
    if request.method == "POST" and not consume_login_attempt():
        return render_template("owner_settings.html", setup=False, ready=True, error="Too many attempts. Try again in fifteen minutes."), 429, {"Retry-After": "900"}
    owner = db.session.get(OwnerLogin, 1, with_for_update=request.method == "POST")
    if owner is None:
        logout_user()
        session.clear()
        return redirect(url_for("login.sign_in"))
    error = None
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        if len(current_password) > 1024 or not check_password_hash(owner.password_hash, current_password):
            error = "Current password is incorrect."
        else:
            try:
                if any(len(request.form.getlist(key)) != 1 for key in request.form):
                    raise ValueError("Supply each form field once.")
                password = request.form.get("password", "")
                validate_owner_credentials(owner.username, password)
                if password != request.form.get("confirmation", ""):
                    raise ValueError("Passwords do not match.")
            except ValueError as invalid:
                error = str(invalid)
            else:
                owner.password_hash = generate_password_hash(password, method="scrypt")
                db.session.execute(db.delete(BrowserSession))
                db.session.commit()
                logout_user()
                session.clear()
                flash("Password changed. Sign in again. All previous sessions were revoked.")
                return redirect(url_for("login.sign_in"), code=303)
        db.session.rollback()
    return render_template("owner_settings.html", setup=False, ready=True, error=error), 400 if error else 200
