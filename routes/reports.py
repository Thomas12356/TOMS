"""Monthly cash-flow summaries from saved transactions."""

import re
from datetime import datetime, timezone

from flask import Blueprint, jsonify
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

from models import Transaction, TransactionIncome
from routes.helpers import query_values
from services.auth import require_api_key
from services.classification import effective_type
from services.database import db
from services.error_logging import log_failure
from services.validation import uid


# A blueprint groups these URLs; every matched route requires the API key.
reports = Blueprint("reports", __name__, url_prefix="/reports")
reports.before_request(require_api_key)


@reports.errorhandler(BadRequest)
def invalid_request(error):
    return jsonify(error=error.description), error.code


@reports.errorhandler(SQLAlchemyError)
def database_error(error):
    log_failure("reports.database", error)
    return jsonify(error="Unable to read the monthly report. Check PostgreSQL and run flask db-upgrade."), 503


def monthly_options():
    """Turn month/account query strings into validated report dates and an account ID."""
    values = query_values({"month", "accountUid"}, unknown_error="Use month and optional accountUid.")
    month = values.get("month", "")
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
            raise ValueError
        year, number = map(int, month.split("-"))
        start = datetime(year, number, 1, tzinfo=timezone.utc)
        # Use the first day of the next month as an exclusive upper bound.
        if number == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, number + 1, 1, tzinfo=timezone.utc)
    except ValueError:
        raise BadRequest("Supply a valid month in YYYY-MM format.") from None
    account = values.get("accountUid")
    if account is not None:
        try:
            account = uid(account)
        except ValueError:
            raise BadRequest("accountUid must be a UUID.") from None
    return month, start, end, account


@reports.get("/monthly")
def monthly_report():
    """Read grouped database totals, build currency summaries, then return JSON."""
    month, start, end, account = monthly_options()
    # Manual transfer types override inference from the bank source.
    internal = effective_type() == "internal_transfer"
    is_income = effective_type() == "income"
    income_type = db.func.coalesce(TransactionIncome.income_type, "unclassified")
    tax_treatment = db.func.coalesce(TransactionIncome.tax_treatment, "unknown")
    # Treat both empty bank categories and the bank's NONE label as uncategorised.
    category = db.func.nullif(Transaction.spending_category, "")
    category = db.func.nullif(category, "NONE")
    category = db.func.coalesce(category, "UNCATEGORISED")
    query = db.select(
        Transaction.currency, Transaction.direction, category.label("category"),
        internal.label("internal"), is_income.label("is_income"),
        income_type.label("income_type"), tax_treatment.label("tax_treatment"), db.func.sum(Transaction.amount_minor).label("amount"),
        db.func.count().label("transaction_count"),
        db.func.sum(db.case((TransactionIncome.needs_review.is_(True), 1), else_=0)).label("needs_review_count"),
    ).outerjoin(Transaction.classification).outerjoin(Transaction.income).where(Transaction.status == "SETTLED", Transaction.transaction_time >= start,
            Transaction.transaction_time < end)
    if account:
        query = query.where(Transaction.account_uid == account)
    query = query.group_by(Transaction.currency, Transaction.direction, category, internal, is_income, income_type, tax_treatment)

    currencies = {}
    for row in db.session.execute(query):
        total = currencies.setdefault(row.currency, {
            "currency": row.currency, "income_minor": 0, "spending_minor": 0,
            "net_minor": 0, "transaction_count": 0, "income_needing_review": 0, "spending_by_category": {}, "income_by_type": {},
            "excluded_internal_transfers": {"transaction_count": 0, "incoming_minor": 0, "outgoing_minor": 0},
        })
        amount, count = int(row.amount), int(row.transaction_count)
        total["income_needing_review"] += int(row.needs_review_count)
        if row.internal:
            excluded = total["excluded_internal_transfers"]
            excluded["transaction_count"] += count
            excluded["incoming_minor" if row.direction == "IN" else "outgoing_minor"] += amount
            continue
        total["transaction_count"] += count
        if row.direction == "IN":
            total["income_minor"] += amount
            if row.is_income:
                key = (row.income_type, row.tax_treatment)
                income = total["income_by_type"].setdefault(key, {
                    "income_type": row.income_type, "tax_treatment": row.tax_treatment,
                    "net_received_minor": 0, "transaction_count": 0})
                income["net_received_minor"] += amount
                income["transaction_count"] += count
        else:
            total["spending_minor"] += amount
            spending = total["spending_by_category"].setdefault(row.category, {
                "category": row.category, "amount_minor": 0, "transaction_count": 0})
            spending["amount_minor"] += amount
            spending["transaction_count"] += count
    for total in currencies.values():
        total["net_minor"] = total["income_minor"] - total["spending_minor"]
        total["spending_by_category"] = sorted(total["spending_by_category"].values(),
            key=lambda item: (-item["amount_minor"], item["category"]))
        total["income_by_type"] = [total["income_by_type"][key] for key in sorted(total["income_by_type"])]
    return jsonify(month=month, timezone="UTC", account_uid=account, status="SETTLED",
                   period_start=start.isoformat(), period_end=end.isoformat(),
                   currencies=[currencies[key] for key in sorted(currencies)])
