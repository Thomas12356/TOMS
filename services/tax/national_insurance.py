"""Standard Class 4 planning on combined business profits, in integer pence.

Class 1 is recorded from payslips, never guessed from annual earnings. Class 2
is not a compulsory cash charge. Exemptions and mixed Class 1/4 annual maximum
adjustments need an individual assessment and are not inferred here.
"""
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

RULE_FILE = Path(__file__).resolve().parents[2] / 'data/tax_rules/uk-ni-2026-27.json'


def reviewed_rules():
    return json.loads(RULE_FILE.read_text())


def class4(profit_minor, tax_year):
    rules = reviewed_rules()
    if rules['tax_year'] != tax_year:
        raise ValueError('No reviewed National Insurance rules for this year.')
    if type(profit_minor) is not int or profit_minor < 0:
        raise ValueError('Business profits must be nonnegative integer pence.')
    main = max(0, min(profit_minor, rules['upper_profits_minor']) - rules['lower_profits_minor'])
    upper = max(0, profit_minor - rules['upper_profits_minor'])
    charge = (Decimal(main) * rules['main_percent'] + Decimal(upper) * rules['upper_percent']) / 100
    return int(charge.quantize(Decimal(1), rounding=ROUND_HALF_UP))
