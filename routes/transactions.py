"""Browse saved transactions without contacting Starling."""

import re
from datetime import date, datetime, time, timedelta, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only, selectinload
from werkzeug.exceptions import BadRequest, NotFound

from models import Transaction, TransactionIncome
from routes.helpers import query_values
from services.web.auth import require_api_key
from services.transactions.classification import CLASSIFICATION_TYPES, classification_details, effective_type, save_classification, clear_classification as restore_classification
from services.database.connection import db
from services.error_logging import log_failure
from services.transactions.income import INCOME_TYPES, TAX_TREATMENTS, income_body, income_details, validate_reconciliation
from services.validation import uid, optional_text


# A blueprint groups these URLs; every matched route requires the API key.
transactions = Blueprint("transactions", __name__, url_prefix="/transactions")
transactions.before_request(require_api_key)


@transactions.errorhandler(BadRequest)
@transactions.errorhandler(NotFound)
def invalid_request(error):
    return jsonify(error=error.description), error.code


@transactions.errorhandler(SQLAlchemyError)
def database_error(error):
    log_failure("transactions.database", error)
    return jsonify(error="Unable to read saved transactions. Check PostgreSQL and run flask db-upgrade."), 503


# Explicitly select public fields; raw bank JSON is neither loaded nor returned.
FIELDS = (
    "account_uid", "category_uid", "feed_item_uid", "amount_minor", "currency",
    "direction", "status", "transaction_time", "source_updated_at", "settlement_time",
    "source", "spending_category", "counterparty_name", "reference",
    "source_amount_minor", "source_currency", "fetched_at",
)


def filters():
    """Validate URL filters before list_transactions builds its database query."""
    allowed = {"start", "end", "accountUid", "direction", "status", "page", "per_page", "classification", "income_type", "tax_treatment"}
    values = query_values(allowed)

    for key, default, maximum in (("page", 1, 2**31 - 1), ("per_page", 50, 100)):
        value = values.get(key, str(default))
        if not re.fullmatch(r"[0-9]{1,10}", value) or not 1 <= int(value) <= maximum:
            raise BadRequest(f"{key} must be an integer between 1 and {maximum}.")
        values[key] = int(value)
    if "accountUid" in values:
        try:
            values["accountUid"] = uid(values["accountUid"])
        except ValueError:
            raise BadRequest("accountUid must be a UUID.") from None
    if "direction" in values and values["direction"] not in ("IN", "OUT"):
        raise BadRequest("direction must be IN or OUT.")
    if "classification" in values and values["classification"] not in CLASSIFICATION_TYPES:
        raise BadRequest("Invalid classification. See /transactions/classification-types.")
    for field, choices in (("income_type", INCOME_TYPES), ("tax_treatment", TAX_TREATMENTS)):
        if field in values and values[field] not in choices:
            raise BadRequest(f"Invalid {field}. See /transactions/income-types.")
    # Status is free text: preserve support for all statuses supplied by Starling.
    if "status" in values:
        try:
            values["status"] = optional_text(values["status"], field="status", maximum=100)
        except ValueError as error:
            raise BadRequest(str(error)) from None
    for key in ("start", "end"):
        if key in values:
            try:
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", values[key]):
                    raise ValueError
                day = date.fromisoformat(values[key])
                if key == "end":
                    day += timedelta(days=1)
                values[key] = datetime.combine(day, time.min, timezone.utc)
            except (ValueError, OverflowError):
                raise BadRequest(f"{key} must be a valid YYYY-MM-DD date.") from None
    if "start" in values and "end" in values and values["start"] >= values["end"]:
        raise BadRequest("start must be on or before end.")
    return values


@transactions.get("")
def list_transactions():
    options = filters()
    query = db.select(Transaction).options(
        load_only(*(getattr(Transaction, field) for field in FIELDS)),
        selectinload(Transaction.classification), selectinload(Transaction.income))
    if "classification" in options:
        query = query.outerjoin(Transaction.classification).where(effective_type() == options["classification"])
    if "income_type" in options or "tax_treatment" in options:
        query = query.join(Transaction.income)
        for field in ("income_type", "tax_treatment"):
            if field in options:
                query = query.where(getattr(TransactionIncome, field) == options[field])
    if "start" in options:
        query = query.where(Transaction.transaction_time >= options["start"])
    if "end" in options:
        query = query.where(Transaction.transaction_time < options["end"])
    for parameter, field in (("accountUid", "account_uid"), ("direction", "direction"), ("status", "status")):
        if parameter in options:
            query = query.where(getattr(Transaction, field) == options[parameter])
    # The full primary key breaks timestamp ties deterministically between pages.
    query = query.order_by(Transaction.transaction_time.desc(), Transaction.account_uid,
                           Transaction.category_uid, Transaction.feed_item_uid)
    page = db.paginate(query, page=options["page"], per_page=options["per_page"],
                       max_per_page=100, error_out=False)
    items = []
    for transaction in page.items:
        item = {field: getattr(transaction, field) for field in FIELDS}
        for field, value in item.items():
            if isinstance(value, datetime):
                item[field] = value.astimezone(timezone.utc).isoformat()
        item["classification"] = classification_details(transaction)
        item["income"] = income_details(transaction)
        items.append(item)
    return jsonify(transactions=items, pagination={
        "page": page.page, "per_page": page.per_page, "total": page.total,
        "pages": page.pages, "has_next": page.has_next, "has_prev": page.has_prev,
    })


