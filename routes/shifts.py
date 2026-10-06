"""Create, edit and review per-stream shifts, separately from bank payments."""
import hmac
from uuid import uuid4

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, NotFound

from models import ExpenseDeduction, IncomeShift, IncomeStream, MileageEntry
from routes.dashboard import database_error, format_amount
from routes.helpers import query_values
from services.database.connection import db
from services.database.session import test_data_active
from services.transactions.income_form import display_amount
from services.transactions.shifts import FIELDS, LOCAL_TIME, shift_fields, shift_label, shift_version
from services.validation import uid
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data

shifts = Blueprint('shifts', __name__, url_prefix='/dashboard/income-streams')
shifts.before_request(require_dashboard_login)
shifts.before_request(activate_test_data)
shifts.register_error_handler(SQLAlchemyError, database_error)


@shifts.route('/<uuid:stream_id>/shifts', methods=['GET', 'POST'])
def page(stream_id):
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))
    stream_id = str(stream_id)
    options = query_values({'edit', 'add'})
    if options.get('add') not in (None, '1'):
        raise BadRequest('Choose a valid add-work action.')
    stream = db.session.get(IncomeStream, stream_id)
    if stream is None:
        raise NotFound('Income stream not found.')
    selected, error, values = None, None, dict(payment_mode='total', unpaid_break_minutes='0')
    try:
        if options.get('edit'):
            selected = db.session.get(IncomeShift, uid(options['edit']))
            if selected is None or selected.income_stream_id != stream.id:
                raise ValueError('Shift not found in this stream.')
        if request.method == 'POST':
            values = request.form.to_dict()
            if set(request.form) - FIELDS or any(len(request.form.getlist(key)) != 1 for key in request.form):
                raise ValueError('Supply each shift field once.')
            action = request.form.get('action')
            if action not in ('create', 'update', 'delete'):
                raise ValueError('Choose a valid shift action.')
            db.session.execute(db.select(db.func.pg_advisory_xact_lock(6075157141257144400 + int(test_data_active()))))
            stream = db.session.scalar(db.select(IncomeStream).where(IncomeStream.id == stream_id).with_for_update().execution_options(populate_existing=True))
            if stream.archived:
                raise ValueError('Restore this stream before changing its shifts.')
            identity = uid(request.form.get('shift_id', ''))
            selected = db.session.scalar(db.select(IncomeShift).where(IncomeShift.id == identity).with_for_update().execution_options(populate_existing=True))
            if selected is not None and selected.income_stream_id != stream.id:
                raise ValueError('Shift not found in this stream.')
            if action == 'create':
                if request.form.get('income_mode') != stream.income_mode:
                    raise ValueError('The income pattern changed. Reload before adding work.')
                if selected is not None:
                    raise ValueError('This shift was already saved. Reload before adding another.')
                selected = IncomeShift(id=identity, income_stream_id=stream.id,
                                       is_overtime=stream.income_mode != 'shifts')
                db.session.add(selected)
            else:
                if selected is None:
                    raise ValueError('Shift not found in this stream.')
                if not hmac.compare_digest(request.form.get('version', '').encode(), shift_version(selected).encode()):
                    raise ValueError('This shift changed. Reload before saving.')
            if action == 'delete':
                linked = db.session.scalar(db.select(ExpenseDeduction.shift_id).where(ExpenseDeduction.shift_id == identity).limit(1))
                linked = linked or db.session.scalar(db.select(MileageEntry.shift_id).where(MileageEntry.shift_id == identity).limit(1))
                if linked:
                    raise ValueError('Unlink this shift’s expense and mileage deductions before removing it.')
                db.session.delete(selected)
            else:
                fields = shift_fields(request.form, stream)
                for field, value in fields.items():
                    setattr(selected, field, value)
            db.session.commit()
            return redirect(url_for('shifts.page', stream_id=stream.id), code=303)
    except (ValueError, BadRequest) as invalid:
        db.session.rollback()
        error = invalid.description if isinstance(invalid, BadRequest) else str(invalid)
        # A failed new shift must still be submitted as new on the next attempt.
        if request.form.get('action') == 'create' or (selected is not None and selected.income_stream_id != stream.id):
            selected = None
    if selected and request.method == 'GET':
        values = dict(starts_at=selected.starts_at.astimezone(LOCAL_TIME).strftime('%Y-%m-%dT%H:%M'),
                      ends_at=selected.ends_at.astimezone(LOCAL_TIME).strftime('%Y-%m-%dT%H:%M'),
                      unpaid_break_minutes=selected.unpaid_break_minutes, payment_mode=selected.payment_mode,
                      hourly_rate=display_amount(selected.hourly_rate_minor, selected.currency),
                      total_payment=display_amount(selected.gross_minor, selected.currency) if selected.payment_mode == 'total' else '',
                      notes=selected.notes)
    entries = db.session.scalars(db.select(IncomeShift).where(IncomeShift.income_stream_id == stream.id).order_by(
        IncomeShift.starts_at.desc(), IncomeShift.id)).all()
    return render_template('shifts.html', stream=stream, selected=selected, values=values, entries=entries, error=error,
        identity=selected.id if selected else values.get('shift_id') or str(uuid4()), version=shift_version(selected) if selected else '',
        format_amount=format_amount, shift_label=shift_label, total=sum(entry.gross_minor for entry in entries if stream.income_mode == 'shifts' or entry.is_overtime),
        currency=stream.expected_gross_currency or 'GBP', current_user=current_user), 400 if error else 200
