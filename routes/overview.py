"""Owner landing page; opening it never requests a bank import or rules check."""
from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

from routes.helpers import query_values
from services.error_logging import log_failure
from services.tax.export import export_session
from services.web.overview import saved_overview
from services.web.presentation import format_amount, local_date
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data

overview = Blueprint('overview', __name__, url_prefix='/dashboard/overview')
overview.before_request(require_dashboard_login)


@overview.before_request
def owner_only():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


overview.before_request(activate_test_data)


def render(result=None, error=None, status=200):
    return render_template('overview.html', result=result, error=error, current_user=current_user,
                           format_amount=format_amount, local_date=local_date), status


@overview.errorhandler(SQLAlchemyError)
def database_error(error):
    log_failure('overview.database', error)
    return render(error='The overview is unavailable. Check PostgreSQL and database migrations.', status=503)


@overview.errorhandler(BadRequest)
def bad_request(error):
    return render(error=error.description, status=400)


@overview.get('')
def page():
    query_values(set())
    with export_session() as session:
        return render(saved_overview(session))
