"""Small helpers for reading HTTP query parameters; business rules stay in services."""

from flask import request
from werkzeug.exceptions import BadRequest


def query_values(allowed, *, unknown_error="Unknown query parameter.", empty_ignored=()):
    """Read optional query values, rejecting unknown, repeated or empty entries."""
    if set(request.args) - set(allowed):
        raise BadRequest(unknown_error)
    values = {}
    for key in request.args:
        entries = request.args.getlist(key)
        if len(entries) != 1:
            raise BadRequest(f"Supply {key} once with a nonempty value.")
        if not entries[0].strip():
            if key in empty_ignored:
                continue
            raise BadRequest(f"Supply {key} once with a nonempty value.")
        values[key] = entries[0].strip()
    return values
