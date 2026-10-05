"""Starling endpoints. UUIDs are supplied in paths; filters use query strings."""

import math
import re
from datetime import date

from flask import Blueprint, Response, current_app, jsonify, request
from werkzeug.exceptions import BadRequest

from services.web.auth import require_api_key
from services.banking.client import StarlingError, get_account_holder_name, starling_request
from services.banking.diagnostics import run_diagnostics
from services.validation import timestamp


# A blueprint groups these URLs; every matched route requires the API key.
starling = Blueprint("starling", __name__, url_prefix="/starling")
starling.before_request(require_api_key)


@starling.errorhandler(BadRequest)
def invalid_request(error):
    return jsonify(error=error.description), error.code


@starling.get("/account-holder/name")
def account_holder_name():
    return jsonify(accountHolderName=get_account_holder_name())


@starling.route("/test", methods=["GET", "POST"])
def test_all():
    """Run live checks and report individual results without exposing bank data."""
    if request.method == "HEAD":
        return Response(status=200)
    if request.method == "GET":
        if request.content_length or request.args:
            raise BadRequest("Use POST with JSON for diagnostic options. GET runs read checks only.")
        options = {}
    elif request.content_length:
        if not request.is_json:
            raise BadRequest("Send diagnostic options as JSON.")
        options = request.get_json()
    else:
        options = {}
    return jsonify(run_diagnostics(current_app._get_current_object(), options))


@starling.errorhandler(StarlingError)
def upstream_error(error):
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else {}
    return jsonify(error=str(error)), error.status_code, headers


def query_params(required=(), optional=(), *, dates=(), timestamps=(), integers=()):
    """Validate and forward only documented query parameters."""
    allowed = set(required) | set(optional)
    if set(request.args) - allowed:
        raise BadRequest("Unknown query parameter.")
    values = {}
    for key in allowed:
        entries = request.args.getlist(key)
        if len(entries) > 1:
            raise BadRequest(f"Supply {key} only once.")
        value = entries[0].strip() if entries else ""
        if not value:
            if key in required or entries:
                raise BadRequest(f"Supply {key} as a query parameter.")
            continue
        try:
            if key in dates:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError
                date.fromisoformat(value)
            if key in timestamps:
                timestamp(value)
            if key in integers:
                if not re.fullmatch(r"\d+", value) or int(value) > 2**63 - 1:
                    raise ValueError
        except ValueError:
            raise BadRequest(f"Invalid {key}.") from None
        values[key] = value
    for start, end in (("start", "end"),
                       ("minTransactionTimestamp", "maxTransactionTimestamp")):
        if start in values and end in values:
            parse = timestamp if start in timestamps else date.fromisoformat
            if parse(values[start]) > parse(values[end]):
                raise BadRequest(f"{start} must be before or equal to {end}.")
    return values


def json_result(path, scope, *, params=None, method="GET", body=None):
    result = starling_request(path, scope=scope, params=params, method=method, body=body)
    if result is None:
        return Response(status=204)
    return jsonify(result)


def download(path, scope, media_type, filename, params=None):
    content = starling_request(path, scope=scope, accept=media_type, params=params)
    return Response(content, mimetype=media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
    })


@starling.get("/accounts")
def accounts():
    return json_result("/api/v2/accounts", "account-list:read")


@starling.get("/accounts/<uuid:account_uid>/balance")
def balance(account_uid):
    return json_result(f"/api/v2/accounts/{account_uid}/balance", "balance:read")


@starling.get("/accounts/<uuid:account_uid>/confirmation-of-funds")
def confirmation_of_funds(account_uid):
    params = query_params(("targetAmountInMinorUnits",), integers=("targetAmountInMinorUnits",))
    return json_result(f"/api/v2/accounts/{account_uid}/confirmation-of-funds",
                       "confirmation-of-funds:read", params=params)


@starling.get("/direct-debit/mandates")
def mandates():
    return json_result("/api/v2/direct-debit/mandates", "mandate:read")


