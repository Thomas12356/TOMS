"""Owner read-only reconciliation, with explicit invoice forms for changes."""
from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

from routes.dashboard import format_amount
from routes.helpers import query_values
from services.error_logging import log_failure
from services.invoices.reconciliation import reconcile
from services.tax.export import export_session
from services.tax.readiness import payment_link
from services.transactions.shifts import shift_label
from services.validation import uid
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data

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
