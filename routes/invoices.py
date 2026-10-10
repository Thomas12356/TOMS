"""Owner-only invoice tracking. All writes are explicit CSRF-protected forms."""
from datetime import datetime, timedelta
from uuid import uuid4

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from models import IncomeStream, Invoice, InvoiceItem
from routes.dashboard import format_amount, requested_page
from routes.helpers import query_values
from services.database.connection import db
from services.error_logging import log_failure
from services.invoices import workflow as work
from services.transactions.income_form import parse_amount, display_amount
from services.transactions.shifts import shift_label
from services.validation import uid, optional_text
from services.web.activity import record_action
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data

invoices = Blueprint('invoices', __name__, url_prefix='/dashboard/invoices')
invoices.before_request(require_dashboard_login)


@invoices.before_request
def owner_only():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


invoices.before_request(activate_test_data)


@invoices.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure('invoices.database', error)
    return render_template('invoices.html', page=None, rows=[], error='Invoices are unavailable. Check PostgreSQL and run flask db-upgrade.',
                           current_user=current_user), 503


def check_form(allowed, multiple=()):
    if set(request.form) - set(allowed) or any(len(request.form.getlist(key)) != 1 for key in request.form if key not in multiple):
        raise BadRequest('Supply each invoice field once.')


@invoices.get('')
def page():
    options = query_values({'page'})
    results = db.paginate(work.invoice_query().order_by(Invoice.created_at.desc(), Invoice.id),
                          page=requested_page(options), per_page=30, error_out=False)
    rows = [dict(invoice=invoice, summary=work.settlement(invoice)) for invoice in results.items]
    return render_template('invoices.html', page=results, rows=rows, error=None,
                           format_amount=format_amount, current_user=current_user)


@invoices.route('/new', methods=['GET', 'POST'])
@invoices.route('/<uuid:identity>/edit', methods=['GET', 'POST'])
def edit(identity=None):
    options = query_values({'stream'} if identity is None else set())
    existing = work.get_invoice(identity) if identity else None
    if existing and existing.state != 'draft':
        return redirect(url_for('invoices.detail', identity=identity))
    values = dict(client_name='', client_address='', reference='', issue_date=work.today().isoformat(),
                  due_date=(work.today() + timedelta(days=30)).isoformat(),
                  **dict.fromkeys(work.BUSINESS_FIELDS, ''))
    selected = []
    if existing:
        values.update({key: getattr(existing, key) for key in values})
        selected = [item.shift_id for item in existing.items]
    stream_id = existing.income_stream_id if existing else options.get('stream')
    error, error_status = None, 400
    if request.method == 'POST':
        check_form({'csrf_token','invoice_id','version','stream_id','client_name','client_address','reference',
                    'issue_date','due_date','shift_id'} | set(work.BUSINESS_FIELDS), multiple={'shift_id'})
        values.update({field: request.form.get(field, '') for field in values})
        selected = request.form.getlist('shift_id')
        stream_id = request.form.get('stream_id', '')
        try:
            if existing and request.form.get('invoice_id') != existing.id:
                raise ValueError('Choose the draft you opened.')
            invoice = work.save_draft(str(identity) if identity else request.form.get('invoice_id', ''), stream_id,
                request.form, selected, request.form.get('version', '') if existing else None)
            record_action('invoice.update' if existing else 'invoice.create', invoice.id)
            db.session.commit()
            return redirect(url_for('invoices.detail', identity=invoice.id), code=303)
        except (ValueError, BadRequest, Conflict) as invalid:
            db.session.rollback()
            error = invalid.description if isinstance(invalid, (BadRequest, Conflict)) else str(invalid)
            error_status = 409 if isinstance(invalid, Conflict) else 400
            existing = work.get_invoice(identity) if identity else None
    stream = None
    if stream_id:
        try:
            stream = db.session.get(IncomeStream, uid(str(stream_id)))
        except ValueError:
            error = error or 'Choose an income stream.'
        if stream is None and request.method == 'GET':
            raise NotFound('Income stream not found.')
    streams = db.session.scalars(db.select(IncomeStream).where(IncomeStream.archived.is_(False)).order_by(IncomeStream.name)).all()
    shifts = work.available_shifts(stream.id, existing.id if existing else None) if stream else []
    return render_template('invoice_edit.html', invoice=existing, stream=stream, streams=streams, shifts=shifts,
        selected=selected, values=values, error=error,
        form_revision=request.form.get('version', '') if error and error_status == 409 else (existing.revision if existing else ''),
        identity=existing.id if existing else request.form.get('invoice_id') or str(uuid4()),
        format_amount=format_amount, shift_label=shift_label, current_user=current_user), error_status if error else 200