@starling.get("/direct-debit/mandates/<uuid:mandate_uid>")
def mandate(mandate_uid):
    return json_result(f"/api/v2/direct-debit/mandates/{mandate_uid}", "mandate:read")


@starling.get("/payees")
def payees():
    return json_result("/api/v2/payees", "payee:read")


@starling.get("/payees/<uuid:payee_uid>")
def payee(payee_uid):
    return json_result(f"/api/v2/payees/{payee_uid}", "payee:read")


@starling.get("/payees/<uuid:payee_uid>/image")
def payee_image(payee_uid):
    return download(f"/api/v2/payees/{payee_uid}/image", "payee-image:read",
                    "image/png", "payee.png")


@starling.get("/payees/<uuid:payee_uid>/account/<uuid:account_uid>/payments")
def payee_transactions(payee_uid, account_uid):
    params = query_params(("since",), dates=("since",))
    return json_result(f"/api/v2/payees/{payee_uid}/account/{account_uid}/payments",
                       "payee-transaction:read", params=params)


@starling.get("/payees/<uuid:payee_uid>/account/<uuid:account_uid>/scheduled-payments")
def scheduled_payments(payee_uid, account_uid):
    return json_result(f"/api/v2/payees/{payee_uid}/account/{account_uid}/scheduled-payments",
                       "scheduled-payment:read")


@starling.get("/payments/local/payment-order/<uuid:payment_order_uid>")
def payment_order(payment_order_uid):
    return json_result(f"/api/v2/payments/local/payment-order/{payment_order_uid}", "pay-local:read")


@starling.get("/payments/local/payment-order/<uuid:payment_order_uid>/payments")
def payment_order_payments(payment_order_uid):
    return json_result(f"/api/v2/payments/local/payment-order/{payment_order_uid}/payments",
                       "pay-local:read")


@starling.get("/feed/account/<uuid:account_uid>/category/<uuid:category_uid>/<uuid:feed_item_uid>/receipts")
def receipts(account_uid, category_uid, feed_item_uid):
    return json_result(f"/api/v2/feed/account/{account_uid}/category/{category_uid}/{feed_item_uid}/receipts",
                       "receipts:read")


@starling.put("/feed/account/<uuid:account_uid>/category/<uuid:category_uid>/<uuid:feed_item_uid>/receipt")
def submit_receipt(account_uid, category_uid, feed_item_uid):
    """Create or edit receipt metadata; no write occurs until this route is called."""
    if not request.is_json:
        raise BadRequest("Send a JSON receipt with Content-Type: application/json.")
    body = request.get_json()
    required = {"items", "metadataSource", "paymentMethods", "receiptIdentifier",
                "receiptMerchant", "totalAmount"}
    if not isinstance(body, dict) or required - body.keys():
        raise BadRequest("Receipt requires: " + ", ".join(sorted(required)))
    if body["metadataSource"] not in ("CUSTOMER", "STARLING", "PARTNER"):
        raise BadRequest("Invalid metadataSource.")
    if not isinstance(body["receiptIdentifier"], str) or not body["receiptIdentifier"].strip():
        raise BadRequest("receiptIdentifier must be a nonempty string.")
    if not isinstance(body["receiptMerchant"], dict):
        raise BadRequest("receiptMerchant must be an object.")
    for key in ("items", "paymentMethods"):
        if not isinstance(body[key], list) or any(not isinstance(item, dict) for item in body[key]):
            raise BadRequest(f"{key} must be an array of objects.")
    amount = body["totalAmount"]
    try:
        valid_amount = (not isinstance(amount, bool) and isinstance(amount, (int, float))
                        and math.isfinite(amount))
    except (OverflowError, ValueError):
        valid_amount = False
    if not valid_amount:
        raise BadRequest("totalAmount must be a finite number.")
    try:
        return json_result(f"/api/v2/feed/account/{account_uid}/category/{category_uid}/{feed_item_uid}/receipt",
                           "metadata:create or metadata:edit", method="PUT", body=body)
    except ValueError:
        raise BadRequest("Receipt must contain valid JSON values.") from None


