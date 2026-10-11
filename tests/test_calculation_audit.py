"""Independent 2026–27 worked totals and penny boundaries, audited 11 October."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from random import Random
from types import SimpleNamespace
import unittest

from services.tax.estimate import income_tax
from services.tax.mileage import mileage_allowances
from services.tax.national_insurance import class4
from services.tax.rules import reviewed_rules


def penny(pounds):
    return int((pounds * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def audited_inputs():
    random = Random(20261011)
    values = {0, *(random.randrange(0, 100000001) for _ in range(20000))}
    for threshold in (1257000, 5027000, 10000000, 12514000):
        values.update(range(threshold - 100, threshold + 101))
    return sorted(values)


class CalculationAuditTests(unittest.TestCase):
    def test_income_tax_against_independent_effective_rate_totals(self):
        # GOV.UK standard EWNI earnings bands; taper creates a 60% interval.
        # This oracle uses gross-income breakpoints, independently of the
        # application's allowance subtraction and taxable-band iteration.
        rules = reviewed_rules()
        for amount in audited_inputs():
            pounds = Decimal(amount) / 100
            if pounds <= 12570:
                expected = Decimal(0)
            elif pounds <= 50270:
                expected = (pounds - 12570) * Decimal('.20')
            elif pounds <= 100000:
                expected = 7540 + (pounds - 50270) * Decimal('.40')
            elif pounds <= 125140:
                expected = 27432 + (pounds - 100000) * Decimal('.60')
            else:
                expected = 42516 + (pounds - 125140) * Decimal('.45')
            self.assertEqual(income_tax(amount, rules)['tax_minor'], penny(expected), amount)

    def test_class4_against_independent_profit_intervals(self):
        # The Class 4 maximum adjustment for mixed employment is outside scope.
        for amount in audited_inputs():
            pounds = Decimal(amount) / 100
            if pounds <= 12570:
                expected = Decimal(0)
            elif pounds <= 50270:
                expected = (pounds - 12570) * Decimal('.06')
            else:
                expected = 2262 + (pounds - 50270) * Decimal('.02')
            self.assertEqual(class4(amount, '2026-27'), penny(expected), amount)

    def test_hmrc_11000_mile_example_and_employee_reimbursement(self):
        # GOV.UK simplified-expenses example: 10,000 × 55p + 1,000 × 25p.
        def entry(kind, reimbursement):
            return SimpleNamespace(id='journey', income_stream_id='source',
                income_stream=SimpleNamespace(kind=kind, mileage_pool_id='pool'),
                journey_date=date(2026, 4, 10), location='england',
                vehicle_type='car_van', miles=Decimal(11000),
                reimbursed_minor=reimbursement)
        self.assertEqual(mileage_allowances([entry('self_employed', 0)]), {'source': 575000})
        # Relief reduces taxable income; it is not a £5,750 tax refund.
        self.assertEqual(mileage_allowances([entry('employed', 300000)]), {'source': 275000})
        self.assertEqual(mileage_allowances([entry('employed', 600000)]), {'source': 0})
