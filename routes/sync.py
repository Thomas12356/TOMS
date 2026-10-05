"""Authenticated manual imports and sync progress reports."""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

from services.auth import require_api_key
from services.error_logging import log_failure
from services.sync_store import SyncStore
from services.transaction_sync import SyncError, parse_options, run_sync


# A blueprint groups these URLs; every matched route requires the API key.
sync = Blueprint("sync", __name__, url_prefix="/sync")
sync.before_request(require_api_key)


@sync.errorhandler(BadRequest)
def invalid_request(error):
    return jsonify(error=error.description), error.code


@sync.errorhandler(SQLAlchemyError)
def database_error(error):
    log_failure("sync.database", error)
    return jsonify(error="Unable to access the sync database. Check PostgreSQL and run flask db-upgrade."), 503


@sync.errorhandler(SyncError)
def failed_sync(error):
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else {}
    return jsonify(error=str(error), run_uid=error.run_uid), error.status_code, headers


@sync.post("/transactions")
def sync_transactions():
    if request.get_data(cache=True):
        if not request.is_json:
            raise BadRequest("Send sync options as JSON.")
        options = request.get_json()
    else:
        options = {}
    # Reject invalid options before constructing the database repository.
    parse_options(options, datetime.now(timezone.utc))
    return jsonify(run_sync(SyncStore(), options))


@sync.get("/runs")
def recent_runs():
    repository = SyncStore()
    if not repository.ready():
        raise SyncError("Database tables are missing. Run flask db-upgrade first.", 503)
    return jsonify(runs=repository.recent())


@sync.get("/runs/<uuid:run_uid>")
def run_report(run_uid):
    repository = SyncStore()
    if not repository.ready():
        raise SyncError("Database tables are missing. Run flask db-upgrade first.", 503)
    report = repository.report(run_uid)
    if report is None:
        return jsonify(error="Sync run not found."), 404
    return jsonify(report)
