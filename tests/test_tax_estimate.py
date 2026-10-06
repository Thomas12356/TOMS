"""Synthetic annual examples, exact rounding, ownership and incomplete inputs."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4


from app import app
from models import BrowserSession
from services.tax.estimate import income_tax, estimate_streams
from services.tax.rules import reviewed_rules
from services.web.sessions import token_hash
from support import PostgreSQLTestCase


def stream(gross, kind='self_employed', mode='none', deducted=None, **changes):
    values = dict(id=str(uuid4()), name='Synthetic job', kind=kind, archived=False,
                  expected_gross_minor=gross, expected_gross_period='yearly', expected_gross_currency='GBP',
                  unpaid_holiday_weeks=Decimal(0), unpaid_holiday_unit='weeks', forecast_tax_year='2026-27',
                  forecast_starts_on=None, forecast_ends_on=None, withholding_mode=mode,
                  expected_tax_deducted_minor=deducted)
    values.update(changes)
    return SimpleNamespace(**values)


class TaxCalculationTests(unittest.TestCase):
    def setUp(self):
        self.rules = reviewed_rules()

    def test_official_bands_and_allowance_taper(self):
        for pounds, tax in ((0, 0), (12570, 0), (30000, 3486), (50270, 7540),
                            (60000, 11432), (100000, 27432), (110000, 33432),
                            (125140, 42516), (150000, 53703)):
            with self.subTest(pounds=pounds):
                result = income_tax(pounds * 100, self.rules)
                self.assertEqual(result['tax_minor'], tax * 100)
                self.assertEqual(sum(band['tax_minor'] for band in result['bands']), Decimal(tax * 100))
        self.assertEqual(income_tax(11000000, self.rules)['allowance_minor'], Decimal(757000))
        self.assertEqual(income_tax(12514000, self.rules)['allowance_minor'], 0)

    def test_pennies_at_thresholds_and_single_rounding(self):
        for gross, tax in ((1257001, 0), (1257003, 1), (5027001, 754000),
                           (10000001, 2743201), (12514001, 4251600)):
            self.assertEqual(income_tax(gross, self.rules)['tax_minor'], tax)
        self.assertEqual(income_tax(10000001, self.rules)['allowance_minor'], Decimal('1256999.5'))
        for invalid in (-1, True, 1.5, '100'):
            with self.assertRaises(ValueError):
                income_tax(invalid, self.rules)

    def test_employment_uses_bands_before_untaxed_income(self):
        result = estimate_streams([stream(4000000, 'employed', 'paye_estimate'), stream(2000000)], self.rules)
        self.assertEqual(result['calculation']['tax_minor'], 1143200)
        self.assertEqual(result['withheld_minor'], 0)
        self.assertEqual(result['reserve_minor'], 594600)
        self.assertEqual(result['reserve_percent'], Decimal('29.73'))
        result = estimate_streams([stream(10000000, 'employed', 'paye_estimate'), stream(1000000)], self.rules)
        self.assertEqual(result['reserve_minor'], 600000)
        self.assertEqual(result['reserve_percent'], Decimal('60.00'))

    def test_only_logged_credits_reduce_tax_not_legacy_forecasts(self):
        employee, business = stream(4000000, 'employed', 'paye_estimate'), stream(2000000, 'cis', 'manual', 400000)
        result = estimate_streams([employee, business], self.rules)
        self.assertEqual(result['withheld_minor'], 0)
        self.assertEqual(result['uncovered_minor'], 1143200)
        self.assertEqual(result['reserve_minor'], 594600)
        logged = estimate_streams([employee, business], self.rules, credits={employee.id: 100000, business.id: 200000})
        self.assertEqual(logged['withheld_minor'], 300000)
        self.assertEqual(logged['uncovered_minor'], 843200)
        self.assertEqual(logged['reserve_minor'], 394600)
        self.assertEqual(logged['reserve_percent'], Decimal('21.92'))

    def test_employment_only_has_no_manual_business_reserve(self):
        result = estimate_streams([stream(6000000, 'employed')], self.rules)
        self.assertEqual(result['withheld_minor'], 0)
        self.assertEqual(result['reserve_minor'], 0)
        self.assertEqual(result['uncovered_minor'], 1143200)
        self.assertIsNone(result['reserve_percent'])

    def test_expenses_reduce_earnings_not_credit_tax_pound_for_pound(self):
        business = stream(3000000)
        result = estimate_streams([business], self.rules, deductions={business.id: 100000})
        self.assertEqual(result['gross_minor'], 3000000)
        self.assertEqual(result['calculation']['gross_minor'], 2900000)
        self.assertEqual(result['calculation']['tax_minor'], 328600)
        self.assertEqual(result['withheld_minor'], 0)
        loss = estimate_streams([business], self.rules, deductions={business.id: 3000001})
        self.assertTrue(loss['blockers'])
        self.assertIsNone(loss['reserve_minor'])

    def test_excess_logged_credits_never_generate_a_negative_target(self):
        business = stream(3000000, 'cis')
        result = estimate_streams([business], self.rules, credits={business.id: 600000})
        self.assertEqual(result['reserve_minor'], 0)
        self.assertEqual(result['excess_withheld_minor'], 251400)

    def test_missing_and_foreign_gross_block_partial_totals(self):
        for missing in (stream(None, expected_gross_period=None, expected_gross_currency=None), stream(100000, expected_gross_currency='EUR')):
            result = estimate_streams([stream(5000000), missing], self.rules)
            self.assertTrue(result['blockers'])
            self.assertIsNone(result['calculation'])
            self.assertIsNone(result['reserve_minor'])
        self.assertTrue(estimate_streams([], self.rules)['blockers'])

    def test_archived_and_part_year_income_is_not_lost(self):
        from datetime import date
        part = stream(5200000, archived=True, expected_gross_period='yearly',
                      forecast_starts_on=date(2026, 4, 6), forecast_ends_on=date(2026, 10, 5),
                      unpaid_holiday_weeks=Decimal(2))
        result = estimate_streams([part], self.rules)
        self.assertEqual(result['calculation']['gross_minor'], 2407123)
        self.assertEqual(result['reserve_minor'], 230025)

    def test_more_logged_business_credits_never_increase_reserve(self):
        business = stream(5000000, 'cis')
        targets = [estimate_streams([business], self.rules, credits={business.id: amount})['reserve_minor']
                   for amount in (0, 100000, 500000, 1000000, 5000000)]
        self.assertEqual(targets, sorted(targets, reverse=True))


class TaxEstimatePageTests(PostgreSQLTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(app.config, SECRET_KEY='a' * 64, SESSION_COOKIE_SECURE=False))
        now, token = datetime.now(timezone.utc), 'b' * 64
        self.session.add(BrowserSession(token_hash=token_hash(token), created_at=now, last_seen_at=now,
                                        expires_at=now + timedelta(hours=8)))
        self.session.commit()
        with self.client.session_transaction() as cookie:
            cookie['_user_id'] = token

    def page(self, rows):
        with patch.object(self.session, 'scalars', return_value=SimpleNamespace(all=lambda: rows)), patch('services.tax.records.tax_records', return_value={}):
            return self.client.get('/dashboard/tax-estimate')

    def test_owner_page_nav_numbers_and_no_bank_calls(self):
        response = self.page([stream(4000000, 'employed', 'paye_estimate'), stream(2000000)])
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for expected in ('GBP 60,000.00', 'GBP 11,432.00', 'GBP 0.00', 'GBP 5,946.00', '29.73%', 'whole year'):
            self.assertIn(expected, html)
        self.assertIn('aria-current="page">Tax estimate', html)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.bank.assert_not_called()

    def test_incomplete_and_archived_names_are_escaped(self):
        response = self.page([stream(None, 'cis', 'unknown', name='<script>alert(1)</script>', archived=True)])
        html = response.get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('Finish your estimate', html)
        self.assertIn('Archived, still included', html)
        self.assertNotIn('class="tax-plan"', html)

    def test_empty_and_non_gbp_pages_render_without_misleading_total(self):
        for rows in ([], [stream(100000, expected_gross_currency='USD')]):
            response = self.page(rows)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('class="tax-totals"', response.get_data(as_text=True))

    def test_unsupported_duplicate_and_unrecognised_query_values_rejected(self):
        for query in ('year=2025-26', 'region=scotland', 'year=2026-27&year=2026-27', 'account=all'):
            self.assertEqual(self.client.get('/dashboard/tax-estimate?' + query).status_code, 400)

    def test_bearer_and_anonymous_cannot_view_owner_forecasts(self):
        self.assertEqual(self.client.get('/dashboard/tax-estimate', headers=self.headers).status_code, 302)
        self.assertEqual(self.client.get('/dashboard/tax-estimate', headers={'Authorization': 'Bearer wrong'}).status_code, 401)
        with self.client.session_transaction() as cookie:
            cookie.clear()
        self.assertEqual(self.client.get('/dashboard/tax-estimate').status_code, 302)
