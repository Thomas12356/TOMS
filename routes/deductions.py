"""Owner-managed links to bank expenses and manual business mileage logs."""
import hmac
from datetime import datetime
from uuid import uuid4

from flask import Blueprint, current_app, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.orm import selectinload, load_only
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

from models import ExpenseDeduction, IncomeStream, IncomeShift, MileageEntry, Transaction
from services.database.connection import db
from services.web.activity import record_action
from services.database.session import test_data_active
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data
from services.tax.deduction_forms import expense_fields, expense_version, mileage_fields, mileage_version, payment_key
from services.tax.records import expense_current
from services.tax.mileage import LOCATIONS, VEHICLES, mileage_allowances
from services.tax.mileage_rules import cached_status, reviewed_rules
from services.transactions.classification import classification_details
from services.validation import uid
from routes.helpers import query_values
from routes.dashboard import format_amount, database_error
from services.transactions.income_form import display_amount
from services.transactions.shifts import LOCAL_TIME, journey_defaults, shift_label


deductions = Blueprint('deductions', __name__, url_prefix='/dashboard/deductions')
deductions.before_request(require_dashboard_login)
deductions.before_request(activate_test_data)
deductions.register_error_handler(SQLAlchemyError, database_error)

EXPENSE_FIELDS = {'csrf_token', 'action', 'payment', 'version', 'stream_id', 'amount', 'purpose', 'category', 'vehicle_key', 'eligible', 'shift_id'}
MILEAGE_FIELDS = {'csrf_token', 'action', 'entry_id', 'stream_id', 'journey_date', 'location', 'vehicle_type', 'vehicle_key',
                  'miles', 'purpose', 'start_postcode', 'end_postcode', 'reimbursed', 'eligible', 'shift_id', 'version'}


