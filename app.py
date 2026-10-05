import os

from sqlalchemy.exc import SQLAlchemyError
import click
from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from routes.starling import starling
from routes.sync import sync
from routes.transactions import transactions
from routes.reports import reports
from routes.dashboard import dashboard
from services.web.auth import require_api_key
from services.database.connection import check_database, db, init_database
from models import Account, Category, Transaction, TransactionClassification, TransactionIncome, SyncRun, SyncTarget
from services.database.migrations import upgrade_database
from services.error_logging import log_failure
from services.web.request_limits import BoundedRequest


app = Flask(__name__)
app.request_class = BoundedRequest
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MiB for JSON options and receipt metadata.
app.config["APP_API_KEY"] = os.getenv("APP_API_KEY", "").strip()
app.register_blueprint(starling)
app.register_blueprint(sync)
app.register_blueprint(transactions)
app.register_blueprint(reports)
app.register_blueprint(dashboard)
init_database(app)


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(error):
    return jsonify(error="Request body is too large.", max_bytes=app.config["MAX_CONTENT_LENGTH"]), 413


@app.shell_context_processor
def shell_context():
    return {"db": db, "Account": Account, "Category": Category,
            "Transaction": Transaction, "TransactionClassification": TransactionClassification,
            "TransactionIncome": TransactionIncome, "SyncRun": SyncRun, "SyncTarget": SyncTarget}


@app.after_request
def protect_banking_responses(response):
    if (request.path.startswith(("/starling/", "/sync/", "/transactions/", "/reports/", "/dashboard/"))
            or request.path in ("/health/db", "/transactions", "/reports", "/dashboard")):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/")
def index():
    return jsonify(message="Welcome to the Flask API")


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
        with db.engine.begin() as connection:
            applied = upgrade_database(connection)
    except SQLAlchemyError as error:
        log_failure("migrations.database", error)
        raise click.ClickException("Database migration failed. Check PostgreSQL and PG settings.") from None
    except RuntimeError as error:
        raise click.ClickException(str(error)) from None
    click.echo("Applied: " + ", ".join(applied) if applied else "Database is already up to date.")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
