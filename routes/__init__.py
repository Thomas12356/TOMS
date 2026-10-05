"""Shared setup and query validation for private API blueprints."""

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

from services.auth import require_api_key
from services.error_logging import log_failure


def json_error(error):
    return jsonify(error=error.description), error.code


def private_blueprint(name, *, database_error=None):
    blueprint = Blueprint(name, __name__, url_prefix=f"/{name}")
    blueprint.before_request(require_api_key)
    blueprint.register_error_handler(BadRequest, json_error)
    if database_error is not None:
        @blueprint.errorhandler(SQLAlchemyError)
        def unavailable_database(error):
            log_failure(f"{name}.database", error)
            return jsonify(error=database_error), 503
    return blueprint


def query_values(allowed, *, unknown_error="Unknown query parameter."):
    """Read optional query values, rejecting unknown, repeated or empty entries."""
    if set(request.args) - set(allowed):
        raise BadRequest(unknown_error)
    values = {}
    for key in request.args:
        entries = request.args.getlist(key)
        if len(entries) != 1 or not entries[0].strip():
            raise BadRequest(f"Supply {key} once with a nonempty value.")
        values[key] = entries[0].strip()
    return values
