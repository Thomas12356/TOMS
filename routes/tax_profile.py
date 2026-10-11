"""CSRF-protected annual profile for the browser owner and selected dataset."""
from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, Conflict

from routes.helpers import query_values
from services.database.connection import db
from services.error_logging import log_failure
from services.tax.profile import JURISDICTIONS, current_profile, save_profile
from services.web.activity import record_action
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data

tax_profile = Blueprint('tax_profile', __name__, url_prefix='/dashboard')
tax_profile.before_request(require_dashboard_login)


@tax_profile.before_request
def owner_only():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


tax_profile.before_request(activate_test_data)


@tax_profile.route('/tax-profile', methods=['GET', 'POST'])
def page():
    query_values(set())
    error, status = None, 200
    if request.method == 'POST':
        try:
            save_profile(request.form)
            record_action('tax-profile.save', '2026-27')
            db.session.commit()
            return redirect(url_for('dashboard.tax_estimate'), code=303)
        except (BadRequest, Conflict) as invalid:
            db.session.rollback()
            error, status = invalid.description, invalid.code
    profile = current_profile(db.session)
    # A stale page must be reloaded, never silently granted a fresh version.
    return render_template('tax_profile.html', profile=profile, choices=JURISDICTIONS,
        selected=request.form.get('jurisdiction', profile['jurisdiction']),
        version=request.form.get('version', str(profile['revision'])),
        error=error, stale=status == 409, current_user=current_user), status


@tax_profile.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure('tax-profile.database', error)
    return render_template('tax_profile.html', profile=None,
        error='Tax profile is unavailable. Check PostgreSQL and database migrations.',
        current_user=current_user), 503
