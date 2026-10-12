"""Owner forms for actual statement charges, bills and observed balances."""
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from uuid import uuid4

from flask import Blueprint, redirect, render_template, request, url_for, send_file
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, Conflict

from models import CashCoverage, CashPayment
from services import cash_planning as cash
from services.database.connection import db
from services.database.session import test_data_active
from services.invoices.workflow import lock_writes, LOCAL_TIME
from services.error_logging import log_failure
from services.tax.export import export_session
from services.tax.reserves import current_plan
from services.transactions.income_form import display_amount
from services.validation import uid
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data
from services.web.presentation import format_amount
from services.web.activity import record_action
from routes.helpers import query_values

cash_planning = Blueprint('cash_planning', __name__, url_prefix='/dashboard/cash-planning')
cash_planning.before_request(require_dashboard_login)


@cash_planning.before_request
def owner_only():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


cash_planning.before_request(activate_test_data)


def check_form(fields):
    if set(request.form) - fields - {'csrf_token'} or any(len(request.form.getlist(key)) != 1 for key in request.form):
        raise BadRequest('Supply each cash-planning field once.')


def page_context(error=None):
    with export_session() as saved:
        estimate = current_plan(refresh_rules=False, session=saved)
        target = estimate['received_reserve_minor'] if not estimate['blockers'] and not estimate['received_issues'] else None
        result = cash.snapshot(saved, tax_target=target, tax_year=estimate['tax_year'])
        return render_template('cash_planning.html', result=result, error=error,
            balance_now=datetime.now(LOCAL_TIME).strftime('%Y-%m-%dT%H:%M'), current_user=current_user,
            format_amount=format_amount, display_amount=display_amount, local_time=LOCAL_TIME)


@cash_planning.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure('cash-planning.database', error)
    return render_template('cash_planning.html', result=None, error='Cash planning is unavailable. Check PostgreSQL and migrations.', current_user=current_user), 503


@cash_planning.route('', methods=['GET','POST'])
def page():
    query_values(set())
    error, status = None, 200
    if request.method == 'POST':
        action = request.form.get('action')
        try:
            if action == 'balance':
                check_form({'action','account','version','amount','observed_at'})
                cash.save_balance(request.form)
                record_action('cash.balance', request.form['account'])
            elif action == 'coverage':
                check_form({'action','fingerprint','confirm'})
                if request.form.get('confirm') != '1':
                    raise ValueError('Confirm that all relevant accounts and commitments are included.')
                lock_writes()
                snapshot = cash.snapshot(db.session)
                if snapshot['fingerprint'] != request.form.get('fingerprint'):
                    raise Conflict('Planning records changed. Reload and check them before confirming.')
                coverage = db.session.get(CashCoverage, 1)
                if coverage is None:
                    coverage = CashCoverage(id=1)
                    db.session.add(coverage)
                coverage.fingerprint, coverage.confirmed_at = snapshot['fingerprint'], datetime.now(timezone.utc)
                record_action('cash.coverage')
            else:
                raise BadRequest('Choose a balance or coverage action.')
            db.session.commit()
            return redirect(url_for('cash_planning.page'), code=303)
        except (ValueError, BadRequest, Conflict) as invalid:
            db.session.rollback()
            error = invalid.description if isinstance(invalid,(BadRequest,Conflict)) else str(invalid)
            status = 409 if isinstance(invalid,Conflict) else 400
    return page_context(error), status


FIELDS = {'identity','version','kind','name','currency','amount','due_date','recurrence','end_date','tax_year','tax_component','notes','statement_confirmed','supported_charge_confirmed'}


