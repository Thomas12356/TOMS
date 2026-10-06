import os
import sys

from sqlalchemy.exc import SQLAlchemyError
import click
from flask import Flask, jsonify, render_template, request, redirect, url_for, session
from werkzeug.exceptions import RequestEntityTooLarge

from routes.starling import starling
from routes.sync import sync
from routes.transactions import transactions
from routes.reports import reports
from routes.dashboard import dashboard
from routes.deductions import deductions
from routes.shifts import shifts
from routes.review import review
from routes.login import login
from services.web.sessions import login_manager, csrf, SESSION_LIFETIME, lock_owner_setup, validate_owner_credentials
from flask_wtf.csrf import CSRFError
from werkzeug.security import generate_password_hash
from services.web.auth import require_api_key
from services.database.connection import check_database, db, init_database
from models import (Account, BrowserSession, Category, OwnerLogin, OwnerSetup, Transaction,
                    TransactionClassification, TransactionIncome, SyncRun, SyncTarget)
from services.database.migrations import upgrade_database
from services.database.migration_connection import migration_engine
from services.database.demo_schema import prepare_demo_schema
from services.error_logging import log_failure
from services.web.request_limits import BoundedRequest
from services.web.setup import announce_setup, create_setup_token


app = Flask(__name__)
app.request_class = BoundedRequest
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MiB for JSON options and receipt metadata.
app.config["APP_API_KEY"] = os.getenv("APP_API_KEY", "").strip()
# Secure cookies are the default for HTTPS through Tailscale Serve.
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "").strip() or None,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "1") != "0",
    PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME,
    SESSION_REFRESH_EACH_REQUEST=False,
    WTF_CSRF_CHECK_DEFAULT=False,  # Browser blueprints explicitly protect forms.
)
# Pass the current-user proxy only to templates that need it. The default
# context processor eagerly loads sessions, even while rendering database errors.
login_manager.init_app(app, add_context_processor=False)
csrf.init_app(app)
app.register_blueprint(login)
app.register_blueprint(starling)
app.register_blueprint(sync)
app.register_blueprint(transactions)
app.register_blueprint(reports)
app.register_blueprint(dashboard)
app.register_blueprint(deductions)
app.register_blueprint(shifts)
app.register_blueprint(review)
init_database(app)


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(error):
    return jsonify(error="Request body is too large.", max_bytes=app.config["MAX_CONTENT_LENGTH"]), 413


@app.context_processor
def test_data_context():
    return {'using_test_data': session.get('use_test_data') is True and request.authorization is None}


@app.shell_context_processor
def shell_context():
    return {"db": db, "Account": Account, "Category": Category,
            "Transaction": Transaction, "TransactionClassification": TransactionClassification,
            "TransactionIncome": TransactionIncome, "SyncRun": SyncRun, "SyncTarget": SyncTarget}


@app.after_request
def protect_banking_responses(response):
    if (request.path.startswith(("/starling/", "/sync/", "/transactions/", "/reports/", "/dashboard/"))
            or request.path in ("/login", "/logout", "/setup", "/settings/password", "/health/db", "/transactions", "/reports", "/dashboard")):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.errorhandler(CSRFError)
def csrf_failure(error):
    return render_template("login.html", error="The form expired or could not be verified. Reload the page and try again.", ready=True), 400


@app.get("/")
def index():
    return redirect(url_for("dashboard.transactions_page"))


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/health/db")
def database_health():
    auth_error = require_api_key()
    if auth_error is not None:
        return auth_error
    try:
        name = check_database()
    except SQLAlchemyError as error:
        log_failure("health.database", error)
        return jsonify(status="error", error="Unable to connect to PostgreSQL. Check the service and PG settings in .env."), 503
    return jsonify(status="ok", database=name)


@app.cli.command("db-upgrade")
def db_upgrade():
    """Apply versioned transaction-storage migrations to the configured database."""
    try:
        engine = migration_engine()
        try:
            with engine.begin() as connection:
                applied = upgrade_database(connection)
                prepare_demo_schema(connection, rebuild=bool(applied))
                connection.exec_driver_sql("REVOKE ALL ON TABLE toms.schema_migrations FROM PUBLIC, toms_app")
        finally:
            engine.dispose()
    except SQLAlchemyError as error:
        log_failure("migrations.database", error)
        raise click.ClickException("Database migration failed. Check PostgreSQL and PG settings.") from None
    except RuntimeError as error:
        raise click.ClickException(str(error)) from None
    click.echo("Applied: " + ", ".join(applied) if applied else "Database is already up to date.")


@app.cli.command("owner-password")
@click.option("--username", prompt=True)
@click.password_option(confirmation_prompt=True)
def owner_password(username, password):
    """Create/update the single owner and revoke all previous browser sessions."""
    try:
        username = validate_owner_credentials(username, password)
    except ValueError as error:
        raise click.ClickException(str(error)) from None
    try:
        lock_owner_setup()
        db.session.merge(OwnerLogin(id=1, username=username,
                                   password_hash=generate_password_hash(password, method="scrypt")))
        db.session.execute(db.delete(BrowserSession))
        db.session.execute(db.delete(OwnerSetup))
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        log_failure("owner.database", error)
        raise click.ClickException("Owner setup failed. Check PostgreSQL and run flask db-upgrade.") from None
    click.echo("Owner password saved. All previous browser sessions have been revoked.")


@app.cli.command("owner-setup-token")
def owner_setup_token():
    """Generate a one-hour token for the first-run setup page; revoke older tokens."""
    try:
        token = create_setup_token()
        if token is None:
            raise click.ClickException("An owner already exists. Use the password settings page or owner-password for recovery.")
    except SQLAlchemyError as error:
        db.session.rollback()
        log_failure("owner.setup", error)
        raise click.ClickException("Setup token generation failed. Check PostgreSQL and run flask db-upgrade.") from None
    click.echo("Open /setup and enter this token within one hour:")
    click.echo(token)


@app.cli.command("refresh-tax-rules")
def refresh_tax_rules():
    """Check official UK rates, retaining reviewed rules if verification fails."""
    from services.tax.rules import refresh_rules
    try:
        result = refresh_rules(app.instance_path, force=True)
    except OSError:
        raise click.ClickException("Unable to save the rule check in the instance directory.") from None
    click.echo(result['status'] + ': ' + result['message'])
    from services.tax.mileage_rules import refresh_rules as refresh_mileage
    mileage = refresh_mileage(app.instance_path, force=True)
    click.echo(mileage['status'] + ': ' + mileage['message'])


@app.cli.command("sync-transactions")
def scheduled_sync():
    """Import transactions without running a web server (used by the daily timer)."""
    from services.transactions.store import SyncStore
    from services.transactions.sync import SyncError, run_sync
    try:
        report = run_sync(SyncStore(), {})
    except SyncError as error:
        if error.status_code == 409:
            click.echo("Another sync is already running.")
            return
        raise click.ClickException(str(error)) from None
    click.echo("Transaction sync completed: " + str(report.get("run_uid", "")))


# Flask's CLI starts its own server rather than calling app.run(). Only the
# initial launcher prints a token; reloader children reuse it.
if (os.getenv("FLASK_RUN_FROM_CLI") == "true" and "run" in sys.argv
        and os.getenv("WERKZEUG_RUN_MAIN") != "true"):
    announce_setup(app)


if __name__ == "__main__":
    announce_setup(app)
    app.run(host=os.getenv("FLASK_RUN_HOST", "127.0.0.1"),
            port=int(os.getenv("FLASK_RUN_PORT", "5000")))
