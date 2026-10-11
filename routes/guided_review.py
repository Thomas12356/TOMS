"""Progressive HTML classification, details and explicit saved-record confirmation."""
import hmac

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only, selectinload
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from models import Transaction, TransactionIncome, IncomeStream, IncomeShift
from routes.helpers import query_values
from services.database.connection import db
from services.database.session import test_data_active
from services.error_logging import log_failure
from services.tax.readiness import check_income
from services.tax.records import expense_current
from services.transactions.classification import CLASSIFICATION_TYPES, classification_details
from services.transactions.guided_review import FORM_FIELDS, apply_action, initial_values, version
from services.transactions.income import INCOME_TYPES, TAX_TREATMENTS
from services.transactions.ledger import FILTERS, ledger_options, filtered_transactions
from services.transactions.review import REVIEW_FIELDS
from services.transactions.shifts import shift_label
from services.transactions.suggestions import payment_suggestions
from services.web.presentation import format_amount, local_date
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data
from services.web.tasks import TASK_FILTERS

guided_review = Blueprint('guided_review', __name__, url_prefix='/dashboard')
guided_review.before_request(require_dashboard_login)


@guided_review.before_request
def owner_only():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


guided_review.before_request(activate_test_data)


def query():
    return db.select(Transaction).options(load_only(*(getattr(Transaction, field) for field in REVIEW_FIELDS),
        Transaction.confirmed_at, Transaction.source_updated_at), selectinload(Transaction.classification),
        selectinload(Transaction.income).selectinload(TransactionIncome.income_stream),
        selectinload(Transaction.expense))


def options():
    values = query_values(FILTERS | {'step', 'return_to', 'tasks'}, empty_ignored={'q', 'from', 'to'})
    context = ledger_options(values)
    if 'return_to' in values or 'tasks' in values:
        if values.get('return_to') != 'home' or values.get('tasks', 'all') not in TASK_FILTERS or context:
            raise BadRequest('Choose Home with a valid action category, or ledger filters.')
        context = {'return_to': 'home', 'tasks': values.get('tasks', 'all')}
    return context


def back_url(context):
    if context.get('return_to') == 'home':
        return url_for('overview.page', tasks=context['tasks'], _anchor='home-actions')
    return url_for('dashboard.transactions_page', **context)


def render(error=None, status=200, **values):
    return render_template('guided_review.html', current_user=current_user, error=error,
        format_amount=format_amount, local_date=local_date, **values), status


@guided_review.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure('guided_review.database', error)
    return render(error='Transaction review is unavailable. Check PostgreSQL and database migrations.', status=503)


@guided_review.errorhandler(BadRequest)
@guided_review.errorhandler(NotFound)
def bad_request(error):
    return render(error=error.description, status=error.code)


@guided_review.get('/review/start')
def start():
    context = options()
    if 'step' in request.args:
        raise BadRequest('Choose a transaction before selecting a review step.')
    selection = filtered_transactions(query(), context).where(Transaction.confirmed_at.is_(None))
    payment = db.session.scalar(selection.order_by(Transaction.transaction_time.desc(), Transaction.account_uid,
        Transaction.category_uid, Transaction.feed_item_uid).limit(1))
    if payment:
        return redirect(url_for('guided_review.payment', account_uid=payment.account_uid,
            category_uid=payment.category_uid, feed_item_uid=payment.feed_item_uid, **context))
    return render(complete=True, back_url=back_url(context),
                  back_label='Back to Home' if context.get('return_to') == 'home' else 'Back to transactions')


