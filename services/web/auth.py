"""Authentication for private API endpoints."""

import hmac

from flask import current_app, jsonify, request


def require_api_key():
    expected = current_app.config.get("APP_API_KEY", "")
    if not expected:
        return jsonify(error="APP_API_KEY is not configured."), 503
    auth = request.authorization
    supplied = ""
    if auth is not None:
        if auth.type == "bearer":
            supplied = auth.token or ""
        elif auth.type == "basic" and auth.username == "api":
            supplied = auth.password or ""
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        return jsonify(error="Authentication required."), 401, {
            "WWW-Authenticate": 'Basic realm="Personal accountant API"',
        }