@starling.get("/account/<uuid:account_uid>/savings-goals")
def savings_goals(account_uid):
    return json_result(f"/api/v2/account/{account_uid}/savings-goals", "savings-goal:read")


@starling.get("/account/<uuid:account_uid>/savings-goals/<uuid:savings_goal_uid>")
def savings_goal(account_uid, savings_goal_uid):
    return json_result(f"/api/v2/account/{account_uid}/savings-goals/{savings_goal_uid}", "savings-goal:read")


@starling.get("/account/<uuid:account_uid>/savings-goals/<uuid:savings_goal_uid>/recurring-transfer")
def savings_goal_transfer(account_uid, savings_goal_uid):
    return json_result(f"/api/v2/account/{account_uid}/savings-goals/{savings_goal_uid}/recurring-transfer",
                       "savings-goal-transfer:read")


@starling.get("/account/<uuid:account_uid>/spaces")
def spaces(account_uid):
    return json_result(f"/api/v2/account/{account_uid}/spaces", "space:read")


@starling.get("/payments/local/account/<uuid:account_uid>/category/<uuid:category_uid>/standing-orders")
def standing_orders(account_uid, category_uid):
    return json_result(f"/api/v2/payments/local/account/{account_uid}/category/{category_uid}/standing-orders",
                       "standing-order:read")


@starling.get("/payments/local/account/<uuid:account_uid>/category/<uuid:category_uid>/standing-orders/<uuid:payment_order_uid>")
def standing_order(account_uid, category_uid, payment_order_uid):
    return json_result(f"/api/v2/payments/local/account/{account_uid}/category/{category_uid}/standing-orders/{payment_order_uid}",
                       "standing-order:read")


def statement(account_uid, extension):
    params = query_params(("yearMonth",))
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", params["yearMonth"]):
        raise BadRequest("yearMonth must use YYYY-MM format.")
    media_type = "application/pdf" if extension == "pdf" else "text/csv"
    return download(f"/api/v2/accounts/{account_uid}/statement/download",
                    f"statement-{extension}:read", media_type, f"statement.{extension}", params)


@starling.get("/accounts/<uuid:account_uid>/statement/pdf")
def statement_pdf(account_uid):
    return statement(account_uid, "pdf")


@starling.get("/accounts/<uuid:account_uid>/statement/csv")
def statement_csv(account_uid):
    return statement(account_uid, "csv")


@starling.get("/accounts/<uuid:account_uid>/feed-export")
def feed_export(account_uid):
    params = query_params(("start",), ("end",), dates=("start", "end"))
    return download(f"/api/v2/accounts/{account_uid}/feed-export", "feed-export-csv:read",
                    "text/csv", "feed-export.csv", params)


@starling.get("/feed/account/<uuid:account_uid>/category/<uuid:category_uid>")
def transactions(account_uid, category_uid):
    params = query_params(("changesSince",), timestamps=("changesSince",))
    return json_result(f"/api/v2/feed/account/{account_uid}/category/{category_uid}",
                       "transaction:read", params=params)


@starling.get("/feed/account/<uuid:account_uid>/category/<uuid:category_uid>/transactions-between")
def transactions_between(account_uid, category_uid):
    keys = ("minTransactionTimestamp", "maxTransactionTimestamp")
    params = query_params(keys, timestamps=keys)
    return json_result(f"/api/v2/feed/account/{account_uid}/category/{category_uid}/transactions-between",
                       "transaction:read", params=params)


@starling.get("/feed/account/<uuid:account_uid>/category/<uuid:category_uid>/<uuid:feed_item_uid>")
def transaction(account_uid, category_uid, feed_item_uid):
    return json_result(f"/api/v2/feed/account/{account_uid}/category/{category_uid}/{feed_item_uid}",
                       "transaction:read")
