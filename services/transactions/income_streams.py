"""Owner-created stream names and types; a stream never infers tax deductions."""
import re
from decimal import Decimal

from werkzeug.exceptions import BadRequest
from services.validation import optional_text, uid
from services.transactions.income_form import parse_amount

STREAM_KINDS = {'self_employed': 'Self employed', 'employed': 'Employed', 'cis': 'CIS'}

FORECAST_PERIODS = {'weekly': 'Weekly', 'monthly': 'Monthly', 'yearly': 'Yearly'}
FORECAST_CURRENCIES = ('GBP', 'EUR', 'USD')


def stream_id(value):
    try:
        return uid(value)
    except ValueError:
        raise BadRequest('Choose a valid income stream.') from None


def stream_fields(form):
    if set(form) - {'csrf_token', 'action', 'stream_id', 'name', 'kind', 'expected_gross', 'expected_gross_period', 'expected_gross_currency', 'unpaid_holiday', 'unpaid_holiday_unit'} or any(len(form.getlist(key)) != 1 for key in form):
        raise BadRequest('Supply each form field once.')
    action = form.get('action', '')
    if action not in ('create', 'update', 'archive', 'restore'):
        raise BadRequest('Choose a valid action.')
    if action in ('archive', 'restore'):
        return action, None, None, {}
    try:
        name = optional_text(form.get('name'), field='name', maximum=200)
    except ValueError as invalid:
        raise BadRequest(str(invalid)) from None
    if not name:
        raise BadRequest('Enter a name for the income stream.')
    kind = form.get('kind')
    if kind not in STREAM_KINDS:
        raise BadRequest('Choose Self employed, Employed or CIS.')
    period = form.get('expected_gross_period')
    currency = form.get('expected_gross_currency')
    if period not in FORECAST_PERIODS:
        raise BadRequest('Choose weekly, monthly or yearly for expected income.')
    if currency not in FORECAST_CURRENCIES:
        raise BadRequest('Choose a supported currency for expected income.')
    gross = parse_amount(form.get('expected_gross', ''), currency)
    if gross is None or gross > 2**63 - 1:
        raise BadRequest('Enter expected gross income, including zero if none is expected.')
    unit = form.get('unpaid_holiday_unit', 'weeks')
    if unit not in ('days', 'weeks'):
        raise BadRequest('Choose days or weeks for unpaid holiday.')
    holiday = form.get('unpaid_holiday', '').strip() or '0'
    maximum = 260 if unit == 'days' else 52
    if len(holiday) > 6 or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', holiday) or Decimal(holiday) > maximum:
        raise BadRequest(f'Enter unpaid holiday between 0 and {maximum} {unit}, with at most two decimal places.')
    weeks = Decimal(holiday) / 5 if unit == 'days' else Decimal(holiday)
    return action, name, kind, dict(expected_gross_minor=gross, expected_gross_period=period,
                                    expected_gross_currency=currency, unpaid_holiday_weeks=weeks,
                                    unpaid_holiday_unit=unit)


def holiday_amount(stream):
    """Display the saved value in the unit chosen by the owner."""
    weeks = Decimal(stream.unpaid_holiday_weeks)
    amount = weeks * 5 if stream.unpaid_holiday_unit == 'days' else weeks
    return format(amount, '.2f')


def annual_gross(stream):
    """A full-year planning estimate, never actual income or a tax calculation."""
    if stream.expected_gross_minor is None:
        return None
    full_year = stream.expected_gross_minor * {'weekly': 52, 'monthly': 12, 'yearly': 1}[stream.expected_gross_period]
    unpaid_units = int(Decimal(getattr(stream, 'unpaid_holiday_weeks', 0)) * 10000)
    # Prorate by paid weeks; round half up to the nearest minor unit using integers.
    return (full_year * (520000 - unpaid_units) + 260000) // 520000
