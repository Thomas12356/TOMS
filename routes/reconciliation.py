"""Owner read-only reconciliation, with explicit invoice forms for changes."""
from flask import Blueprint, redirect, render_template, request, url_for, current_app, flash
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, Conflict
from itsdangerous import URLSafeTimedSerializer, BadSignature
from services.web.signing import ClockTolerantSigner

from services.web.presentation import format_amount
from routes.helpers import query_values
from services.error_logging import log_failure
from services.invoices.reconciliation import reconcile
from services.tax.export import export_session
from services.tax.readiness import payment_link
from services.transactions.shifts import shift_label
from services.validation import uid
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data
from services.database.connection import db
from services.database.session import test_data_active
from services.invoices import matching, workflow as work
from services.transactions.income_form import parse_amount, display_amount
from services.validation import optional_text
from services.web.activity import record_action

reconciliation = Blueprint('reconciliation', __name__, url_prefix='/dashboard/reconciliation')
reconciliation.before_request(require_dashboard_login)


@reconciliation.before_request
def owner_only():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


reconciliation.before_request(activate_test_data)


def render(result=None, error=None, status=200):
    return render_template('reconciliation.html', result=result, error=error, current_user=current_user,
        format_amount=format_amount, payment_link=payment_link, shift_label=shift_label), status


@reconciliation.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure('reconciliation.database', error)
    return render(error='Reconciliation is unavailable. Check PostgreSQL and database migrations.', status=503)


@reconciliation.errorhandler(BadRequest)
def bad_request(error):
    return render(error=error.description, status=400)


@reconciliation.get('')
def page():
    options = query_values({'stream'})
    try:
        stream_id = uid(options['stream']) if options.get('stream') else None
    except ValueError as error:
        raise BadRequest(str(error)) from None
    with export_session() as session:
        result = reconcile(session, stream_id=stream_id)
        # Keep ORM values attached while rendering the evidence and action links.
        if stream_id and stream_id not in {stream.id for stream in result['streams']}:
            raise BadRequest('Choose a stream in the supported tax year.')
        return render(result)


@reconciliation.route('/payment/<account_uid>/<category_uid>/<feed_item_uid>', methods=['GET', 'POST'])
def match_payment(account_uid, category_uid, feed_item_uid):
    options = query_values({'q'})
    try:
        key = tuple(uid(value) for value in (account_uid, category_uid, feed_item_uid))
        search = optional_text(options.get('q'), field='invoice search', maximum=100)
    except ValueError as invalid:
        raise BadRequest(str(invalid)) from None
    signer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='receipt-match-preview-v1', signer=ClockTolerantSigner)
    error, status, preview = None, 200, None
    if request.method == 'POST':
        action = request.form.get('action')
        allowed = {'csrf_token', 'action', 'invoice', 'cash', 'tax', 'ni'} if action == 'preview' else {'csrf_token', 'action', 'preview'}
        if action not in ('preview', 'confirm') or set(request.form) - allowed or any(len(request.form.getlist(field)) != 1 for field in request.form):
            raise BadRequest('Choose one matching action and supply each field once.')
        try:
            if action == 'confirm':
                try:
                    saved = signer.loads(request.form.get('preview', ''), max_age=900)
                except BadSignature:
                    raise Conflict('This preview expired or is invalid. Preview the match again.') from None
                if saved['key'] != list(key) or saved['sample'] != test_data_active():
                    raise Conflict('Preview this payment in the current data mode before confirming.')
                invoice = work.get_invoice(saved['invoice'], str(saved['revision']))
                payment = matching.receipt(key, lock=True)
                if work.payment_version(payment) != saved['payment_version']:
                    raise Conflict('This payment changed. Review its details and preview again.')
                if not matching.reviewed_receipt(payment, invoice):
                    raise ValueError('Review this payment’s gross pay and recorded deductions before matching.')
                work.allocate(invoice, key, *saved['amounts'])
                record_action('invoice.allocate', invoice.id)
                db.session.commit()
                flash('Payment matched. Any remaining amount can be matched to another invoice.', 'success')
                return redirect(url_for('reconciliation.match_payment', account_uid=key[0], category_uid=key[1], feed_item_uid=key[2]), code=303)
            payment = matching.receipt(key)
            invoice = work.get_invoice(uid(request.form.get('invoice', '')))
            if not matching.reviewed_receipt(payment, invoice):
                raise ValueError('Review this payment’s source, gross pay and recorded deductions before matching.')
            amounts = [parse_amount(request.form.get(field, ''), invoice.currency) or 0 for field in ('cash', 'tax', 'ni')]
            summary = work.validate_allocation(invoice, payment, *amounts)
            saved = dict(key=list(key), sample=test_data_active(), invoice=invoice.id, revision=invoice.revision,
                payment_version=work.payment_version(payment), amounts=amounts)
            preview = dict(invoice=invoice, amounts=amounts, total=sum(amounts), remaining=summary['outstanding'] - sum(amounts), token=signer.dumps(saved))
        except (ValueError, BadRequest, Conflict) as invalid:
            db.session.rollback()
            error = invalid.description if isinstance(invalid, (BadRequest, Conflict)) else str(invalid)
            status = 409 if isinstance(invalid, Conflict) else 400
    payment = matching.receipt(key)
    try:
        context = matching.choices(payment, search)
    except Conflict as invalid:
        context = dict(source=None, available=None, rows=[], eligible=False)
        error, status = invalid.description, 409
    return render_template('receipt_match.html', payment=payment, context=context, search=search or '',
        preview=preview, error=error, values=request.form if error else {}, format_amount=format_amount,
        display_amount=display_amount, current_user=current_user, payment_link=payment_link,
        payment_date=payment.transaction_time.astimezone(work.LOCAL_TIME).strftime('%d %b %Y')), status