@invoices.route('/<uuid:identity>', methods=['GET', 'POST'])
def detail(identity):
    options = query_values({'payment_search'})
    try:
        search = optional_text(options.get('payment_search'), field='payment search', maximum=100)
    except ValueError as invalid:
        raise BadRequest(str(invalid)) from None
    invoice = work.get_invoice(identity)
    error, error_status = None, 400
    if request.method == 'POST':
        action = request.form.get('action')
        allowed = {'csrf_token','action','version'}
        if action == 'allocate':
            allowed |= {'payment','cash','tax','ni'}
        elif action == 'remove_payment':
            allowed |= {'payment_id'}
        elif action == 'cancel':
            allowed |= {'confirm_cancel'}
        elif action == 'sent':
            allowed |= {'sent_at', 'method', 'recipient', 'notes'}
        elif action != 'issue':
            raise BadRequest('Choose a valid invoice action.')
        check_form(allowed)
        try:
            invoice = work.get_invoice(identity, request.form.get('version', ''))
            if action == 'issue':
                work.issue(invoice)
            elif action == 'cancel':
                if request.form.get('confirm_cancel') != '1':
                    raise ValueError('Confirm that you want to cancel this invoice.')
                work.cancel(invoice)
            elif action == 'allocate':
                parts = request.form.get('payment', '').split('|')
                if len(parts) != 3:
                    raise ValueError('Choose an incoming payment.')
                key = tuple(uid(part) for part in parts)
                amounts = [parse_amount(request.form.get(field, ''), invoice.currency) or 0 for field in ('cash','tax','ni')]
                work.allocate(invoice, key, *amounts)
            elif action == 'sent':
                work.record_delivery(invoice, request.form)
            else:
                work.remove_payment(invoice, uid(request.form.get('payment_id', '')))
            record_action('invoice.' + action, invoice.id)
            db.session.commit()
            return redirect(url_for('invoices.detail', identity=invoice.id), code=303)
        except (ValueError, BadRequest, Conflict) as invalid:
            db.session.rollback()
            error = invalid.description if isinstance(invalid, (BadRequest, Conflict)) else str(invalid)
            error_status = 409 if isinstance(invalid, Conflict) else 400
            invoice = work.get_invoice(identity)
    summary = work.settlement(invoice)
    receipts = work.receipt_choices(invoice, search) if invoice.state == 'issued' and summary['outstanding'] else []
    payments = [dict(payment=payment, current=work.payment_current(payment, invoice) if payment.active else False)
                for payment in sorted(invoice.payments, key=lambda row: (row.created_at, row.id), reverse=True)]
    return render_template('invoice.html', invoice=invoice, summary=summary, receipts=receipts, payments=payments,
        deliveries=sorted(invoice.deliveries, key=lambda row: (row.sent_at, row.recorded_at, row.id), reverse=True),
        delivery_methods=work.DELIVERY_METHODS, local_time=work.LOCAL_TIME,
        sent_now=datetime.now(work.LOCAL_TIME).strftime('%Y-%m-%dT%H:%M'),
        error=error, values=request.form if error else {},
        form_revision=request.form.get('version', '') if error and error_status == 409 else invoice.revision, format_amount=format_amount, display_amount=display_amount,
        current_user=current_user), error_status if error else 200


@invoices.get('/<uuid:identity>/print')
def print_invoice(identity):
    query_values(set())
    invoice = work.get_invoice(identity)
    return render_template('invoice_print.html', invoice=invoice, format_amount=format_amount,
                           preview=invoice.state == 'draft', sample=work.test_data_active())


def shift_invoice_links(stream_id):
    """One lookup for shift-page badges and links, including draft reservations."""
    return dict(db.session.execute(db.select(InvoiceItem.shift_id, InvoiceItem.invoice_id).join(Invoice)
        .where(Invoice.income_stream_id == stream_id, InvoiceItem.active.is_(True))).all())
