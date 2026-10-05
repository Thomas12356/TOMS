"""The first dashboard page: browse saved transactions, 50 at a time."""

import re
from datetime import timezone

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only, selectinload
from werkzeug.exceptions import BadRequest

from models import Account, Transaction
from routes.helpers import query_values
from services.database.connection import db
from services.error_logging import log_failure
from services.transactions.classification import CLASSIFICATION_TYPES, classification_details
from services.web.auth import require_api_key
from services.validation import uid
from services.banking.client import StarlingError, starling_request


dashboard = Blueprint("dashboard", __name__, url_prefix="/dashboard")
dashboard.before_request(require_api_key)


@dashboard.errorhandler(BadRequest)
def invalid_request(error):
    if request.endpoint == "dashboard.account_balances":
        return jsonify(error=error.description), 400
    return render_template("dashboard.html", error=error.description, page=None), 400


@dashboard.errorhandler(SQLAlchemyError)
def database_error(error):
    log_failure("dashboard.database", error)
    message = "Unable to load transactions. Check PostgreSQL and run flask db-upgrade."
    if request.endpoint == "dashboard.account_balances":
        return jsonify(error="Unable to load saved accounts. Check PostgreSQL."), 503
    return render_template("dashboard.html", error=message, page=None), 503


def format_amount(amount_minor, currency):
    """Format common two-decimal currencies without converting money to floats."""
    if currency in ("GBP", "EUR", "USD"):
        sign = "−" if amount_minor < 0 else ""
        amount_minor = abs(amount_minor)
        return f"{sign}{currency} {amount_minor // 100:,}.{amount_minor % 100:02d}"
    # Other currencies can have different decimal scales, so preserve their units.
    return f"{currency} {amount_minor:,} minor units"


def requested_account(options):
    """Selecting an account filters the view without modifying saved records."""
    if options.get("account", "all") == "all":
        return None
    try:
        return uid(options["account"])
    except ValueError:
        raise BadRequest("account must be a valid account ID.") from None


@dashboard.get("/balances")
def account_balances():
    """Load live main-account balances separately from the saved transaction table."""
    if request.method == "HEAD":
        return jsonify(balances=[])

    account_uid = requested_account(query_values({"account"}))
    query = db.select(Account).options(
        load_only(Account.account_uid, Account.name, Account.currency)
    ).order_by(Account.account_uid)
    if account_uid:
        query = query.where(Account.account_uid == account_uid)
    accounts = list(db.session.scalars(query))
    if account_uid and not accounts:
        raise BadRequest("This account is not available. Choose a saved account.")
    balances = []
    rate_limited = False
    for account in accounts:
        card = {"name": account.name or f"Starling {account.currency} account",
                "amount": None, "error": None}
        if rate_limited:
            card["error"] = "Starling rate limit reached. Try again later."
        else:
            try:
                payload = starling_request(f"/api/v2/accounts/{account.account_uid}/balance", scope="balance:read")
                balance = payload.get("effectiveBalance") if isinstance(payload, dict) else None
                # Balances may be negative, unlike individual transaction amounts.
                if (not isinstance(balance, dict) or type(balance.get("minorUnits")) is not int
                        or not -(2**63) <= balance["minorUnits"] <= 2**63 - 1
                        or balance.get("currency") != account.currency):
                    raise StarlingError("Starling returned an invalid account balance.")
                card["amount"] = format_amount(balance["minorUnits"], balance["currency"])
            except StarlingError as error:
                card["error"] = str(error)
                rate_limited = error.status_code == 429
        balances.append(card)
    return jsonify(balances=balances)


@dashboard.get("")
def transactions_page():
    # 1. Read the requested page and reject invalid input before accessing PostgreSQL.
    options = query_values({"page", "account"})
    account_uid = requested_account(options)
    page_number = options.get("page", "1")
    if not re.fullmatch(r"[0-9]{1,10}", page_number) or not 1 <= int(page_number) <= 2**31 - 1:
        raise BadRequest("page must be a positive integer up to 2147483647.")

    accounts = list(db.session.scalars(db.select(Account).options(
        load_only(Account.account_uid, Account.name, Account.currency)
    ).order_by(Account.account_uid)))
    selected_account = next((item for item in accounts if item.account_uid == account_uid), None)
    if account_uid and selected_account is None:
        raise BadRequest("This account is not available. Choose a saved account.")

    # 2. Select only the fields this page displays, leaving private raw bank JSON unloaded.
    query = db.select(Transaction).options(
        load_only(Transaction.transaction_time, Transaction.counterparty_name,
                  Transaction.reference, Transaction.amount_minor, Transaction.currency,
                  Transaction.direction, Transaction.status, Transaction.source),
        selectinload(Transaction.classification),
    ).order_by(Transaction.transaction_time.desc(), Transaction.account_uid,
               Transaction.category_uid, Transaction.feed_item_uid)
    if account_uid:
        query = query.where(Transaction.account_uid == account_uid)
    page = db.paginate(query, page=int(page_number), per_page=50, error_out=False)

    # 3. Give the template plain display values; database work stays in this route.
    rows = []
    for transaction in page.items:
        classification = classification_details(transaction)
        rows.append({
            "date": transaction.transaction_time.astimezone(timezone.utc).strftime("%d %b %Y"),
            "counterparty": transaction.counterparty_name or "Unnamed payment",
            "reference": transaction.reference,
            "amount": format_amount(transaction.amount_minor, transaction.currency),
            "direction": transaction.direction,
            "status": transaction.status,
            "classification": CLASSIFICATION_TYPES[classification["type"]]["label"],
            "origin": classification["origin"],
        })

    return render_template("dashboard.html", page=page, rows=rows, error=None,
                           accounts=accounts, selected_account=selected_account, account_uid=account_uid)
