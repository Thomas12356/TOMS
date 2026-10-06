"""Small, strict validators shared by expense and journey forms."""
import hashlib
import json
import re
from datetime import date
from zoneinfo import ZoneInfo
from decimal import Decimal

from services.transactions.income_form import parse_amount
from services.validation import optional_text, uid
from services.tax.mileage import LOCATIONS, VEHICLES


def text(value, field, maximum):
    result = optional_text(value, field=field, maximum=maximum)
    if not result:
        raise ValueError(f'Enter {field}.')
    return result


def payment_key(value):
    parts = value.split(':')
    if len(parts) != 3:
        raise ValueError('Choose a valid existing expense.')
    return tuple(uid(part) for part in parts)


def expense_version(payment, record):
    values = [payment.account_uid, payment.category_uid, payment.feed_item_uid, payment.amount_minor,
              payment.currency, payment.direction, payment.status, payment.transaction_time,
              payment.source_updated_at, record.as_dict() if record else None]
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def vehicle_key(value):
    value = value.strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9 -]{0,39}', value):
        raise ValueError('Use a vehicle registration or label of 1–40 letters, numbers, spaces or hyphens.')
    return re.sub(r'[ -]', '', value)


def expense_fields(form, payment):
    if not (payment.direction == 'OUT' and payment.status == 'SETTLED' and payment.currency == 'GBP'):
        raise ValueError('Only settled GBP expenses can be deducted.')
    day = payment.transaction_time.astimezone(ZoneInfo('Europe/London')).date()
    if not date(2026, 4, 6) <= day <= min(date.today(), date(2027, 4, 5)):
        raise ValueError('Choose an existing expense in the supported 2026–27 tax year.')
    amount = parse_amount(form.get('amount', ''), 'GBP')
    if amount is None or not 0 < amount <= payment.amount_minor:
        raise ValueError('The eligible amount must be greater than zero and no more than the payment.')
    category = form.get('category')
    if category not in ('general', 'vehicle_running', 'parking_tolls'):
        raise ValueError('Choose a valid expense category.')
    key = vehicle_key(form.get('vehicle_key', '')) if category == 'vehicle_running' else ''
    if form.get('eligible') != '1':
        raise ValueError('Confirm that this is an eligible, unreimbursed expense.')
    return dict(amount_minor=amount, purpose=text(form.get('purpose'), 'business purpose', 1000),
                category=category, vehicle_key=key, recorded_amount_minor=payment.amount_minor,
                recorded_currency=payment.currency, recorded_time=payment.transaction_time)


def mileage_fields(form, stream):
    try:
        journey = date.fromisoformat(form.get('journey_date', ''))
    except ValueError:
        raise ValueError('Enter a valid journey date.') from None
    if not date(2026, 4, 6) <= journey <= min(date.today(), date(2027, 4, 5)):
        raise ValueError('Log a completed journey in 2026–27 (6 April 2026 to 5 April 2027). Other years need reviewed rules.')
    if form.get('location') not in LOCATIONS or form.get('vehicle_type') not in VEHICLES:
        raise ValueError('Choose a supported UK location and vehicle type.')
    if form['vehicle_type'] == 'bicycle' and stream.kind != 'employed':
        raise ValueError('Bicycle mileage is supported for employees only.')
    miles = form.get('miles', '')
    if not re.fullmatch(r'[0-9]{1,6}(?:\.[0-9]{1,2})?', miles) or not 0 < Decimal(miles) <= 100000:
        raise ValueError('Enter business miles greater than zero, up to 100,000, with at most two decimal places.')
    reimbursed = parse_amount(form.get('reimbursed', ''), 'GBP')
    if reimbursed is None or reimbursed > 2**63 - 1:
        raise ValueError('Enter the actual mileage reimbursement, including zero if none.')
    if stream.kind != 'employed' and reimbursed:
        raise ValueError('Business reimbursements must be recorded as business income; enter zero here.')
    if form.get('eligible') != '1':
        raise ValueError('Confirm the journey and vehicle qualify for mileage relief.')
    return dict(journey_date=journey, location=form['location'], vehicle_type=form['vehicle_type'],
                vehicle_key=vehicle_key(form.get('vehicle_key', '')), miles=Decimal(miles),
                purpose=text(form.get('purpose'), 'journey purpose', 1000),
                start_postcode=text(form.get('start_postcode'), 'start postcode', 12).upper(),
                end_postcode=text(form.get('end_postcode'), 'end postcode', 12).upper(),
                reimbursed_minor=reimbursed)


def mileage_version(entry):
    return hashlib.sha256(json.dumps(entry.as_dict(), sort_keys=True, default=str).encode()).hexdigest()
