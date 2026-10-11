"""Reuse existing validators while keeping payment review a single journey."""
from datetime import datetime, timezone

from werkzeug.exceptions import BadRequest
from werkzeug.datastructures import MultiDict

from models import ExpenseDeduction, IncomeStream, IncomeShift, MileageEntry
from services.database.connection import db
from services.tax.deduction_forms import expense_fields
from services.transactions.classification import classification_details, save_classification
from services.transactions.income import save_income
from services.transactions.income_form import FIELDS, form_values, display_amount
from services.transactions.review import record_version, review_version
from services.validation import uid, optional_text
from services.web.activity import record_action


def version(payment):
    return record_version(dict(payment=review_version(payment), source_updated_at=payment.source_updated_at,
        expense=payment.expense.as_dict() if payment.expense else None))


def initial_values(payment):
    details = classification_details(payment)
    values = dict(type=details['type'], notes=details['notes'] or '', tax_treatment='unknown',
                  income_type='other', category='general', tax_status='tracking')
    income = payment.income
    if income:
        values.update(income_stream_id=income.income_stream_id or '', income_type=income.income_type,
            tax_treatment=income.tax_treatment, source_name=income.source_name or '', adjustment_notes=income.adjustment_notes or '')
        if income.recorded_currency == payment.currency:
            for field in ('gross', 'tax_deducted', 'ni_deducted', 'adjustment'):
                values[field] = display_amount(getattr(income, field + '_minor'), payment.currency)
    expense = payment.expense
    if expense:
        values.update(stream_id=expense.income_stream_id,
            amount=display_amount(expense.amount_minor, payment.currency) if expense.recorded_currency == payment.currency else '',
            purpose=expense.purpose, category=expense.category, vehicle_key=expense.vehicle_key,
            tax_status='deduction' if expense.is_tax_deductible else 'tracking', shift_id=expense.shift_id or '')
    return values


def chosen_stream(value, existing_id=None):
    try:
        identity = uid(value)
    except ValueError:
        raise BadRequest('Choose an income stream, or confirm this income is non-taxable.') from None
    stream = db.session.scalar(db.select(IncomeStream).where(IncomeStream.id == identity).with_for_update()
                               .execution_options(populate_existing=True))
    if stream is None or (stream.archived and stream.id != existing_id):
        raise BadRequest('Choose an active income stream.')
    return stream


def save_details(payment, form):
    kind = classification_details(payment)['type']
    if kind == 'income':
        identity = form.get('income_stream_id', '')
        existing = payment.income.income_stream_id if payment.income else None
        stream = chosen_stream(identity, existing) if identity else None
        if stream is None and form.get('tax_treatment') != 'non_taxable':
            raise BadRequest('Choose an income stream for taxable receipts.')
        income_type = ('employment' if stream.kind == 'employed' else 'other') if stream else form.get('income_type', 'other')
        fields = form_values(MultiDict((key, value) for key, value in form.items() if key in FIELDS),
                             payment.currency, income_type=income_type)
        fields['income_stream_id'] = stream.id if stream else None
        save_income(payment, fields)
        # save_income preserves an existing stream for API callers; an explicit
        # non-taxable browser selection may deliberately remove that association.
        payment.income.income_stream_id = fields['income_stream_id']
        record_action('income.save', payment.feed_item_uid)
    elif kind == 'expense':
        existing = payment.expense.income_stream_id if payment.expense else None
        stream = chosen_stream(form.get('stream_id', ''), existing)
        fields = expense_fields(form, payment)
        shift = None
        if form.get('shift_id'):
            try:
                shift = db.session.get(IncomeShift, uid(form['shift_id']))
            except ValueError:
                raise BadRequest('Choose a valid work shift.') from None
            if shift is None or shift.income_stream_id != stream.id:
                raise BadRequest('Choose a shift belonging to this income stream.')
        fields['shift_id'] = shift.id if shift else None
        if fields['is_tax_deductible'] and fields['category'] == 'vehicle_running' and db.session.scalar(
                db.select(MileageEntry.id).where(MileageEntry.vehicle_key == fields['vehicle_key']).limit(1)):
            raise BadRequest('Mileage already covers running costs for this vehicle. Parking and tolls may be recorded separately.')
        if payment.expense is None:
            payment.expense = ExpenseDeduction(income_stream_id=stream.id, **fields)
        else:
            payment.expense.income_stream_id = stream.id
            for key, value in fields.items():
                setattr(payment.expense, key, value)
        payment.confirmed_at = None
        record_action('deduction.save_expense', payment.feed_item_uid)
    else:
        raise BadRequest('This classification has no income or expense details.')


def apply_action(payment, form):
    action = form.get('action')
    if action == 'classify':
        save_classification(payment, form.get('type', ''), optional_text(form.get('notes'), field='notes', maximum=2000))
        record_action('classification.save', payment.feed_item_uid)
        return 'details' if form.get('type') in ('income', 'expense') else 'check'
    if action == 'details':
        save_details(payment, form)
        return 'check'
    if action == 'skip':
        # Skipping records payment review, not a claim that tax details are complete.
        return 'check'
    if action == 'remove_income':
        if payment.income:
            db.session.delete(payment.income)
            payment.income = None
        payment.confirmed_at = None
        record_action('income.delete', payment.feed_item_uid)
        return 'classification'
    if action == 'remove_expense':
        if payment.expense:
            db.session.delete(payment.expense)
            payment.expense = None
        payment.confirmed_at = None
        record_action('deduction.delete_expense', payment.feed_item_uid)
        return 'classification'
    if action == 'confirm':
        if payment.confirmed_at is None:
            payment.confirmed_at = datetime.now(timezone.utc)
            record_action('confirm', payment.feed_item_uid)
        return 'done'
    raise BadRequest('Choose a valid review action.')


FORM_FIELDS = {
    'preview_classification': {'suggestion_version'}, 'preview_source': {'suggestion_version'},
    'classify': {'type', 'notes'}, 'details': set(FIELDS) | {'stream_id', 'amount', 'purpose', 'category',
        'vehicle_key', 'eligible', 'shift_id', 'tax_status'},
    'skip': set(), 'confirm': set(), 'remove_income': set(), 'remove_expense': set(),
}
