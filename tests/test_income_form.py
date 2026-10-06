"""Exact money conversion and authenticated income edits on rollback-only data."""
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import BadRequest

from app import app
from models import BrowserSession, Transaction, IncomeStream
from uuid import uuid4
from sqlalchemy import select
from services.transactions.classification import save_classification
from services.transactions.income_form import display_amount, form_values, parse_amount
from services.web.sessions import token_hash
from support import SavedTransactionTestCase


class IncomeAmountTests(unittest.TestCase):
    def test_exact_conversion_and_currency_units(self):
        for amount in (0, 1, 2500, 2**63 - 1, -(2**63)):
            self.assertEqual(parse_amount(display_amount(amount, 'GBP'), 'GBP', signed=True), amount)
        self.assertEqual(parse_amount('0.29', 'GBP'), 29)
        self.assertEqual(parse_amount('25', 'JPY'), 25)
        self.assertIsNone(parse_amount('', 'GBP'))

    def test_invalid_amounts_and_duplicate_fields(self):
        for value in ('1.001', '1e3', '£25', '1,000', '-1', 'NaN', 'Infinity'):
            with self.subTest(value=value), self.assertRaises(BadRequest):
                parse_amount(value, 'GBP')
        with self.assertRaises(BadRequest):
            parse_amount('1.00', 'JPY')
        with self.assertRaises(BadRequest):
            form_values(MultiDict([('income_type', 'other'), ('income_type', 'employment')]), 'GBP')
        with self.assertRaises(BadRequest):
            form_values(MultiDict({'income_type': 'other', 'gross': '92233720368547758.08'}), 'GBP')

    def test_unpaid_holiday_proration_and_exact_rounding(self):
        from decimal import Decimal
        from types import SimpleNamespace
        from services.transactions.income_streams import annual_gross
        for period, amount, weeks, expected in (
            ('weekly', 50000, '4', 2400000), ('monthly', 200000, '4', 2215385),
            ('yearly', 5200000, '4', 4800000), ('weekly', 50000, '2.5', 2475000),
            ('weekly', 1, '0.5', 52), ('monthly', 200000, '52', 0),
            ('yearly', 5200000, '0', 5200000)):
            with self.subTest(period=period, weeks=weeks):
                stream = SimpleNamespace(expected_gross_minor=amount, expected_gross_period=period,
                                         unpaid_holiday_weeks=Decimal(weeks))
                self.assertEqual(annual_gross(stream), expected)


