"""Account earmarks and receipt notices, restricted to the signed-in owner."""
import hmac

from flask import Blueprint, jsonify, redirect, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, Conflict

from models import Account, AccountReserve, IncomeNoticeRead, Transaction
from services.database.connection import db
from services.error_logging import log_failure
from services.database.session import test_data_active
from services.notifications.income import notices, notice_version
from services.tax.deduction_forms import payment_key
from services.tax.reserves import current_plan
from services.transactions.classification import classification_details
from services.transactions.income_form import parse_amount
from services.validation import uid
from services.web.activity import record_action
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data
from routes.dashboard import format_amount
from routes.helpers import query_values

reserves = Blueprint('reserves', __name__, url_prefix='/dashboard')
reserves.before_request(require_dashboard_login)


@reserves.before_request
def owner_only():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


reserves.before_request(activate_test_data)


@reserves.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure('reserves.database', error)
    return jsonify(error='Income notices and reserves are unavailable. Check database migrations.'), 503


@reserves.errorhandler(BadRequest)
@reserves.errorhandler(Conflict)
def invalid_request(error):
    db.session.rollback()
    return jsonify(error=error.description), error.code


@reserves.errorhandler(ValueError)
def invalid_value(error):
    db.session.rollback()
    return jsonify(error=str(error)), 400


def check_form(fields):
    if set(request.form) - set(fields) or any(len(request.form.getlist(key)) != 1 for key in request.form):
        raise BadRequest('Supply each field once.')


@reserves.get('/income-notifications')
def notifications():
    query_values(set())
    if request.method == 'HEAD':
        return jsonify(notices=[], count=0)
    rows = notices(current_plan(), format_amount)
    return jsonify(notices=rows[:20], count=len(rows))


@reserves.post('/income-notifications/read')
def acknowledge():
    query_values(set())
    check_form({'csrf_token', 'payment', 'version'})
    key = payment_key(request.form.get('payment', ''))
    payment = db.session.scalar(db.select(Transaction).where(
        Transaction.account_uid == key[0], Transaction.category_uid == key[1], Transaction.feed_item_uid == key[2]
    ).with_for_update().execution_options(populate_existing=True))
    if (payment is None or payment.direction != 'IN' or payment.status != 'SETTLED'
            or classification_details(payment)['type'] != 'income'):
        raise BadRequest('Choose a settled incoming income payment.')
    version = notice_version(payment)
    if not hmac.compare_digest(request.form.get('version', '').encode(), version.encode()):
        raise Conflict('This payment changed. Refresh the notices before acknowledging it.')
    row = db.session.get(IncomeNoticeRead, key)
    if row is None:
        row = IncomeNoticeRead(account_uid=key[0], category_uid=key[1], feed_item_uid=key[2])
        db.session.add(row)
    row.payment_version = version
    db.session.commit()
    return '', 204


@reserves.post('/account-reserves')
def save():
    query_values(set())
    check_form({'csrf_token', 'account', 'version', 'tax', 'ni'})
    identity = uid(request.form.get('account', ''))
    # Lock the parent even for the first insert, preventing two initial earmarks.
    account = db.session.scalar(db.select(Account).where(Account.account_uid == identity).with_for_update())
    if account is None:
        raise BadRequest('Choose an available account.')
    row = db.session.get(AccountReserve, identity, populate_existing=True)
    revision = str(row.revision) if row else '0'
    if request.form.get('version') != revision:
        raise Conflict('These earmarks changed. Reload the income streams page before saving.')
    tax = parse_amount(request.form.get('tax', ''), account.currency)
    ni = parse_amount(request.form.get('ni', ''), account.currency)
    if tax is None or ni is None or tax + ni > 2**63 - 1:
        raise ValueError('Enter nonnegative tax and NI amounts within the supported money range.')
    if row is None:
        row = AccountReserve(account_uid=identity, revision=1)
        db.session.add(row)
    else:
        row.revision += 1
    row.tax_minor, row.ni_minor = tax, ni
    record_action('reserve.save', identity, sample=test_data_active())
    db.session.commit()
    return redirect(url_for('dashboard.income_streams', _anchor='account-reserves'), code=303)
