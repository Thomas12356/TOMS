"""Overview reads stay private, isolated and consistent with planning services."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import uuid4
import unittest
import os

from sqlalchemy.exc import OperationalError

from app import app
from deduction_support import DeductionOwnerCase
from models import Account, AccountReserve, IncomeStream, Invoice, InvoicePayment, Transaction, TransactionIncome
from services.web.overview import saved_overview
from services.tax.reserves import current_plan
from support import ApiTestCase
import test_test_data as sample_support


class OverviewAuthTests(ApiTestCase):
    def test_owner_required_before_snapshot_read(self):
        with patch('routes.overview.export_session') as read:
            for headers in ({}, self.headers):
                self.assertEqual(self.client.get('/dashboard/overview', headers=headers).status_code, 302)
            read.assert_not_called()


class OverviewTests(DeductionOwnerCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch('services.tax.reserves.cached_status', return_value={'status': 'verified'}))
        self.enterContext(patch('services.tax.ni_rules.cached_status', return_value={'status': 'verified'}))
        # The existing fixture deliberately keeps writes within one rollback-only
        # connection. Snapshot routing is exercised separately with real engines.
        @contextmanager
        def snapshot():
            yield self.session
        self.enterContext(patch('routes.overview.export_session', snapshot))

    def test_unknown_target_and_safe_next_action(self):
        stream = self.session.get(IncomeStream, self.stream)
        stream.name = '<script>Unsafe</script>'
        stream.expected_gross_minor = stream.expected_gross_currency = stream.expected_gross_period = None
        self.session.commit()
        with patch('services.tax.rules.start_rule_check') as rules:
            result = saved_overview(self.session)
            self.assertIsNone(result['target'])
            self.assertIsNone(result['gap'])
            self.assertFalse(result['checks']['annual_ready'])
            response = self.client.get('/dashboard/overview')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertIn('Unavailable', response.get_data(as_text=True))
            self.assertIn('&lt;script&gt;Unsafe&lt;/script&gt;', response.get_data(as_text=True))
            self.assertNotIn('<script>Unsafe</script>', response.get_data(as_text=True))
            rules.assert_not_called()
            self.bank.assert_not_called()

    def test_earmarks_and_outstanding_invoices_keep_currencies_separate(self):
        account = str(uuid4())
        self.session.add(Account(account_uid=account, default_category_uid=str(uuid4()), name='Euro', currency='EUR', account_type='PRIMARY', raw_payload={}))
        self.session.flush()
        self.session.add_all([AccountReserve(account_uid=self.account, tax_minor=123, ni_minor=456),
                              AccountReserve(account_uid=account, tax_minor=500, ni_minor=0)])
        for currency, total in (('GBP', 10000), ('EUR', 7000)):
            invoice = Invoice(id=str(uuid4()), income_stream_id=self.stream, stream_name='Business',
                client_name='Client', state='issued', number='OVERVIEW-' + currency,
                issued_at=datetime.now(timezone.utc), issue_date=date(2025, 4, 6), due_date=date(2025, 5, 6),
                currency=currency, total_minor=total)
            self.session.add(invoice)
            self.session.flush()
            if currency == 'GBP':
                self.session.add(InvoicePayment(id=str(uuid4()), invoice_id=invoice.id,
                    account_uid=self.account, category_uid=self.category, feed_item_uid=self.incoming,
                    cash_minor=1000, payment_version='stale-version', payment_label='Old payment'))
        self.session.commit()
        result = saved_overview(self.session)
        self.assertEqual(result['earmarks'], {'EUR': 500, 'GBP': 579})
        self.assertEqual(result['invoices']['GBP']['outstanding'], 10000)
        self.assertEqual(result['invoices']['EUR']['outstanding'], 7000)
        self.assertEqual(result['invoices']['GBP']['review'], 1)
        html = self.client.get('/dashboard/overview').get_data(as_text=True)
        self.assertIn('Outstanding amounts exclude stale allocations.', html)
        plan = current_plan(refresh_rules=False, session=self.session)
        expected = plan['received_reserve_minor'] if result['checks']['received_ready'] else None
        self.assertEqual(result['target'], expected)
        self.assertEqual(result['gap'], None if expected is None else max(0, expected - 579))

    def test_errors_reject_extra_queries_and_do_not_expose_database_details(self):
        self.assertEqual(self.client.get('/dashboard/overview?unexpected=1').status_code, 400)
        with patch('routes.overview.saved_overview', side_effect=OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-PASSWORD'))):
            response = self.client.get('/dashboard/overview')
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('PRIVATE-', response.get_data(as_text=True))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_available_target_and_gap_match_saved_plan(self):
        receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        receipt.source = 'FASTER_PAYMENTS_IN'
        receipt.transaction_time = datetime(2026, 4, 12, 12, tzinfo=timezone.utc)
        receipt.confirmed_at = datetime.now(timezone.utc)
        receipt.income = TransactionIncome(income_stream_id=self.stream, income_type='other',
            gross_minor=receipt.amount_minor, tax_deducted_minor=0, tax_treatment='no_tax_deducted',
            recorded_currency='GBP', adjustment_minor=0, needs_review=False)
        self.payment().confirmed_at = datetime.now(timezone.utc)
        self.session.add(AccountReserve(account_uid=self.account, tax_minor=500, ni_minor=0))
        self.session.commit()
        result = saved_overview(self.session)
        plan = current_plan(refresh_rules=False, session=self.session)
        self.assertTrue(result['checks']['received_ready'])
        self.assertEqual(result['target'], plan['received_reserve_minor'])
        self.assertEqual(result['gap'], max(0, result['target'] - 500))


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS') == '1', 'Requires PostgreSQL.')
class OverviewIsolationTests(unittest.TestCase):
    setUp = sample_support.TestDataTests.setUp
    drop_schemas = sample_support.TestDataTests.drop_schemas
    csrf = sample_support.TestDataTests.csrf
    toggle = sample_support.TestDataTests.toggle
    def test_overview_snapshot_routes_sample_and_real_data(self):
        real = self.client.get('/dashboard/overview')
        self.assertEqual(real.status_code, 200)
        self.assertNotIn('Test mode · sample records', real.get_data(as_text=True))
        self.assertEqual(self.toggle(True).status_code, 303)
        sample = self.client.get('/dashboard/overview')
        self.assertEqual(sample.status_code, 200)
        self.assertIn('Test mode · sample records', sample.get_data(as_text=True))
        self.assertIn('sample records', sample.get_data(as_text=True))
        self.bank.assert_not_called()
