"""Owner-created streams, gross forecasts, active dates and unpaid absence."""
import re
from decimal import Decimal
from datetime import date
from types import SimpleNamespace

from werkzeug.exceptions import BadRequest
from services.validation import optional_text, uid
from services.transactions.income_form import parse_amount

STREAM_KINDS = {'self_employed': 'Self employed', 'employed': 'Employed', 'cis': 'CIS'}

FORECAST_PERIODS = {'weekly': 'Weekly', 'monthly': 'Monthly', 'yearly': 'Yearly'}
FORECAST_CURRENCIES = ('GBP', 'EUR', 'USD')
TAX_YEARS = {'2026-27': (date(2026, 4, 6), date(2027, 4, 5))}


def stream_id(value):
    try:
        return uid(value)
    except ValueError:
        raise BadRequest('Choose a valid income stream.') from None


def stream_fields(form):
    if set(form) - {'csrf_token', 'action', 'stream_id', 'name', 'kind', 'expected_gross', 'expected_gross_period', 'expected_gross_currency', 'unpaid_holiday', 'unpaid_holiday_unit', 'forecast_tax_year', 'forecast_starts_on', 'forecast_ends_on', 'income_mode'} or any(len(form.getlist(key)) != 1 for key in form):
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
    mode = form.get('income_mode', 'forecast')
    if mode not in ('forecast', 'shifts'):
        raise BadRequest('Choose Regular forecast or Individual shifts.')
    period = 'yearly' if mode == 'shifts' else form.get('expected_gross_period')
    currency = form.get('expected_gross_currency')
    if period not in FORECAST_PERIODS:
        raise BadRequest('Choose weekly, monthly or yearly for expected income.')
    if currency not in FORECAST_CURRENCIES:
        raise BadRequest('Choose a supported currency for expected income.')
    gross = 0 if mode == 'shifts' else parse_amount(form.get('expected_gross', ''), currency)
    if gross is None or gross > 2**63 - 1:
        raise BadRequest('Enter expected gross income, including zero if none is expected.')
    unit = 'weeks' if mode == 'shifts' else form.get('unpaid_holiday_unit', 'weeks')
    if unit not in ('days', 'weeks'):
        raise BadRequest('Choose days or weeks for unpaid holiday.')
    holiday = '0' if mode == 'shifts' else form.get('unpaid_holiday', '').strip() or '0'
    maximum = 260 if unit == 'days' else 52
    if len(holiday) > 6 or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', holiday) or Decimal(holiday) > maximum:
        raise BadRequest(f'Enter unpaid holiday between 0 and {maximum} {unit}, with at most two decimal places.')
    weeks = Decimal(holiday) / 5 if unit == 'days' else Decimal(holiday)
    year = form.get('forecast_tax_year')
    if year not in TAX_YEARS:
        raise BadRequest('Choose a supported tax year: 2026–27.')
    starts = None if mode == 'shifts' else forecast_date(form.get('forecast_starts_on', ''))
    ends = None if mode == 'shifts' else forecast_date(form.get('forecast_ends_on', ''))
    if starts and ends and starts > ends:
        raise BadRequest('The end date must be on or after the start date.')
    forecast = dict(income_mode=mode, expected_gross_minor=gross, expected_gross_period=period,
                    expected_gross_currency=currency, unpaid_holiday_weeks=weeks, unpaid_holiday_unit=unit,
                    forecast_tax_year=year, forecast_starts_on=starts, forecast_ends_on=ends)
    active, total = active_days(SimpleNamespace(**forecast))
    if int(weeks * 10000) * total > active * 520000:
        raise BadRequest('Unpaid absence cannot exceed the time this stream is active in the selected tax year.')
    return action, name, kind, forecast


def forecast_date(value):
    value = value.strip()
    if not value:
        return None
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise BadRequest('Enter a valid start or end date.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BadRequest('Enter a valid start or end date.') from None


def active_days(stream):
    """Inclusive dates, clipped to the selected UK tax year."""
    first, last = TAX_YEARS[getattr(stream, 'forecast_tax_year', '2026-27')]
    start = max(first, getattr(stream, 'forecast_starts_on', None) or first)
    end = min(last, getattr(stream, 'forecast_ends_on', None) or last)
    return max(0, (end - start).days + 1), (last - first).days + 1


def holiday_amount(stream):
    """Display the saved value in the unit chosen by the owner."""
    weeks = Decimal(stream.unpaid_holiday_weeks)
    amount = weeks * 5 if stream.unpaid_holiday_unit == 'days' else weeks
    return format(amount, '.2f')


def annual_gross(stream):
    """Gross forecast for the active part of the tax year, minus planned absence."""
    if getattr(stream, 'income_mode', 'forecast') == 'shifts':
        return getattr(stream, 'shift_gross_minor', None)
    if stream.expected_gross_minor is None:
        return None
    full_year = stream.expected_gross_minor * {'weekly': 52, 'monthly': 12, 'yearly': 1}[stream.expected_gross_period]
    unpaid_units = int(Decimal(getattr(stream, 'unpaid_holiday_weeks', 0)) * 10000)
    active, total = active_days(stream)
    denominator = total * 520000
    paid_fraction = max(0, active * 520000 - unpaid_units * total)
    # Keep the existing 52-week/12-month annual rates, prorated by calendar days.
    regular = (full_year * paid_fraction + denominator // 2) // denominator
    # Overtime is an explicit extra amount, not recurring pay or bank receipts.
    return regular + getattr(stream, 'overtime_gross_minor', 0)