class IncomeFormTests(SavedTransactionTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(app.config, SECRET_KEY='a' * 64, SESSION_COOKIE_SECURE=False))
        now = datetime.now(timezone.utc)
        token = 'e' * 64
        self.session.add(BrowserSession(token_hash=token_hash(token), created_at=now, last_seen_at=now,
                                        expires_at=now + timedelta(hours=8)))
        self.stream = str(uuid4())
        self.session.add(IncomeStream(id=self.stream, name='My job', kind='employed'))
        save_classification(self.payment(), 'income', None)
        self.session.commit()
        with self.client.session_transaction() as cookie:
            cookie['_user_id'] = token

    def payment(self):
        self.session.expire_all()
        return self.session.get(Transaction, (self.account, self.category, self.incoming))

    def path(self, item=None):
        return f'/dashboard/transactions/{self.account}/{self.category}/{item or self.incoming}/income'

    def submit(self, **values):
        path = self.path() + f'?account={self.account}&page=2'
        html = self.client.get(path).get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
        data = dict(income_stream_id=self.stream, tax_treatment='paye', gross='30.00', tax_deducted='5.00',
                    source_name='Example employer', csrf_token=token, action='save',
                    version=re.search(r'name="version" value="([^"]+)"', html)[1])
        data.update(values)
        return self.client.post(path, data=data)

    def test_save_prefill_return_context_and_confirmation_reset(self):
        self.payment().confirmed_at = datetime.now(timezone.utc)
        self.session.commit()
        response = self.submit()
        self.assertEqual(response.status_code, 303)
        self.assertIn('page=2', response.location)
        self.assertIn('account=' + self.account, response.location)
        payment = self.payment()
        self.assertIsNone(payment.confirmed_at)
        self.assertEqual(payment.income.gross_minor, 3000)
        self.assertEqual(payment.income.tax_deducted_minor, 500)
        self.assertEqual(payment.income.recorded_currency, 'GBP')
        self.assertIn('value="30.00"', self.client.get(self.path()).get_data(as_text=True))
        self.bank.assert_not_called()

    def test_invalid_reconciliation_does_not_save_and_keeps_input(self):
        response = self.submit(gross='40.00')
        self.assertEqual(response.status_code, 400)
        self.assertIn('value="40.00"', response.get_data(as_text=True))
        self.assertIsNone(self.payment().income)

    def test_unknown_amounts_and_signed_adjustment(self):
        self.assertEqual(self.submit(tax_treatment='unknown', gross='', tax_deducted='').status_code, 303)
        self.assertIsNone(self.payment().income.gross_minor)
        self.assertEqual(self.submit(gross='31.00', adjustment='-1.00', adjustment_notes='Provider fee').status_code, 303)
        self.assertEqual(self.payment().income.adjustment_minor, -100)

    def test_changed_currency_requires_reentry_and_safe_text(self):
        self.assertEqual(self.submit(source_name='<script>alert(1)</script>').status_code, 303)
        payment = self.payment()
        payment.currency = 'EUR'
        payment.income.needs_review = True
        self.session.commit()
        html = self.client.get(self.path()).get_data(as_text=True)
        self.assertIn('Saved amounts were in GBP', html)
        self.assertIn('id="gross" name="gross" value=""', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertEqual(self.submit().status_code, 303)
        self.assertEqual(self.payment().income.recorded_currency, 'EUR')
        self.assertFalse(self.payment().income.needs_review)

    def test_auth_csrf_and_eligibility(self):
        self.assertEqual(self.client.post(self.path(), data={'income_type': 'employment'}).status_code, 400)
        self.assertEqual(self.client.get(self.path(), headers=self.headers).status_code, 302)
        self.assertEqual(self.client.get(self.path(self.outgoing)).status_code, 400)
        save_classification(self.payment(), 'internal_transfer', None)
        self.session.commit()
        self.assertEqual(self.client.get(self.path()).status_code, 400)
        with self.client.session_transaction() as cookie:
            cookie.clear()
        self.assertEqual(self.client.get(self.path()).status_code, 302)

    def test_income_streams_custom_filter_and_bank_isolation(self):
        response = self.client.get('/dashboard/income-streams')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('My job', html)
        self.assertNotIn('Amazon Flex', html)
        self.assertIn('No recorded income here yet', html)
        self.assertEqual(self.submit().status_code, 303)
        html = self.client.get('/dashboard/income-streams?stream=' + self.stream).get_data(as_text=True)
        self.assertIn('Example employer', html)
        self.assertIn('1 payments', html)
        self.assertIn('return_to=income-streams', html)
        self.assertEqual(self.client.get('/dashboard/income-streams?stream=' + str(uuid4())).status_code, 404)
        self.assertEqual(self.client.get('/dashboard/income-streams?stream=invalid').status_code, 400)
        self.assertEqual(self.client.get('/dashboard/income-streams?page=0').status_code, 400)
        self.bank.assert_not_called()

    def test_income_stream_return_is_constrained(self):
        self.assertEqual(self.submit().status_code, 303)
        path = self.path() + '?return_to=income-streams&stream=' + self.stream + '&page=2'
        html = self.client.get(path).get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
        response = self.client.post(path, data={'csrf_token': token, 'income_stream_id': self.stream, 'tax_treatment': 'unknown',
            'version': re.search(r'name="version" value="([^"]+)"', html)[1]})
        self.assertEqual(response.status_code, 303)
        self.assertIn('/dashboard/income-streams?', response.location)
        self.assertIn('stream=' + self.stream, response.location)
        self.assertIn('page=2', response.location)
        self.assertEqual(self.client.get(self.path() + '?return_to=https://example.com').status_code, 400)
        self.assertEqual(self.client.get(self.path() + '?return_to=income-streams&stream=invalid').status_code, 400)

    def test_income_streams_require_login_and_escape_sources(self):
        self.assertEqual(self.submit(source_name='<script>alert(1)</script>').status_code, 303)
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        with self.client.session_transaction() as cookie:
            cookie.clear()
        self.assertEqual(self.client.get('/dashboard/income-streams').status_code, 302)

    def stream_post(self, **data):
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        if data.get('action') in ('create', 'update'):
            data.setdefault('forecast_tax_year', '2026-27')
            data.setdefault('expected_gross', '30000.00')
            data.setdefault('expected_gross_period', 'yearly')
            data.setdefault('expected_gross_currency', 'GBP')
        data['csrf_token'] = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
        return self.client.post('/dashboard/income-streams', data=data)

    def test_forecasts_do_not_change_logged_deductions(self):
        self.assertEqual(self.submit().status_code, 303)
        response = self.stream_post(action='update', stream_id=self.stream, name='My job', kind='employed')
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.payment().income.tax_deducted_minor, 500)
        self.assertNotIn('name="withholding_mode"', self.client.get('/dashboard/income-streams').get_data(as_text=True))

    def test_retired_forecast_deduction_fields_are_rejected(self):
        for fields in ({'withholding_mode': 'paye_estimate'}, {'expected_tax_deducted': '3000'}):
            self.assertEqual(self.stream_post(action='create', name='New', kind='employed', **fields).status_code, 400)

    def test_create_rename_archive_restore_and_no_tax_assumptions(self):
        for kind in ('self_employed', 'employed', 'cis'):
            self.assertEqual(self.stream_post(action='create', name='My ' + kind, kind=kind).status_code, 303)
        self.assertEqual(self.stream_post(action='update', stream_id=self.stream, name='Renamed job', kind='employed').status_code, 303)
        self.assertEqual(self.stream_post(action='update', stream_id=self.stream, name='Job', kind='cis').status_code, 400)
        self.assertEqual(self.submit().status_code, 303)
        self.assertEqual(self.payment().income.income_stream_id, self.stream)
        self.assertEqual(self.stream_post(action='archive', stream_id=self.stream).status_code, 303)
        self.assertEqual(self.submit().status_code, 303)  # Existing assignments remain editable.
        cis = self.session.scalar(select(IncomeStream).where(IncomeStream.kind == 'cis'))
        self.assertEqual(self.submit(income_stream_id=cis.id, tax_treatment='unknown', gross='', tax_deducted='').status_code, 303)
        self.assertIsNone(self.payment().income.tax_deducted_minor)
        self.assertEqual(self.submit().status_code, 400)  # Cannot newly assign archived streams.
        self.assertEqual(self.stream_post(action='restore', stream_id=self.stream).status_code, 303)
        self.assertEqual(self.submit().status_code, 303)

    def test_stream_creation_validation_and_security(self):
        for values in ({'name': '', 'kind': 'employed'}, {'name': 'x' * 201, 'kind': 'employed'},
                       {'name': 'A', 'kind': 'roofing'}, {'name': 'A', 'kind': 'employed', 'extra': 'x'}):
            self.assertEqual(self.stream_post(action='create', **values).status_code, 400)
        self.assertEqual(self.client.post('/dashboard/income-streams', data={'action': 'create', 'name': 'Unsafe', 'kind': 'cis'}).status_code, 400)
        self.assertEqual(self.client.post('/dashboard/income-streams', headers=self.headers, data={'action': 'create', 'name': 'Unsafe', 'kind': 'cis'}).status_code, 302)
        self.assertEqual(self.submit(income_stream_id=str(uuid4())).status_code, 400)
        self.assertEqual(self.stream_post(action='create', name='<script>test</script>', kind='self_employed').status_code, 303)
        self.assertIn('&lt;script&gt;', self.client.get('/dashboard/income-streams').get_data(as_text=True))

    def test_streams_start_empty_and_income_requires_owner_choice(self):
        self.session.delete(self.session.get(IncomeStream, self.stream))
        self.session.commit()
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        self.assertIn('No streams yet', html)
        self.assertNotIn('Amazon Flex', html)
        self.assertIn('Self employed', html)
        self.assertIn('Employed', html)
        self.assertIn('CIS', html)
        self.assertEqual(self.submit(income_stream_id='').status_code, 400)
        self.assertIsNone(self.payment().income)

    def test_expected_income_creation_periods_and_annual_estimates(self):
        from services.transactions.income_streams import annual_gross
        for period, amount, annual in (('weekly', '123.45', 641940), ('monthly', '2000.01', 2400012), ('yearly', '30000', 3000000)):
            response = self.stream_post(action='create', name=period, kind='self_employed', expected_gross=amount,
                                        expected_gross_period=period, expected_gross_currency='EUR')
            self.assertEqual(response.status_code, 303)
            stream = self.session.scalar(select(IncomeStream).where(IncomeStream.name == period))
            self.assertEqual(annual_gross(stream), annual)
            self.assertEqual(stream.expected_gross_currency, 'EUR')
        self.assertEqual(self.stream_post(action='create', name='No income', kind='cis', expected_gross='0').status_code, 303)
        self.bank.assert_not_called()

    def test_forecast_update_preserves_payment_and_archive_preserves_forecast(self):
        self.assertEqual(self.submit().status_code, 303)
        self.assertEqual(self.stream_post(action='update', stream_id=self.stream, name='My job', kind='employed',
                                         expected_gross='1000.50', expected_gross_period='monthly').status_code, 303)
        self.session.expire_all()
        stream = self.session.get(IncomeStream, self.stream)
        self.assertEqual(stream.expected_gross_minor, 100050)
        self.assertEqual(self.payment().income.gross_minor, 3000)
        self.assertEqual(self.stream_post(action='archive', stream_id=self.stream).status_code, 303)
        self.session.expire_all()
        self.assertEqual(self.session.get(IncomeStream, self.stream).expected_gross_minor, 100050)

    def test_invalid_forecast_does_not_save_and_preserves_inputs(self):
        for value in ('', '-1', '1.001', '1e4', '£10', '1,000', '92233720368547758.08'):
            with self.subTest(value=value):
                response = self.stream_post(action='create', name='Invalid forecast', kind='employed', expected_gross=value)
                self.assertEqual(response.status_code, 400)
        for field, value in (('expected_gross_period', 'daily'), ('expected_gross_currency', 'INVALID')):
            self.assertEqual(self.stream_post(action='create', name='Invalid forecast', kind='employed', **{field: value}).status_code, 400)
        self.assertIsNone(self.session.scalar(select(IncomeStream).where(IncomeStream.name == 'Invalid forecast')))
        response = self.stream_post(action='update', stream_id=self.stream, name='Retain input', kind='employed', expected_gross='123.456')
        self.assertEqual(response.status_code, 400)
        self.assertIn('value="123.456"', response.get_data(as_text=True))
        self.assertIn('value="Retain input"', response.get_data(as_text=True))
        self.session.expire_all()
        self.assertIsNone(self.session.get(IncomeStream, self.stream).expected_gross_minor)
        self.assertIn('Expected gross income not set', self.client.get('/dashboard/income-streams').get_data(as_text=True))

    def test_unpaid_holiday_saved_updated_and_actual_income_preserved(self):
        from decimal import Decimal
        from services.transactions.income_streams import annual_gross
        self.assertEqual(self.submit().status_code, 303)
        self.assertEqual(self.stream_post(action='update', stream_id=self.stream, name='My job', kind='employed',
            expected_gross='500', expected_gross_period='weekly', unpaid_holiday='4').status_code, 303)
        self.session.expire_all()
        stream = self.session.get(IncomeStream, self.stream)
        self.assertEqual(stream.unpaid_holiday_weeks, Decimal('4'))
        self.assertEqual(annual_gross(stream), 2400000)
        self.assertEqual(self.payment().income.gross_minor, 3000)
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        self.assertIn('GBP 24,000.00 for 2026-27', html)
        self.assertIn('value="4.00"', html)
        self.assertEqual(self.stream_post(action='create', name='Holiday business', kind='self_employed',
            expected_gross='500', expected_gross_period='weekly', unpaid_holiday='2.5').status_code, 303)
        created = self.session.scalar(select(IncomeStream).where(IncomeStream.name == 'Holiday business'))
        self.assertEqual(created.unpaid_holiday_weeks, Decimal('2.5'))
        self.bank.assert_not_called()

    def test_invalid_unpaid_holiday_does_not_save_and_retains_input(self):
        for weeks in ('-1', '52.01', '100', '1.001', '1e1', 'NaN', 'Infinity', '1,5'):
            with self.subTest(weeks=weeks):
                self.assertEqual(self.stream_post(action='create', name='Invalid holiday', kind='employed',
                                                 unpaid_holiday=weeks).status_code, 400)
        response = self.stream_post(action='update', stream_id=self.stream, name='My job', kind='employed', unpaid_holiday='53')
        self.assertIn('value="53"', response.get_data(as_text=True))
        self.session.expire_all()
        self.assertEqual(self.session.get(IncomeStream, self.stream).unpaid_holiday_weeks, 0)
        self.assertIsNone(self.session.scalar(select(IncomeStream).where(IncomeStream.name == 'Invalid holiday')))

    def test_holiday_days_preserve_choice_and_match_weeks(self):
        from decimal import Decimal
        from services.transactions.income_streams import annual_gross
        for days, weeks, annual in (('20', '4', 2400000), ('0.01', '0.002', 2599900), ('260', '52', 0)):
            self.assertEqual(self.stream_post(action='update', stream_id=self.stream, name='My job', kind='employed',
                expected_gross='500', expected_gross_period='weekly', unpaid_holiday=days,
                unpaid_holiday_unit='days').status_code, 303)
            self.session.expire_all()
            stream = self.session.get(IncomeStream, self.stream)
            self.assertEqual(stream.unpaid_holiday_unit, 'days')
            self.assertEqual(stream.unpaid_holiday_weeks, Decimal(weeks))
            self.assertEqual(annual_gross(stream), annual)
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        self.assertIn('260.00 days unpaid holiday', html)
        self.assertIn('value="days" selected', html)

    def test_holiday_units_and_day_bounds_are_validated(self):
        for days in ('-1', '260.01', '261', '1.001', 'NaN'):
            self.assertEqual(self.stream_post(action='create', name='Invalid days', kind='employed',
                                             unpaid_holiday=days, unpaid_holiday_unit='days').status_code, 400)
        self.assertEqual(self.stream_post(action='create', name='Invalid unit', kind='employed',
                                         unpaid_holiday='1', unpaid_holiday_unit='hours').status_code, 400)

    def test_tax_rules_page_login_csrf_and_source_check(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory, patch.object(app, 'instance_path', directory), \
             patch('services.tax.rules.start_rule_check') as start, patch('services.tax.rules.refresh_rules') as refresh:
            response = self.client.get('/dashboard/tax-rules')
            self.assertEqual(response.status_code, 200)
            self.assertIn('England, Wales and Northern Ireland', response.get_data(as_text=True))
            start.assert_called_once_with(directory)
            token = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))[1]
            self.assertEqual(self.client.post('/dashboard/tax-rules', data={'csrf_token': token}).status_code, 303)
            refresh.assert_called_once_with(directory, force=True)
            self.assertEqual(self.client.post('/dashboard/tax-rules').status_code, 400)
            self.assertEqual(self.client.get('/dashboard/tax-rules', headers=self.headers).status_code, 302)
            with self.client.session_transaction() as cookie:
                cookie.clear()
            self.assertEqual(self.client.get('/dashboard/tax-rules').status_code, 302)

    def test_stale_income_form_cannot_save_old_amounts_in_new_currency(self):
        html = self.client.get(self.path()).get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
        version = re.search(r'name="version" value="([^"]+)"', html)[1]
        self.payment().currency = 'EUR'
        self.session.commit()
        response = self.client.post(self.path(), data={'csrf_token': token, 'version': version, 'income_stream_id': self.stream,
            'tax_treatment': 'paye', 'gross': '30.00', 'tax_deducted': '5.00'})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.payment().income)
        self.assertIn('id="gross" name="gross" value=""', response.get_data(as_text=True))
        self.assertEqual(self.submit().status_code, 303)

    def test_income_edits_reject_missing_duplicate_and_stale_versions(self):
        html = self.client.get(self.path()).get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
        old = re.search(r'name="version" value="([^"]+)"', html)[1]
        self.assertEqual(self.submit().status_code, 303)
        response = self.client.post(self.path(), data={'csrf_token': token, 'version': old, 'income_stream_id': self.stream,
            'tax_treatment': 'unknown', 'gross': '999.00'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.payment().income.gross_minor, 3000)
        self.assertEqual(self.submit(version='').status_code, 409)
        html = self.client.get(self.path()).get_data(as_text=True)
        current = re.search(r'name="version" value="([^"]+)"', html)[1]
        data = MultiDict([('csrf_token', token), ('version', current), ('version', current),
                          ('income_stream_id', self.stream), ('tax_treatment', 'unknown')])
        self.assertEqual(self.client.post(self.path(), data=data).status_code, 400)
        self.assertEqual(self.payment().income.gross_minor, 3000)

    def test_repeated_stream_fields_and_sql_injection_ids_cannot_mutate(self):
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
        self.assertEqual(self.client.post('/dashboard/income-streams', data=MultiDict([
            ('csrf_token', token), ('action', 'archive'), ('stream_id', self.stream), ('stream_id', str(uuid4()))])).status_code, 400)
        self.assertEqual(self.stream_post(action='archive', stream_id="' OR 1=1 --").status_code, 400)
        self.assertFalse(self.session.get(IncomeStream, self.stream).archived)

    def test_part_year_forecasts_clip_dates_and_keep_inclusive_boundaries(self):
        from datetime import date
        from types import SimpleNamespace
        from services.transactions.income_streams import annual_gross, active_days
        for starts, ends, days, expected in (
            (None, None, 365, 3650000),
            (date(2026, 10, 6), None, 182, 1820000),
            (date(2026, 4, 6), date(2026, 4, 6), 1, 10000),
            (date(2027, 4, 5), date(2027, 4, 5), 1, 10000),
            (date(2025, 1, 1), date(2028, 1, 1), 365, 3650000),
            (date(2025, 1, 1), date(2026, 4, 5), 0, 0),
            (date(2027, 4, 6), None, 0, 0)):
            with self.subTest(starts=starts, ends=ends):
                stream = SimpleNamespace(expected_gross_minor=3650000, expected_gross_period='yearly',
                    forecast_tax_year='2026-27', forecast_starts_on=starts, forecast_ends_on=ends, unpaid_holiday_weeks=0)
                self.assertEqual(active_days(stream), (days, 365))
                self.assertEqual(annual_gross(stream), expected)

        for period, amount, expected in (('weekly', 36500, 946400), ('monthly', 36500, 218400), ('yearly', 3650000, 1820000)):
            stream = SimpleNamespace(expected_gross_minor=amount, expected_gross_period=period,
                forecast_tax_year='2026-27', forecast_starts_on=date(2026, 10, 6), forecast_ends_on=None, unpaid_holiday_weeks=0)
            self.assertEqual(annual_gross(stream), expected)

    def test_dates_persist_and_update_forecast_without_changing_payments(self):
        from datetime import date
        from services.transactions.income_streams import annual_gross
        self.assertEqual(self.submit().status_code, 303)
        response = self.stream_post(action='update', stream_id=self.stream, name='My job', kind='employed',
            expected_gross='500', expected_gross_period='weekly', forecast_starts_on='2026-10-06',
            forecast_ends_on='2027-04-05', unpaid_holiday='4')
        self.assertEqual(response.status_code, 303)
        self.session.expire_all()
        stream = self.session.get(IncomeStream, self.stream)
        self.assertEqual(stream.forecast_starts_on, date(2026, 10, 6))
        self.assertEqual(stream.forecast_ends_on, date(2027, 4, 5))
        self.assertEqual(stream.forecast_tax_year, '2026-27')
        self.assertEqual(annual_gross(stream), 1096438)
        self.assertEqual(self.payment().income.gross_minor, 3000)
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        self.assertIn('GBP 10,964.38 for 2026-27', html)
        self.assertIn('value="2026-10-06"', html)
        self.assertIn('182 active days', html)
        self.bank.assert_not_called()

    def test_invalid_year_dates_and_excess_part_year_absence_are_rejected(self):
        for values in ({'forecast_tax_year': '2025-26'}, {'forecast_tax_year': ''},
                       {'forecast_starts_on': '2026-02-29'}, {'forecast_starts_on': '2026-W15-1'},
                       {'forecast_starts_on': '2027-02-01', 'forecast_ends_on': '2026-10-06'},
                       {'forecast_starts_on': '2027-04-05', 'unpaid_holiday': '1'},
                       {'forecast_starts_on': '2027-04-06', 'unpaid_holiday': '0.01'}):
            with self.subTest(values=values):
                self.assertEqual(self.stream_post(action='create', name='Invalid dates', kind='employed', **values).status_code, 400)
        self.assertIsNone(self.session.scalar(select(IncomeStream).where(IncomeStream.name == 'Invalid dates')))
        response = self.stream_post(action='update', stream_id=self.stream, name='My job', kind='employed',
                                   forecast_starts_on='2027-02-01', forecast_ends_on='2026-10-06')
        self.assertIn('value="2027-02-01"', response.get_data(as_text=True))
        self.session.expire_all()
        self.assertIsNone(self.session.get(IncomeStream, self.stream).forecast_starts_on)

    def test_database_rejects_unsupported_year_and_reversed_forecast_dates(self):
        from datetime import date
        from sqlalchemy.exc import IntegrityError
        for fields in ({'forecast_tax_year': '2025-26'},
                       {'forecast_starts_on': date(2027, 1, 1), 'forecast_ends_on': date(2026, 1, 1)}):
            with self.assertRaises(IntegrityError):
                with self.session.begin_nested():
                    stream = self.session.get(IncomeStream, self.stream)
                    for key, value in fields.items():
                        setattr(stream, key, value)
                    self.session.flush()
            self.session.expire_all()
        self.assertEqual(self.session.get(IncomeStream, self.stream).forecast_tax_year, '2026-27')
        self.assertIsNone(self.session.get(IncomeStream, self.stream).forecast_starts_on)
