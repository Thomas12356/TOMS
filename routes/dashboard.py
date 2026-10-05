"""Browse saved transactions, view balances and edit local classifications."""

import re
from datetime import timezone

from flask import Blueprint, jsonify, render_template, request, redirect, url_for, current_app
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only, selectinload
from werkzeug.exceptions import BadRequest, NotFound

from models import Account, Transaction
from routes.helpers import query_values
from services.database.connection import db
from services.error_logging import log_failure
from services.transactions.automatic_sync import latest_sync, start_dashboard_sync
from services.transactions.classification import CLASSIFICATION_TYPES, classification_details, save_classification, clear_classification
from services.web.sessions import require_dashboard_login
from services.validation import uid, optional_text
from services.banking.client import StarlingError, starling_request


dashboard = Blueprint("dashboard", __name__, url_prefix="/dashboard")
dashboard.before_request(require_dashboard_login)


@dashboard.errorhandler(NotFound)
@dashboard.errorhandler(BadRequest)
def invalid_request(error):
    if request.endpoint in ("dashboard.account_balances", "dashboard.trigger_sync", "dashboard.sync_status"):
        return jsonify(error=error.description), 400
    return render_template("dashboard.html", error=error.description, page=None), error.code


@dashboard.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure("dashboard.database", error)
    message = "Unable to load transactions. Check PostgreSQL and run flask db-upgrade."
    if request.endpoint in ("dashboard.account_balances", "dashboard.trigger_sync", "dashboard.sync_status"):
        return jsonify(error="Unable to load saved accounts. Check PostgreSQL."), 503
    return render_template("dashboard.html", error=message, page=None), 503


@dashboard.post("/sync")
def trigger_sync():
    if not current_user.is_authenticated:
        return jsonify(error="Browser login is required."), 401
    options = query_values({"force"})
    if options.get("force", "0") not in ("0", "1"):
        raise BadRequest("force must be 0 or 1.")
    started = start_dashboard_sync(current_app._get_current_object(), force=options.get("force") == "1")
    return jsonify(started=started), 202


@dashboard.get("/sync-status")
def sync_status():
    return jsonify(run=latest_sync())


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


def requested_page(options):
    """Keep pagination validation identical for the ledger and edit return links."""
    value = options.get("page", "1")
    if not re.fullmatch(r"[0-9]{1,10}", value) or not 1 <= int(value) <= 2**31 - 1:
        raise BadRequest("page must be a positive integer up to 2147483647.")
    return int(value)


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
    page_number = requested_page(options)

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
    page = db.paginate(query, page=page_number, per_page=50, error_out=False)

    # 3. Give the template plain display values; database work stays in this route.
    rows = []
    for number, transaction in enumerate(page.items, start=(page_number - 1) * 50 + 1):
        classification = classification_details(transaction)
        rows.append({
            "number": number,
            "date": transaction.transaction_time.astimezone(timezone.utc).strftime("%d %b %Y"),
            "counterparty": transaction.counterparty_name or "Unnamed payment",
            "reference": transaction.reference,
            "amount": format_amount(transaction.amount_minor, transaction.currency),
            "direction": transaction.direction,
            "status": transaction.status,
            "classification": CLASSIFICATION_TYPES[classification["type"]]["label"],
            "origin": classification["origin"],
            "edit_url": url_for("dashboard.edit_classification", account_uid=transaction.account_uid,
                                category_uid=transaction.category_uid, feed_item_uid=transaction.feed_item_uid,
                                account=account_uid, page=page_number),
        })

    return render_template("dashboard.html", page=page, rows=rows, error=None,
                           accounts=accounts, selected_account=selected_account, account_uid=account_uid,
                           current_user=current_user)


@dashboard.route("/transactions/<uuid:account_uid>/<uuid:category_uid>/<uuid:feed_item_uid>/classification", methods=["GET", "POST"])
def edit_classification(account_uid, category_uid, feed_item_uid):
    if not current_app.secret_key or len(current_app.secret_key) < 32:
        return render_template("dashboard.html", error="Set SECRET_KEY before using dashboard editing forms.", page=None), 503

    # 1. Keep return navigation constrained to our dashboard, never arbitrary URLs.
    options = query_values({"page", "account"})
    selected_account = requested_account(options)
    page_number = requested_page(options)
    back_url = url_for("dashboard.transactions_page", account=selected_account, page=page_number)

    # 2. Read the payment without its raw payload; lock the parent on saves.
    query = db.select(Transaction).where(
        Transaction.account_uid == str(account_uid), Transaction.category_uid == str(category_uid),
        Transaction.feed_item_uid == str(feed_item_uid)).options(
            load_only(Transaction.direction, Transaction.source, Transaction.amount_minor, Transaction.currency,
                      Transaction.counterparty_name, Transaction.reference),
            selectinload(Transaction.classification), selectinload(Transaction.income))
    if request.method == "POST":
        query = query.with_for_update(of=Transaction)
    transaction = db.session.scalar(query)
    if transaction is None:
        raise NotFound("Saved transaction not found.")
    details = classification_details(transaction)
    kind, notes = details["type"], details["notes"] or ""
    error = None

    # 3. Validate the form and use the same rules as the JSON API.
    if request.method == "POST":
        kind, notes = request.form.get("type", ""), request.form.get("notes", "")
        try:
            if set(request.form) - {"csrf_token", "type", "notes", "action"} or any(len(request.form.getlist(key)) != 1 for key in request.form):
                raise ValueError("Supply each form field once.")
            action = request.form.get("action", "save")
            if action == "automatic":
                clear_classification(transaction)
            elif action == "save":
                clean_notes = optional_text(notes, field="notes", maximum=2000)
                save_classification(transaction, kind, clean_notes)
            else:
                raise ValueError("Choose a valid action.")
        except ValueError as invalid:
            db.session.rollback()
            error = str(invalid)
        else:
            db.session.commit()
            return redirect(back_url, code=303)

    choices = {key: value for key, value in CLASSIFICATION_TYPES.items()
               if transaction.direction in value["directions"]}
    return render_template("classification.html", transaction=transaction,
                           amount=format_amount(transaction.amount_minor, transaction.currency),
                           choices=choices, kind=kind, notes=notes, origin=details["origin"],
                           back_url=back_url, error=error), 400 if error else 200