@cash_planning.route('/new', methods=['GET','POST'])
@cash_planning.route('/<uuid:identity>/edit', methods=['GET','POST'])
def edit(identity=None):
    options = query_values({'kind'} if identity is None else set())
    item = cash.commitment(identity) if identity else None
    values = {field:'' for field in FIELDS}
    values.update(kind=options.get('kind','bill'), currency='GBP', recurrence='once',
        due_date=datetime.now(LOCAL_TIME).date().isoformat(), tax_year='2026-27', tax_component='balancing')
    if item:
        values.update(item.as_dict(), amount=display_amount(item.amount_minor,item.currency))
    error, status = None, 200
    if request.method == 'POST':
        values.update(request.form)
        try:
            check_form(FIELDS)
            if identity and request.form.get('identity') != str(identity):
                raise ValueError('Choose the commitment you opened.')
            item = cash.save_commitment(str(identity) if identity else request.form.get('identity',''), request.form,
                request.form.get('version','') if identity else None)
            record_action('cash.commitment', item.id)
            db.session.commit()
            return redirect(url_for('cash_planning.detail',identity=item.id), code=303)
        except (ValueError, BadRequest, Conflict) as invalid:
            db.session.rollback()
            error = invalid.description if isinstance(invalid,(BadRequest,Conflict)) else str(invalid)
            status = 409 if isinstance(invalid,Conflict) else 400
    return render_template('cash_commitment_edit.html', item=item, values=values, error=error,
        identity=str(identity) if identity else request.form.get('identity') or str(uuid4()),
        revision=request.form.get('version','') if status==409 else item.revision if item else '',
        components=cash.COMPONENTS, recurrences=cash.RECURRENCES, current_user=current_user), status


@cash_planning.route('/<uuid:identity>', methods=['GET','POST'])
def detail(identity):
    query_values(set())
    item = cash.commitment(identity)
    error, status = None, 200
    if request.method == 'POST':
        try:
            action = request.form.get('action')
            allowed = {'action','version','payment_id','occurrence_date','paid_on','amount','reference','tax_credit'} if action=='pay' else {'action','version','payment_id'} if action=='void' else {'action','version','confirm'}
            check_form(allowed)
            item = cash.commitment(identity,request.form.get('version',''))
            if action=='pay':
                cash.record_payment(item,request.form)
            elif action=='void':
                payment = db.session.get(CashPayment,uid(request.form.get('payment_id','')))
                if payment is None or payment.commitment_id != item.id or not payment.active:
                    raise ValueError('Choose an active payment on this commitment.')
                payment.active, payment.changed_at = False, datetime.now(timezone.utc)
                item.revision += 1
            elif action=='cancel' and request.form.get('confirm')=='1':
                if not item.active:
                    raise Conflict('This schedule was already cancelled.')
                item.active = False
                item.revision += 1
            else:
                raise ValueError('Choose an action and confirm any cancellation.')
            record_action('cash.payment' if action in ('pay','void') else 'cash.commitment',item.id)
            db.session.commit()
            return redirect(url_for('cash_planning.detail',identity=item.id),code=303)
        except (ValueError, BadRequest, Conflict) as invalid:
            db.session.rollback()
            item = cash.commitment(identity)
            error = invalid.description if isinstance(invalid,(BadRequest,Conflict)) else str(invalid)
            status = 409 if isinstance(invalid,Conflict) else 400
    today = datetime.now(LOCAL_TIME).date()
    payments = db.session.scalars(db.select(CashPayment).where(CashPayment.commitment_id==item.id)
        .order_by(CashPayment.recorded_at.desc(),CashPayment.id)).all()
    paid = defaultdict(int)
    for payment in payments:
        if payment.active:
            paid[payment.occurrence_date] += payment.amount_minor
    rows = []
    try:
        for due in cash.occurrences(item,today+timedelta(days=30)):
            remaining = None if item.amount_minor is None else max(0,item.amount_minor-paid[due])
            if remaining != 0 and len(rows)<100:
                rows.append(dict(item=item,due=due,remaining=remaining,overdue=due<today))
    except ValueError as invalid:
        error, status = str(invalid), 400
    return render_template('cash_commitment.html', item=item, error=error,
        rows=rows, payments=payments,
        payment_id=str(uuid4()), today=today, values=request.form if error else {},
        revision=request.form.get('version','') if status==409 else item.revision,
        components=cash.COMPONENTS, current_user=current_user, format_amount=format_amount),status


@cash_planning.get('/export')
def export():
    query_values(set())
    from services.cash_export import package
    with export_session() as saved:
        output = package(saved)
    try:
        output.seek(0,2)
        size = output.tell()
        output.seek(0)
        name = 'TOMS-SAMPLE-cash-planning.zip' if test_data_active() else 'TOMS-cash-planning.zip'
        response = send_file(output,mimetype='application/zip',as_attachment=True,download_name=name,max_age=0)
        response.content_length = size
        response.call_on_close(output.close)
        return response
    except BaseException:
        output.close()
        raise