@guided_review.route('/transactions/<uuid:account_uid>/<uuid:category_uid>/<uuid:feed_item_uid>/review', methods=['GET', 'POST'])
def payment(account_uid, category_uid, feed_item_uid):
    context = options()
    step = request.args.get('step', 'classification')
    if step not in ('classification', 'details', 'check'):
        raise BadRequest('Choose classification, details or check.')
    selection = query().where(Transaction.account_uid == str(account_uid),
        Transaction.category_uid == str(category_uid), Transaction.feed_item_uid == str(feed_item_uid))
    if request.method == 'POST':
        # Shared deduction/stream lock first, then payment lock, matching existing
        # expense writers and serializing evidence edits across workers.
        db.session.execute(db.select(db.func.pg_advisory_xact_lock(6075157141257144400 + int(test_data_active()))))
        selection = selection.with_for_update(of=Transaction).execution_options(populate_existing=True)
    record = db.session.scalar(selection)
    if record is None:
        raise NotFound('Saved transaction not found.')
    saved_version = version(record)
    values = initial_values(record)
    error, status, stale = None, 200, False
    key = dict(account_uid=str(account_uid), category_uid=str(category_uid), feed_item_uid=str(feed_item_uid))
    if request.method == 'POST':
        values.update(request.form.to_dict())
        try:
            action = request.form.get('action', '')
            allowed = FORM_FIELDS.get(action)
            if allowed is None or set(request.form) - allowed - {'csrf_token', 'action', 'version'} or any(
                    len(request.form.getlist(field)) != 1 for field in request.form):
                raise BadRequest('Supply valid review fields once.')
            submitted = request.form.get('version', '')
            if len(submitted) != 64 or not hmac.compare_digest(submitted.encode(), saved_version.encode()):
                raise Conflict('These saved details changed. Your entries are retained; reload the current record before saving or confirming.')
            if action == 'confirm' and step != 'check':
                raise BadRequest('Open the saved-details check before confirming.')
            if action in ('preview_classification', 'preview_source'):
                proposal_kind = 'classification' if action == 'preview_classification' else 'source'
                if step != ('classification' if proposal_kind == 'classification' else 'details'):
                    raise BadRequest('Open the appropriate review step before using a suggestion.')
                proposal = payment_suggestions(record).get(proposal_kind)
                if proposal is None or not hmac.compare_digest(request.form.get('suggestion_version', '').encode(), proposal['version'].encode()):
                    raise Conflict('This suggestion changed. Reload and review the current evidence.')
                values['type' if proposal_kind == 'classification' else 'income_stream_id'] = proposal['value']
                values['suggestion_preview'] = 'Suggestion placed in the form. Check it and save to change this payment.'
            else:
                next_step = apply_action(record, request.form)
                db.session.commit()
                if next_step == 'done':
                    if context.get('return_to') == 'home':
                        return redirect(back_url(context), code=303)
                    return redirect(url_for('guided_review.start', **context), code=303)
                return redirect(url_for('guided_review.payment', **key, **context, step=next_step), code=303)
        except (BadRequest, Conflict, ValueError) as invalid:
            db.session.rollback()
            error = invalid.description if isinstance(invalid, (BadRequest, Conflict)) else str(invalid)
            status = invalid.code if isinstance(invalid, (BadRequest, Conflict)) else 400
            stale = isinstance(invalid, Conflict)
            if stale:
                saved_version = request.form.get('version', '')
    kind = classification_details(record)['type']
    streams = db.session.scalars(db.select(IncomeStream).order_by(IncomeStream.name, IncomeStream.id)).all()
    shifts = db.session.scalars(db.select(IncomeShift).order_by(IncomeShift.starts_at.desc()).limit(200)).all()
    if record.expense and record.expense.shift_id and record.expense.shift_id not in {shift.id for shift in shifts}:
        linked = db.session.get(IncomeShift, record.expense.shift_id)
        if linked:
            shifts.insert(0, linked)
    return render(status=status, error=error, record=record, kind=kind, step=step, values=values,
        version=saved_version, stale=stale, suggestions=payment_suggestions(record), streams=streams, stream_names={stream.id: stream.name for stream in streams}, shifts=shifts, shift_label=shift_label,
        treatments=TAX_TREATMENTS, income_types=INCOME_TYPES,
        choices={key: choice for key, choice in CLASSIFICATION_TYPES.items() if record.direction in choice['directions']},
        income_complete=check_income(record, {}) if kind == 'income' else True,
        expense_current=expense_current(record.expense) if record.expense else True,
        back_url=back_url(context), back_label='Back to Home' if context.get('return_to') == 'home' else 'Back to transactions',
        step_url=lambda value: url_for('guided_review.payment', **key, **context, step=value))
