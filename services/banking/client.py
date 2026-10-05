"""Call Starling through a reusable HTTPX client with safe error reporting."""

import atexit
import os
import sqlite3
from pathlib import Path

import httpx
from dotenv import load_dotenv
from services.banking.rate_limit import RateLimitError, acquire_slot


# This module is in services/banking; configuration lives at the repository root.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# HTTPX clients reuse connections and can be shared between request threads.
# Credentials are supplied per request, never attached to an arbitrary URL.
http_client = httpx.Client(base_url="https://api.starlingbank.com", timeout=10,
                           follow_redirects=False,
                           headers={"User-Agent": "TOMS-Flask-API/1.0"})
atexit.register(http_client.close)


class StarlingError(Exception):
    """A safe error message that can be returned by the Flask API."""

    def __init__(self, message, status_code=502, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def response_error(code, scope):
    if 300 <= code < 400:
        message = "Starling returned a redirect, which was blocked to protect the token."
    elif code == 401:
        message = "Starling rejected the token. Check its value and expiry."
    elif code == 403:
        message = f"Starling denied access. Check {scope or 'token'} permission."
    else:
        message = f"Starling returned HTTP {code}."
    status = code if code in (400, 404, 409, 422, 429) else 502
    return StarlingError(message, status)


def starling_request(path, *, method="GET", params=None, body=None,
                     accept="application/json", scope=None):
    """Call a fixed API path; keep credentials and upstream error bodies private."""
    token = os.getenv("STARLING_ACCESS_TOKEN", "").strip()
    if not token:
        raise StarlingError("STARLING_ACCESS_TOKEN is missing from .env.", 503)
    if not path.startswith("/api/v2/") or ".." in path:
        raise ValueError("Expected a Starling API v2 path.")

    # Build before acquiring a slot so invalid JSON never consumes a bank attempt.
    request = http_client.build_request(method, path, params=params, json=body,
        headers={"Authorization": f"Bearer {token}", "Accept": accept})
    try:
        acquire_slot(token)
    except RateLimitError as exc:
        raise StarlingError(str(exc), 429, exc.retry_after) from None
    except (sqlite3.Error, OSError):
        raise StarlingError("Unable to check the Starling request limit.", 503) from None

    try:
        response = http_client.send(request)
    except (httpx.RequestError, OSError):
        raise StarlingError("Unable to reach Starling. Please try again later.") from None
    if not response.is_success:
        raise response_error(response.status_code, scope)
    if accept != "application/json":
        return response.content
    try:
        return response.json() if response.content else None
    except (ValueError, UnicodeError):
        raise StarlingError("Starling returned an invalid JSON response.") from None


def get_account_holder_name():
    """Fetch the name using account-holder-name:read permission."""
    data = starling_request("/api/v2/account-holder/name", scope="account-holder-name:read")
    name = data.get("accountHolderName") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise StarlingError("Starling did not return an account holder name.")
    return name
