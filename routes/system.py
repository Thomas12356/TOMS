"""Owner-only, read-only backup, storage and activity dashboard."""
from pathlib import Path

from flask import Blueprint, current_app, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

from models import UserAction
from routes.helpers import query_values
from services.database.connection import db
from services.error_logging import log_failure
from services.system.overview import backup_inventory, database_storage, format_bytes, format_timestamp
from services.web.activity import ACTIONS
from services.web.sessions import require_dashboard_login

system = Blueprint('system', __name__, url_prefix='/dashboard/system')
system.before_request(require_dashboard_login)


@system.get('')
def page():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))
    options = query_values({'page', 'mode'})
    number = options.get('page', '1')
    mode = options.get('mode', 'all')
    if not number.isascii() or not number.isdigit() or len(number) > 6 or not 1 <= int(number) <= 100000:
        raise BadRequest('Choose a valid activity page.')
    if mode not in ('all', 'real', 'test'):
        raise BadRequest('Choose all, real or test activity.')
    error, storage, actions = None, None, None
    try:
        # This page always describes the real installation, including in test mode.
        with db.engine.connect() as connection:
            storage = database_storage(connection)
        query = db.select(UserAction).order_by(UserAction.occurred_at.desc(), UserAction.id.desc())
        if mode != 'all':
            query = query.where(UserAction.sample_data.is_(mode == 'test'))
        actions = db.paginate(query, page=int(number), per_page=30, error_out=False)
    except SQLAlchemyError as failure:
        db.session.rollback()
        log_failure('system.database', failure)
        error = 'System data is unavailable. Check PostgreSQL and run flask db-upgrade.'
    backups = backup_inventory(Path(current_app.instance_path) / 'backups')
    return render_template('system.html', storage=storage, backups=backups, actions=actions,
                           mode=mode, labels=ACTIONS, format_bytes=format_bytes, format_timestamp=format_timestamp, error=error,
                           current_user=current_user), 503 if error else 200
