"""Convert readable form amounts to exact integer minor units; never use floats."""
import re
from decimal import Decimal

from werkzeug.exceptions import BadRequest
from services.transactions.income import income_body

FIELDS = ('income_stream_id', 'income_type', 'tax_treatment', 'source_name', 'gross', 'tax_deducted', 'ni_deducted', 'adjustment', 'adjustment_notes')


def decimal_places(currency):
    # Match the dashboard's supported currencies; others explicitly use minor units.
    return 2 if currency in ('GBP', 'EUR', 'USD') else 0


def display_amount(amount, currency):
    if amount is None:
        return ''
    places = decimal_places(currency)
    sign = '-' if amount < 0 else ''
    amount = abs(amount)
    return f'{sign}{amount // 100}.{amount % 100:02d}' if places else f'{sign}{amount}'


def parse_amount(value, currency, *, signed=False, blank=None):
    value = value.strip()
    if not value:
        return blank
    places = decimal_places(currency)
    pattern = r'-?[0-9]+(?:\.[0-9]{1,2})?' if places else r'-?[0-9]+'
    if len(value) > 30 or not re.fullmatch(pattern, value):
        raise BadRequest('Enter amounts without commas, currency symbols or extra decimal places.')
    amount = int(Decimal(value) * (100 if places else 1))
    if amount < 0 and not signed:
        raise BadRequest('Gross income, income tax and NI deducted cannot be negative.')
    return amount


def form_values(form, currency, *, income_type=None):
    if set(form) - set(FIELDS) - {'csrf_token', 'action', 'version'} or any(len(form.getlist(key)) != 1 for key in form):
        raise BadRequest('Supply each form field once.')
    return income_body({
        'income_type': income_type or form.get('income_type', ''), 'tax_treatment': form.get('tax_treatment', 'unknown'),
        'source_name': form.get('source_name', ''), 'adjustment_notes': form.get('adjustment_notes', ''),
        'gross_minor': parse_amount(form.get('gross', ''), currency),
        'tax_deducted_minor': parse_amount(form.get('tax_deducted', ''), currency),
        'ni_deducted_minor': parse_amount(form.get('ni_deducted', ''), currency),
        'adjustment_minor': parse_amount(form.get('adjustment', ''), currency, signed=True, blank=0),
    })
