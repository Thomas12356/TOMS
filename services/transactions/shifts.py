"""Exact shift pay, UK local-time validation and aggregate planning totals."""
import hashlib
import json
import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from werkzeug.exceptions import BadRequest

from models import IncomeShift
from services.database.connection import db
from services.transactions.income_form import parse_amount
from services.transactions.income_streams import TAX_YEARS
from services.validation import optional_text

LOCAL_TIME = ZoneInfo('Europe/London')
FIELDS = {'csrf_token', 'action', 'shift_id', 'version', 'income_mode', 'starts_at', 'ends_at', 'unpaid_break_minutes',
          'payment_mode', 'hourly_rate', 'total_payment', 'notes'}


def local_time(value):
    """Reject nonexistent/ambiguous clock readings instead of guessing DST offsets."""
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}', value):
        raise ValueError('Enter a valid shift date and time.')
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('Enter a valid shift date and time.') from None
    local = naive.replace(tzinfo=LOCAL_TIME)
    if local.astimezone(timezone.utc).astimezone(LOCAL_TIME).replace(tzinfo=None) != naive:
        raise ValueError('This local time does not exist because the clocks change. Choose a valid time.')
    if local.utcoffset() != naive.replace(tzinfo=LOCAL_TIME, fold=1).utcoffset():
        raise ValueError('This local time occurs twice when the clocks change. Choose an unambiguous start/end time.')
    return local.astimezone(timezone.utc)


def shift_fields(form, stream):
    if set(form) - FIELDS or any(len(form.getlist(key)) != 1 for key in form):
        raise ValueError('Supply each shift field once.')
    starts, ends = local_time(form.get('starts_at', '')), local_time(form.get('ends_at', ''))
    first, last = TAX_YEARS[stream.forecast_tax_year]
    if starts.astimezone(LOCAL_TIME).date() < first or ends > datetime.combine(last + timedelta(days=1), time(), LOCAL_TIME):
        raise ValueError('Enter a shift within the stream’s supported tax year (2026–27).')
    minutes = int((ends - starts).total_seconds()) // 60
    if not 0 < minutes <= 1440:
        raise ValueError('The end must be after the start, with a shift lasting no more than 24 hours. Use the next date for overnight work.')
    breaks = form.get('unpaid_break_minutes', '0') or '0'
    if not re.fullmatch(r'[0-9]{1,4}', breaks) or not 0 <= int(breaks) < minutes:
        raise ValueError('Unpaid break minutes must be less than the shift length.')
    currency = stream.expected_gross_currency or 'GBP'
    mode = form.get('payment_mode')
    if mode not in ('hourly', 'total'):
        raise ValueError('Choose Hourly rate or Total payment.')
    try:
        rate = parse_amount(form.get('hourly_rate', ''), currency)
        total = parse_amount(form.get('total_payment', ''), currency)
    except BadRequest:
        raise ValueError('Enter positive shift amounts without commas, symbols or extra decimal places.') from None
    if mode == 'hourly':
        if rate is None or not 0 < rate <= 2**63 - 1 or total not in (None, 0):
            raise ValueError('Enter a positive hourly rate and leave total payment blank.')
        gross = int((Decimal(rate) * (minutes - int(breaks)) / 60).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    else:
        if total is None or not 0 < total <= 2**63 - 1 or rate not in (None, 0):
            raise ValueError('Enter a positive total payment and leave hourly rate blank.')
        gross, rate = total, None
    if not 0 < gross <= 2**63 - 1:
        raise ValueError('Shift pay must be greater than zero and fit the supported amount.')
    notes = optional_text(form.get('notes'), field='shift notes', maximum=1000) or ''
    return dict(starts_at=starts, ends_at=ends, unpaid_break_minutes=int(breaks), payment_mode=mode,
                hourly_rate_minor=rate, gross_minor=gross, currency=currency, notes=notes)


def shift_version(shift):
    return hashlib.sha256(json.dumps(shift.as_dict(), sort_keys=True, default=str).encode()).hexdigest()


def shift_label(shift):
    start, end = shift.starts_at.astimezone(LOCAL_TIME), shift.ends_at.astimezone(LOCAL_TIME)
    return f'{start:%d %b %Y %H:%M} – {end:%d %b %H:%M}'


def attach_shift_totals(streams):
    """One query: all work for shift mode, only overtime for regular forecasts."""
    selected = list(streams)
    if not selected:
        return
    query = db.select(IncomeShift.income_stream_id, IncomeShift.currency, IncomeShift.is_overtime, db.func.sum(IncomeShift.gross_minor)).where(
        IncomeShift.income_stream_id.in_([stream.id for stream in selected]),
        IncomeShift.starts_at >= datetime(2026, 4, 6, tzinfo=LOCAL_TIME),
        IncomeShift.starts_at < datetime(2027, 4, 6, tzinfo=LOCAL_TIME)).group_by(IncomeShift.income_stream_id, IncomeShift.currency, IncomeShift.is_overtime)
    totals = {(key, currency, overtime): int(total) for key, currency, overtime, total in db.session.execute(query)}
    for stream in selected:
        stream.overtime_gross_minor = totals.get((stream.id, stream.expected_gross_currency, True), 0)
        stream.shift_gross_minor = stream.overtime_gross_minor + totals.get((stream.id, stream.expected_gross_currency, False), 0)