@deductions.route('', methods=['GET', 'POST'])
def page():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))
    from services.tax.rules import start_rule_check
    start_rule_check(current_app.instance_path)
    options = query_values({'expense', 'type', 'shift'})
    deduction_type = options.get('type', 'expense' if options.get('expense') else '')
    if deduction_type not in ('', 'expense', 'mileage'):
        raise BadRequest('Choose Expense or Mileage.')
    selected, error, values = None, None, {}
    try:
        if options.get('shift'):
            linked_shift = db.session.get(IncomeShift, uid(options['shift']))
            if linked_shift is None:
                raise ValueError('Shift not found.')
            values = dict(shift_id=linked_shift.id, stream_id=linked_shift.income_stream_id,
                          action='save_mileage' if deduction_type == 'mileage' else 'save_expense')
            if deduction_type == 'mileage':
                stream = db.session.get(IncomeStream, linked_shift.income_stream_id)
                values.update(journey_defaults(linked_shift, stream.name))
        if options.get('expense'):
            selected = db.session.get(Transaction, payment_key(options['expense']))
            if selected is None:
                raise ValueError('Expense not found.')
        if request.method == 'POST':
            values = request.form.to_dict()
            action = values.get('action')
            deduction_type = 'mileage' if action in ('save_mileage', 'delete_mileage', 'link_mileage_shift') else 'expense'
            allowed = EXPENSE_FIELDS if action in ('save_expense', 'delete_expense') else MILEAGE_FIELDS
            if action not in ('save_expense', 'delete_expense', 'save_mileage', 'delete_mileage', 'link_mileage_shift') or set(request.form) - allowed or any(len(request.form.getlist(key)) != 1 for key in request.form):
                raise ValueError('Supply valid form fields once.')
            # Serialize method selection and journey writes, including across workers.
            # Demo records use a different key and remain isolated from real records.
            db.session.execute(db.select(db.func.pg_advisory_xact_lock(6075157141257144400 + int(test_data_active()))))
            if action.endswith('expense'):
                key = payment_key(values.get('payment', ''))
                selected = db.session.scalar(db.select(Transaction).where(
                    Transaction.account_uid == key[0], Transaction.category_uid == key[1], Transaction.feed_item_uid == key[2]).with_for_update().execution_options(populate_existing=True))
                if selected is None:
                    raise ValueError('Expense not found.')
                record = selected.expense
                if not hmac.compare_digest(values.get('version', '').encode(), expense_version(selected, record).encode()):
                    raise ValueError('This expense changed. Reload and review it before saving.')
                if action == 'delete_expense':
                    if record:
                        db.session.delete(record)
                else:
                    stream = db.session.get(IncomeStream, uid(values.get('stream_id', '')))
                    if stream is None or stream.archived:
                        raise ValueError('Choose an active income stream.')
                    if classification_details(selected)['type'] != 'expense':
                        raise ValueError('Classify this payment as an expense first.')
                    fields = expense_fields(request.form, selected)
                    fields['shift_id'] = linked_shift_id(request.form, stream)
                    journeys = db.session.scalars(db.select(MileageEntry)).all()
                    if fields['category'] == 'vehicle_running' and any(entry.vehicle_key == fields['vehicle_key'] for entry in journeys):
                        raise ValueError('Mileage already covers running costs for this vehicle. Parking and tolls may be recorded separately.')
                    if record is None:
                        record = ExpenseDeduction(account_uid=key[0], category_uid=key[1], feed_item_uid=key[2])
                        db.session.add(record)
                    record.income_stream_id = stream.id
                    for field, value in fields.items():
                        setattr(record, field, value)
            else:
                entry_id = uid(values.get('entry_id', ''))
                existing = db.session.get(MileageEntry, entry_id)
                if action == 'delete_mileage':
                    if existing:
                        if not hmac.compare_digest(values.get('version', '').encode(), mileage_version(existing).encode()):
                            raise ValueError('This mileage entry changed. Reload before removing it.')
                        db.session.delete(existing)
                elif action == 'link_mileage_shift':
                    if existing is None:
                        raise ValueError('Mileage entry not found.')
                    if not hmac.compare_digest(values.get('version', '').encode(), mileage_version(existing).encode()):
                        raise ValueError('This mileage entry changed. Reload before changing its shift.')
                    existing.shift_id = linked_shift_id(request.form, existing.income_stream)
                else:
                    if existing:
                        raise ValueError('This journey was already saved. Remove it before replacing it.')
                    stream = db.session.get(IncomeStream, uid(values.get('stream_id', '')))
                    if stream is None or stream.archived:
                        raise ValueError('Choose an active income stream.')
                    fields = mileage_fields(request.form, stream)
                    fields['shift_id'] = linked_shift_id(request.form, stream)
                    if cached_status(current_app.instance_path).get('status') == 'needs_review':
                        raise ValueError('Official mileage rules need review before new allowances can be saved.')
                    expenses = db.session.scalars(db.select(ExpenseDeduction).where(
                        ExpenseDeduction.category == 'vehicle_running').options(selectinload(ExpenseDeduction.transaction))).all()
                    if any(record.vehicle_key == fields['vehicle_key'] for record in expenses):
                        raise ValueError('Remove actual running-cost deductions for this vehicle before using simplified mileage.')
                    entry = MileageEntry(id=entry_id, income_stream_id=stream.id, **fields)
                    journeys = db.session.scalars(db.select(MileageEntry)).all()
                    if any(j.vehicle_key == entry.vehicle_key and j.vehicle_type != entry.vehicle_type for j in journeys):
                        raise ValueError('This vehicle already has a different vehicle type in the mileage log.')
                    db.session.add(entry)
            record_action('deduction.' + action, values.get('entry_id', '') if 'mileage' in action else selected.feed_item_uid)
            db.session.commit()
            return redirect(url_for('deductions.page'), code=303)
    except (ValueError, BadRequest) as invalid:
        db.session.rollback()
        error = invalid.description if isinstance(invalid, BadRequest) else str(invalid)
    streams = db.session.scalars(db.select(IncomeStream).order_by(IncomeStream.name, IncomeStream.id)).all()
    expenses = db.session.scalars(db.select(ExpenseDeduction).options(selectinload(ExpenseDeduction.transaction))).all()
    journeys = db.session.scalars(db.select(MileageEntry).options(selectinload(MileageEntry.income_stream)).order_by(MileageEntry.journey_date.desc(), MileageEntry.id)).all()
    payments = db.session.scalars(db.select(Transaction).where(Transaction.direction == 'OUT', Transaction.status == 'SETTLED', Transaction.currency == 'GBP').options(
        load_only(Transaction.transaction_time, Transaction.counterparty_name, Transaction.amount_minor,
                  Transaction.currency, Transaction.direction, Transaction.status, Transaction.source),
        selectinload(Transaction.classification)).order_by(Transaction.transaction_time.desc()).limit(200)).all()
    if selected and selected not in payments:
        payments.insert(0, selected)
    payments = [payment for payment in payments if classification_details(payment)['type'] == 'expense']
    if not selected and values.get('payment'):
        try:
            selected = db.session.get(Transaction, payment_key(values['payment']))
        except ValueError:
            pass
    record = selected.expense if selected else None
    if record and request.method != 'POST':
        values = dict(stream_id=record.income_stream_id, amount=display_amount(record.amount_minor, 'GBP'), purpose=record.purpose,
                      category=record.category, vehicle_key=record.vehicle_key, shift_id=record.shift_id)
    names = {stream.id: stream.name for stream in streams}
    available_shifts = db.session.scalars(db.select(IncomeShift).order_by(IncomeShift.starts_at.desc(), IncomeShift.id)).all()
    return render_template('deductions.html', streams=streams, expenses=expenses, journeys=journeys, payments=payments,
        available_shifts=available_shifts, shift_names={shift.id: shift_label(shift) for shift in available_shifts}, shift_label=shift_label,
        journey_suggestions={shift.id: journey_defaults(shift, names[shift.income_stream_id]) for shift in available_shifts},
        deduction_type=deduction_type, names=names, selected=selected, values=values, error=error,
        entry_id=values.get('entry_id') or str(uuid4()), today=datetime.now(LOCAL_TIME).date(), locations=LOCATIONS, vehicles=VEHICLES,
        rules=reviewed_rules(), status=cached_status(current_app.instance_path), format_amount=format_amount,
        expense_current=expense_current, expense_version=expense_version, mileage_version=mileage_version, allowance=mileage_allowances(journeys),
        current_user=current_user), 400 if error else 200


def linked_shift_id(form, stream):
    """A shift link is optional, but cannot change ownership of a deduction."""
    value = form.get('shift_id', '').strip()
    if not value:
        return None
    shift = db.session.get(IncomeShift, uid(value))
    if shift is None or shift.income_stream_id != stream.id:
        raise ValueError('Choose a shift belonging to this income stream.')
    return shift.id
