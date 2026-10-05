"""Authenticated manual imports and sync progress reports."""

from datetime import datetime, timezone

from flask import jsonify, request
from werkzeug.exceptions import BadRequest

from routes import private_blueprint
from services.sync_store import SyncStore
from services.transaction_sync import SyncError, parse_options, run_sync


sync = private_blueprint("sync", database_error=
    "Unable to access the sync database. Check PostgreSQL and run flask db-upgrade.")


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
