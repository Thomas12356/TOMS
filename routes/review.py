"""Owner confirmation of saved transactions; no bank writes."""
import hmac
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only, selectinload
from werkzeug.exceptions import BadRequest

from models import Transaction, TransactionIncome
from routes.dashboard import format_amount, requested_account, requested_page
from routes.helpers import query_values
from services.database.connection import db
from services.error_logging import log_failure
from services.transactions.classification import CLASSIFICATION_TYPES, classification_details
from services.transactions.income import INCOME_TYPES, TAX_TREATMENTS, income_details
from services.transactions.review import REVIEW_FIELDS, review_version
from services.transactions.income_streams import STREAM_KINDS
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data

review = Blueprint('review', __name__, url_prefix='/dashboard')
review.before_request(require_dashboard_login)
review.before_request(activate_test_data)


@review.errorhandler(BadRequest)
def bad_request(error):
    return jsonify(error=error.description), 400


@review.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure('review.database', error)
    return jsonify(error='Transaction review is unavailable. Try again later.'), 503


def transaction_query():
    return db.select(Transaction).options(
        load_only(*(getattr(Transaction, field) for field in REVIEW_FIELDS), Transaction.confirmed_at),
        selectinload(Transaction.classification), selectinload(Transaction.income).selectinload(TransactionIncome.income_stream))


@review.get('/review')
def next_transaction():
    options = query_values({'account', 'page'})
    account_uid = requested_account(options)
    page_number = requested_page(options)
    query = transaction_query().where(Transaction.confirmed_at.is_(None))
    if account_uid:
        query = query.where(Transaction.account_uid == account_uid)
    count = db.session.scalar(db.select(db.func.count()).select_from(query.subquery()))
    transaction = db.session.scalar(query.order_by(Transaction.transaction_time.desc(), Transaction.account_uid,
                                   Transaction.category_uid, Transaction.feed_item_uid).limit(1))
    if transaction is None:
        return jsonify(remaining=0, transaction=None)
    classification = classification_details(transaction)
    details = [
        ['Payment', transaction.counterparty_name or 'Unnamed payment'],
        ['Reference', transaction.reference or 'No reference'],
        ['Date (UTC)', transaction.transaction_time.astimezone(timezone.utc).strftime('%d %b %Y, %H:%M')],
        ['Direction', 'Money in' if transaction.direction == 'IN' else 'Money out'],
        ['Amount', format_amount(transaction.amount_minor, transaction.currency)],
        ['Bank status', transaction.status.replace('_', ' ').title()],
        ['Classification', CLASSIFICATION_TYPES[classification['type']]['label']],
        ['Classification origin', classification['origin'].title()],
        ['Notes', classification['notes'] or 'None'],
    ]
    income = income_details(transaction)
    if income:
        stream = transaction.income.income_stream
        details.append(['Income stream', stream.name if stream else 'Not assigned'])
        details.extend([['Income type', STREAM_KINDS[stream.kind] if stream else INCOME_TYPES[income['income_type']]],
                        ['Tax treatment', TAX_TREATMENTS[income['tax_treatment']]],
                        ['Income source', income['source_name'] or 'Not specified'],
                        ['Income currency', income['recorded_currency']]])
        for field, label in (('gross_minor', 'Gross income'), ('tax_deducted_minor', 'Tax deducted'), ('adjustment_minor', 'Adjustment')):
            details.append([label, format_amount(income[field], income['recorded_currency']) if income[field] is not None else 'Not specified'])
        details.extend([['Adjustment notes', income['adjustment_notes'] or 'None'],
                        ['Income needs review', 'Yes' if income['needs_review'] else 'No']])
    key = dict(account_uid=transaction.account_uid, category_uid=transaction.category_uid, feed_item_uid=transaction.feed_item_uid)
    return jsonify(remaining=count, transaction={
        'details': details, 'version': review_version(transaction),
        'confirm_url': url_for('review.confirm_transaction', **key),
        'edit_url': url_for('dashboard.edit_classification', **key, account=account_uid, page=page_number),
    })


@review.post('/transactions/<uuid:account_uid>/<uuid:category_uid>/<uuid:feed_item_uid>/confirm')
def confirm_transaction(account_uid, category_uid, feed_item_uid):
    if request.authorization is not None or not current_user.is_authenticated:
        return jsonify(error='Browser login is required.'), 401
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {'version'} or not isinstance(body['version'], str) or len(body['version']) != 64:
        raise BadRequest('Send the version of the transaction you reviewed.')
    transaction = db.session.scalar(transaction_query().where(
        Transaction.account_uid == str(account_uid), Transaction.category_uid == str(category_uid),
        Transaction.feed_item_uid == str(feed_item_uid)).with_for_update(of=Transaction))
    if transaction is None:
        return jsonify(error='Saved transaction not found.'), 404
    if not hmac.compare_digest(body['version'].encode(), review_version(transaction).encode()):
        return jsonify(error='These details changed. Review the updated transaction before confirming.'), 409
    if transaction.confirmed_at is None:
        transaction.confirmed_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(confirmed=True)
