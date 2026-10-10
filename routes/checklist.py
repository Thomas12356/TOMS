"""Owner checklist: read saved data without starting bank or rule checks."""
from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

from services.database.connection import db
from services.error_logging import log_failure
from services.tax.checklist import guided_checklist
from services.tax.reserves import current_plan
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data
from routes.helpers import query_values

checklist = Blueprint('checklist', __name__, url_prefix='/dashboard/checklist')
checklist.before_request(require_dashboard_login)


@checklist.before_request
def owner_only():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


checklist.before_request(activate_test_data)


@checklist.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure('checklist.database', error)
    return render_template('checklist.html', checklist=None,
                           error='The checklist is unavailable. Check PostgreSQL and database migrations.', current_user=current_user), 503


@checklist.errorhandler(BadRequest)
def bad_request(error):
    return render_template('checklist.html', checklist=None, error=error.description, current_user=current_user), 400


@checklist.get('')
def page():
    query_values(set())
    result = guided_checklist(current_plan(refresh_rules=False))
    return render_template('checklist.html', checklist=result, error=None, current_user=current_user)