@transactions.get("/classification-types")
def classification_types():
    return jsonify(types=[{"type": name, **details} for name, details in CLASSIFICATION_TYPES.items()])


def saved_transaction(account_uid, category_uid, feed_item_uid, *, lock=False):
    """Load one saved payment, or raise a JSON 404 through invalid_request above.

    Editing routes use lock=True so simultaneous edits cannot race each other.
    """
    query = db.select(Transaction).where(
        Transaction.account_uid == str(account_uid), Transaction.category_uid == str(category_uid),
        Transaction.feed_item_uid == str(feed_item_uid)).options(
            load_only(Transaction.account_uid, Transaction.category_uid, Transaction.feed_item_uid,
                      Transaction.direction, Transaction.source, Transaction.amount_minor, Transaction.currency),
            selectinload(Transaction.classification), selectinload(Transaction.income))
    # Serialize edits on the parent row to prevent competing inserts for the same key.
    if lock:
        query = query.with_for_update(of=Transaction)
    transaction = db.session.scalar(query)
    if transaction is None:
        raise NotFound("Saved transaction not found.")
    return transaction


CLASSIFICATION_PATH = "/<uuid:account_uid>/<uuid:category_uid>/<uuid:feed_item_uid>/classification"


@transactions.get(CLASSIFICATION_PATH)
def get_classification(account_uid, category_uid, feed_item_uid):
    transaction = saved_transaction(account_uid, category_uid, feed_item_uid)
    return jsonify(classification=classification_details(transaction))


@transactions.put(CLASSIFICATION_PATH)
def put_classification(account_uid, category_uid, feed_item_uid):
    if not request.is_json:
        raise BadRequest("Send a JSON classification with type and optional notes.")
    body = request.get_json()
    if not isinstance(body, dict) or set(body) - {"type", "notes"} or not isinstance(body.get("type"), str):
        raise BadRequest("Use type and optional notes in a JSON object.")
    kind = body["type"]
    if kind not in CLASSIFICATION_TYPES:
        raise BadRequest("Invalid classification. See /transactions/classification-types.")
    try:
        notes = optional_text(body.get("notes"), field="notes", maximum=2000)
    except ValueError as error:
        raise BadRequest(str(error)) from None
    transaction = saved_transaction(account_uid, category_uid, feed_item_uid, lock=True)
    try:
        save_classification(transaction, kind, notes)
    except ValueError as error:
        raise BadRequest(str(error)) from None
    db.session.commit()
    return jsonify(classification=classification_details(transaction))


@transactions.delete(CLASSIFICATION_PATH)
def clear_classification(account_uid, category_uid, feed_item_uid):
    transaction = saved_transaction(account_uid, category_uid, feed_item_uid, lock=True)
    try:
        restore_classification(transaction)
    except ValueError as error:
        raise BadRequest(str(error)) from None
    db.session.commit()
    return jsonify(classification=classification_details(transaction))


@transactions.get("/income-types")
def income_types():
    return jsonify(income_types=[{"type": key, "label": label} for key, label in INCOME_TYPES.items()],
                   tax_treatments=[{"type": key, "label": label} for key, label in TAX_TREATMENTS.items()])


INCOME_PATH = "/<uuid:account_uid>/<uuid:category_uid>/<uuid:feed_item_uid>/income"


@transactions.get(INCOME_PATH)
def get_income(account_uid, category_uid, feed_item_uid):
    transaction = saved_transaction(account_uid, category_uid, feed_item_uid)
    return jsonify(income=income_details(transaction))


@transactions.put(INCOME_PATH)
def put_income(account_uid, category_uid, feed_item_uid):
    """Validate the submitted details, check them against the deposit, and save them."""
    if not request.is_json:
        raise BadRequest("Send income details as a JSON object.")
    values = income_body(request.get_json())
    transaction = saved_transaction(account_uid, category_uid, feed_item_uid, lock=True)
    if transaction.direction != "IN" or classification_details(transaction)["type"] != "income":
        raise BadRequest("Income details require an incoming transaction classified as income.")
    validate_reconciliation(values, transaction.amount_minor)
    # Only a successful edit against the current bank amount clears the review flag.
    values.update(recorded_currency=transaction.currency, needs_review=False)
    transaction.confirmed_at = None
    if transaction.income is None:
        transaction.income = TransactionIncome(**values)
    else:
        for field, value in values.items():
            setattr(transaction.income, field, value)
    db.session.commit()
    return jsonify(income=income_details(transaction))


@transactions.delete(INCOME_PATH)
def clear_income(account_uid, category_uid, feed_item_uid):
    transaction = saved_transaction(account_uid, category_uid, feed_item_uid, lock=True)
    transaction.income = None
    transaction.confirmed_at = None
    db.session.commit()
    return jsonify(income=None)
