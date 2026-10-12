"""Owner-only evidence download; no bank calls or rule refreshes."""
from flask import Blueprint, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

from routes.helpers import query_values
from services.error_logging import log_failure
from services.tax.export import export_session, package
from services.tax.rules import reviewed_rules
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data

tax_export = Blueprint('tax_export', __name__, url_prefix='/dashboard/tax-export')
tax_export.before_request(require_dashboard_login)


@tax_export.before_request
def owner_only():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


tax_export.before_request(activate_test_data)


@tax_export.errorhandler(SQLAlchemyError)
def database_error(error):
    log_failure('tax_export.database', error)
    return render_template('tax_export.html', tax_year=reviewed_rules()['tax_year'],
        error='The export is unavailable. Check PostgreSQL and database migrations.', current_user=current_user), 503


@tax_export.errorhandler(BadRequest)
def bad_request(error):
    return render_template('tax_export.html', tax_year=reviewed_rules()['tax_year'],
        error=error.description, current_user=current_user), 400


@tax_export.get('')
def page():
    query_values(set())
    return render_template('tax_export.html', tax_year=reviewed_rules()['tax_year'], error=None, current_user=current_user)


@tax_export.get('/download')
def download():
    values = query_values({'documents'})
    if values.get('documents', '1') != '1':
        raise BadRequest('Choose documents=1 to include supporting files.')
    with export_session() as session:
        options = {'include_documents': True} if 'documents' in values else {}
        output, manifest = package(session, include_records=False, **options)
    prefix = 'TOMS-SAMPLE' if manifest['sample_data'] else 'TOMS'
    try:
        output.seek(0, 2)
        size = output.tell()
        output.seek(0)
        response = send_file(output, mimetype='application/zip', as_attachment=True,
            download_name=f"{prefix}-tax-{manifest['tax_year']}.zip", max_age=0)
        response.content_length = size
        response.call_on_close(output.close)
        return response
    except BaseException:
        output.close()
        raise
