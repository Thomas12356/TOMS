"""Owner-only private supporting documents; never infer deduction eligibility."""
from io import BytesIO
from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import BadRequest, Conflict, RequestEntityTooLarge
from models import EvidenceDocument
from routes.helpers import query_values
from services import evidence as storage
from services.database.connection import db
from services.error_logging import log_failure
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data

evidence = Blueprint('evidence', __name__, url_prefix='/dashboard/evidence')


@evidence.before_request
def upload_limit():
    if request.endpoint == 'evidence.attachments' and request.method == 'POST':
        request.max_content_length = storage.MAX_FILE + 65536
        request.max_form_parts = 10


evidence.before_request(require_dashboard_login)


@evidence.before_request
def owner():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


evidence.before_request(activate_test_data)


def view(**values):
    return render_template('evidence.html', current_user=current_user, **values)


@evidence.errorhandler(SQLAlchemyError)
def unavailable(error):
    db.session.rollback()
    log_failure('evidence.database', error)
    return view(error='Supporting documents are unavailable. Check PostgreSQL and migrations.', documents=[]), 503


@evidence.errorhandler(RequestEntityTooLarge)
def too_large(error):
    return view(documents=[], error='Choose one file up to 10 MiB with notes up to 1,000 characters.'), 413


def check(fields):
    query_values(set())
    if set(request.form) - fields - {'csrf_token'} or any(len(request.form.getlist(key)) != 1 for key in request.form):
        raise BadRequest('Supply each document field once.')


def record_key(doc):
    if doc.kind == 'transaction':
        return ':'.join((doc.account_uid, doc.category_uid, doc.feed_item_uid))
    return doc.mileage_id


@evidence.get('')
def index():
    values = query_values({'page'})
    try:
        page = int(values.get('page', '1'))
        if not 1 <= page <= 100000:
            raise ValueError
    except ValueError:
        raise BadRequest('Choose a valid document page.') from None
    rows = db.session.scalars(db.select(EvidenceDocument).options(joinedload(EvidenceDocument.asset)).order_by(
        EvidenceDocument.created_at.desc(), EvidenceDocument.id).offset((page-1)*40).limit(41)).all()
    return view(documents=rows[:40], page=page, more=len(rows)>40, record_key=record_key)


@evidence.route('/attach/<kind>/<key>', methods=['GET', 'POST'])
def attachments(kind, key):
    query_values(set())
    _, fields, label = storage.target(kind, key)
    error, status = None, 200
    if request.method == 'POST':
        try:
            check({'notes'})
            if set(request.files) != {'file'} or len(request.files.getlist('file')) != 1:
                raise BadRequest('Choose one supporting file.')
            _, created = storage.upload(kind, key, request.files['file'], request.form.get('notes'))
            db.session.commit()
            flash('Document attached.' if created else 'This file is already attached.', 'evidence')
            return redirect(url_for('.attachments', kind=kind, key=key), code=303)
        except (ValueError, BadRequest) as failure:
            db.session.rollback()
            error = failure.description if isinstance(failure, BadRequest) else str(failure)
            status = 400
    return view(documents=storage.documents(kind, fields), kind=kind, key=key, label=label,
        error=error, record_key=record_key), status


@evidence.post('/<uuid:identity>/archive')
def archive(identity):
    try:
        check({'version', 'confirm'})
        if request.files:
            raise BadRequest('Unexpected file.')
        doc = storage.archive(identity, request.form)
        key, kind = record_key(doc), doc.kind
        db.session.commit()
        return redirect(url_for('.attachments', kind=kind, key=key) if key else url_for('.index'), code=303)
    except (ValueError, BadRequest, Conflict) as failure:
        db.session.rollback()
        return view(documents=[], error=failure.description if isinstance(failure, (BadRequest, Conflict)) else str(failure)), 409 if isinstance(failure, Conflict) else 400


@evidence.get('/<uuid:identity>/download')
def download(identity):
    query_values(set())
    doc = storage.document(identity)
    return send_file(BytesIO(doc.asset.data), as_attachment=True, download_name=doc.name,
        mimetype='application/octet-stream', conditional=False, max_age=0)


@evidence.get('/<uuid:identity>/preview')
def preview(identity):
    query_values(set())
    doc = storage.document(identity)
    if doc.asset.preview is None:
        from werkzeug.exceptions import NotFound
        raise NotFound('No image preview for this document. Download the original.')
    return send_file(BytesIO(doc.asset.preview), mimetype='image/jpeg', conditional=False, max_age=0)
