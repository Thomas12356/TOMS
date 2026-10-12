"""Reconciliation uses actual evidence, never a second income ledger."""
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from models import IncomeStream, IncomeShift, Transaction, TransactionIncome, TransactionClassification
from services.invoices import workflow as work
from services.invoices.reconciliation import reconcile, LIMIT
from services.tax.reserves import current_plan
from services.web.test_data import sample_id
from deduction_support import DeductionOwnerCase
from support import ApiTestCase
import test_test_data as sample_support


class ReconciliationAuthTests(ApiTestCase):
    def test_owner_required_before_read(self):
        with patch('routes.reconciliation.export_session') as read:
            for headers in ({}, self.headers):
                self.assertEqual(self.client.get('/dashboard/reconciliation', headers=headers).status_code, 302)
            read.assert_not_called()


class ReconciliationTests(DeductionOwnerCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch('services.tax.reserves.cached_status', return_value={'status': 'verified'}))
        self.enterContext(patch('services.tax.ni_rules.cached_status', return_value={'status': 'verified'}))
        self.session.get(IncomeStream, self.stream).income_mode = 'shifts'
        self.shifts = []
        for day in (10, 11):
            shift = IncomeShift(id=str(uuid4()), income_stream_id=self.stream,
                starts_at=datetime(2026,4,day,8,tzinfo=timezone.utc),
                ends_at=datetime(2026,4,day,16,tzinfo=timezone.utc),
                payment_mode='total', gross_minor=50000, currency='GBP', notes='Recorded work')
            self.session.add(shift)
            self.shifts.append(shift)
        self.receipt = self.session.get(Transaction, (self.account,self.category,self.incoming))
        self.receipt.transaction_time = datetime(2026,4,12,12,tzinfo=timezone.utc)
        self.receipt.source = 'FASTER_PAYMENTS_IN'
        self.receipt.amount_minor = 80000
        self.receipt.confirmed_at = datetime.now(timezone.utc)
        self.receipt.income = TransactionIncome(income_stream_id=self.stream, income_type='other',
            gross_minor=100000, tax_deducted_minor=20000, ni_deducted_minor=0, tax_treatment='cis',
            adjustment_minor=0, recorded_currency='GBP', needs_review=False)
        self.session.commit()

    def result(self, **options):
        return reconcile(self.session, **options)

    def invoice(self, shifts=None, issue_date='2026-04-12', issued=True):
        invoice = work.save_draft(str(uuid4()), self.stream,
            dict(client_name='Client', client_address='', reference='Work', issue_date=issue_date,
                due_date=issue_date, business_name='Test business', business_address='1 Example Road', business_contact='', payment_instructions='Bank transfer'),
            [shift.id for shift in (shifts or self.shifts)])
        self.session.commit()
        if issued:
            invoice = work.get_invoice(invoice.id, str(invoice.revision))
            work.issue(invoice)
            self.session.commit()
        return work.get_invoice(invoice.id)

    def allocate(self, invoice, cash, tax=0, ni=0):
        allocation = work.allocate(work.get_invoice(invoice.id, str(invoice.revision)),
            work.payment_key(self.receipt), cash, tax, ni)
        self.session.commit()
        return allocation

    def test_separate_work_and_actual_gross_with_withholding(self):
        before = current_plan(refresh_rules=False)
        with patch('services.tax.rules.start_rule_check') as rules:
            result = self.result()
            row = result['summaries'][0]
            self.assertEqual(row['work_minor'], 100000)
            self.assertEqual(row['received_minor'], 100000)
            self.assertEqual(row['discrepancy_minor'], 0)
            self.assertEqual(row['unallocated_minor'], 100000)
            self.assertEqual(result['counts']['unbilled'], 2)
            self.assertEqual(result['counts']['receipts'], 1)
            self.assertEqual(result['receipts'][0]['remaining'], dict(cash=80000,tax=20000,ni=0))
            self.assertEqual(current_plan(refresh_rules=False)['gross_minor'], before['gross_minor'])
            rules.assert_not_called()
            self.bank.assert_not_called()

    def test_forecast_sources_do_not_compare_overtime_to_all_pay(self):
        self.session.get(IncomeStream, self.stream).income_mode = 'forecast'
        self.session.commit()
        self.assertIsNone(self.result()['summaries'][0]['discrepancy_minor'])

    def test_missing_pay_and_missing_work_have_opposite_differences(self):
        self.shifts[0].gross_minor = 70000
        self.session.commit()
        self.assertEqual(self.result()['summaries'][0]['discrepancy_minor'], 20000)
        self.shifts[0].gross_minor = 30000
        self.session.commit()
        self.assertEqual(self.result()['summaries'][0]['discrepancy_minor'], -20000)

    def test_partial_full_removed_and_stale_allocations(self):
        invoice = self.invoice()
        payment = self.allocate(invoice, 40000, 10000)
        result = self.result()
        self.assertEqual(result['counts']['unbilled'], 0)
        self.assertEqual(result['summaries'][0]['received_minor'], 100000)
        self.assertEqual(result['receipts'][0]['available_minor'], 50000)
        self.assertEqual(result['invoices'][0]['summary']['outstanding'], 50000)
        self.assertEqual(result['receipts'][0]['invoices'][0].id, invoice.id)
        self.allocate(work.get_invoice(invoice.id), 40000, 10000)
        self.assertEqual(self.result()['counts']['receipts'], 0)
        self.assertEqual(self.result()['counts']['invoices'], 0)
        work.remove_payment(work.get_invoice(invoice.id), payment.id)
        self.session.commit()
        self.assertEqual(self.result()['receipts'][0]['available_minor'], 50000)
        self.receipt.reference = 'Bank changed reference'
        self.session.commit()
        result = self.result()
        self.assertEqual(result['counts']['receipts'], 0)
        self.assertEqual(result['counts']['review'], 1)
        self.assertTrue(result['invoices'][0]['summary']['stale'])
        self.assertIsNone(result['summaries'][0]['discrepancy_minor'])

    def test_old_invoice_allocations_reduce_current_receipt_budget(self):
        invoice = self.invoice(issue_date='2026-04-01')
        self.allocate(invoice, 40000, 10000)
        result = self.result()
        self.assertEqual(result['receipts'][0]['available_minor'], 50000)
        self.assertEqual(result['counts']['invoices'], 1)  # linked work is in year

    def test_drafts_reserve_work_and_cancelled_invoices_release_it(self):
        invoice = self.invoice(issued=False)
        self.assertEqual(self.result()['counts']['unbilled'], 0)
        self.assertEqual(self.result()['invoices'][0]['summary']['label'], 'Draft')
        work.cancel(work.get_invoice(invoice.id))
        self.session.commit()
        self.assertEqual(self.result()['counts']['unbilled'], 2)
        self.assertEqual(self.result()['counts']['invoices'], 0)

    def test_changed_work_does_not_change_issued_invoice_balance(self):
        invoice = self.invoice()
        self.shifts[0].gross_minor = 60000
        self.session.commit()
        result = self.result()
        self.assertEqual(result['counts']['changed_work'], 1)
        self.assertEqual(result['changed_work'][0]['invoice'].id, invoice.id)
        self.assertEqual(result['invoices'][0]['summary']['outstanding'], 100000)

    def test_incomplete_unconfirmed_and_mismatched_receipts_need_review(self):
        for changes in (dict(needs_review=True),dict(gross_minor=None),dict(tax_treatment='unknown',tax_deducted_minor=None),dict(adjustment_minor=100,adjustment_notes='Adjustment')):
            with self.subTest(changes=changes):
                saved = {key: getattr(self.receipt.income,key) for key in changes}
                for key,value in changes.items():
                    setattr(self.receipt.income,key,value)
                self.session.commit()
                result = self.result()
                self.assertEqual(result['counts']['review'], 1)
                self.assertEqual(result['counts']['receipts'], 0)
                self.assertIsNone(result['summaries'][0]['discrepancy_minor'])
                for key,value in saved.items():
                    setattr(self.receipt.income,key,value)
                self.session.commit()
        self.receipt.confirmed_at = None
        self.session.commit()
        self.assertEqual(self.result()['counts']['review'], 1)

    def test_unassigned_receipts_block_comparison_even_with_source_filter(self):
        self.receipt.income.income_stream_id = None
        self.session.commit()
        for options in ({},dict(stream_id=self.stream)):
            result = self.result(**options)
            self.assertEqual(result['counts']['review'], 1)
            self.assertIsNone(result['summaries'][0]['discrepancy_minor'])

    def test_pending_future_out_of_year_and_transfers_are_excluded(self):
        for changes in (dict(status='PENDING'),dict(transaction_time=datetime(2027,1,1,tzinfo=timezone.utc)),
                        dict(transaction_time=datetime(2026,4,5,22,59,tzinfo=timezone.utc)),dict(source='INTERNAL_TRANSFER')):
            saved = {key:getattr(self.receipt,key) for key in changes}
            for key,value in changes.items(): setattr(self.receipt,key,value)
            self.session.commit()
            self.assertEqual(self.result()['counts']['receipts'], 0)
            self.assertEqual(self.result()['counts']['review'], 0)
            for key,value in saved.items(): setattr(self.receipt,key,value)
            self.session.commit()
        self.shifts[0].starts_at = datetime(2027,1,1,8,tzinfo=timezone.utc)
        self.shifts[0].ends_at = datetime(2027,1,1,16,tzinfo=timezone.utc)
        self.session.commit()
        self.assertEqual(self.result()['summaries'][0]['work_minor'], 50000)

    def test_non_taxable_receipts_do_not_look_like_missing_business_pay(self):
        self.receipt.income.tax_treatment = 'non_taxable'
        self.receipt.income.tax_deducted_minor = 0
        self.session.commit()
        self.assertEqual(self.result()['counts']['receipts'], 0)
        self.assertEqual(self.result()['counts']['review'], 0)

    def test_non_taxable_receipts_that_changed_require_review(self):
        self.receipt.income.tax_treatment = 'non_taxable'
        self.receipt.income.tax_deducted_minor = 0
        self.receipt.income.needs_review = True
        self.session.commit()
        result = self.result()
        self.assertEqual(result['counts']['review'], 1)
        self.assertEqual(result['counts']['receipts'], 0)
        self.assertIsNone(result['summaries'][0]['discrepancy_minor'])

    def test_stream_filter_and_archived_records_keep_correct_totals(self):
        other = IncomeStream(id=str(uuid4()),name='Another source',kind='self_employed',income_mode='shifts',
            expected_gross_minor=100000,expected_gross_period='yearly',expected_gross_currency='GBP')
        self.session.add(other)
        self.session.flush()
        self.session.add(IncomeShift(id=str(uuid4()),income_stream_id=other.id,
            starts_at=datetime(2026,4,10,8,tzinfo=timezone.utc),ends_at=datetime(2026,4,10,9,tzinfo=timezone.utc),
            payment_mode='total',gross_minor=1000,currency='GBP',notes='Other work'))
        self.session.get(IncomeStream,self.stream).archived = True
        self.session.commit()
        result = self.result(stream_id=self.stream)
        self.assertEqual(len(result['summaries']), 1)
        self.assertEqual(result['summaries'][0]['work_minor'],100000)
        self.assertEqual(result['counts']['unbilled'],2)
        self.assertTrue(result['summaries'][0]['stream'].archived)

    def test_currency_groups_stay_separate(self):
        self.shifts[0].currency = 'EUR'
        self.receipt.currency = self.receipt.income.recorded_currency = 'EUR'
        self.session.commit()
        rows = {row['currency']:row for row in self.result()['summaries']}
        self.assertEqual(rows['EUR']['work_minor'], 50000)
        self.assertEqual(rows['EUR']['received_minor'], 100000)
        self.assertEqual(rows['GBP']['work_minor'], 50000)
        self.assertEqual(rows['GBP']['received_minor'], 0)

    def test_manual_transfer_classification_is_respected(self):
        self.receipt.classification = TransactionClassification(type='internal_transfer',notes='Owner transfer')
        self.session.commit()
        self.assertEqual(self.result()['counts']['receipts'], 0)

    def test_display_limit_keeps_complete_totals_and_counts(self):
        for index in range(LIMIT):
            self.session.add(IncomeShift(id=str(uuid4()),income_stream_id=self.stream,
                starts_at=datetime(2026,4,10,8,tzinfo=timezone.utc),ends_at=datetime(2026,4,10,9,tzinfo=timezone.utc),
                payment_mode='total',gross_minor=100,currency='GBP',notes='Work'))
        self.session.commit()
        result = self.result()
        self.assertEqual(result['counts']['unbilled'], LIMIT+2)
        self.assertEqual(len(result['unbilled']), LIMIT)
        self.assertEqual(result['summaries'][0]['work_minor'], 100000+LIMIT*100)

    def test_receipt_display_limit_keeps_all_gross_and_logged_withholding(self):
        for index in range(60):
            stamp = datetime(2026, 4, 15, 12, tzinfo=timezone.utc)
            receipt = Transaction(account_uid=self.account, category_uid=self.category, feed_item_uid=str(uuid4()),
                amount_minor=100, currency='GBP', direction='IN', status='SETTLED', transaction_time=stamp,
                source_updated_at=stamp, source='FASTER_PAYMENTS_IN', confirmed_at=stamp, raw_payload={})
            receipt.income = TransactionIncome(income_stream_id=self.stream, income_type='other', tax_treatment='cis',
                gross_minor=120, tax_deducted_minor=20, ni_deducted_minor=0, adjustment_minor=0,
                recorded_currency='GBP', needs_review=False)
            self.session.add(receipt)
        self.session.commit()
        result = self.result(stream_id=self.stream)
        self.assertEqual(result['counts']['receipts'], 61)
        self.assertEqual(len(result['receipts']), LIMIT)
        self.assertEqual(result['summaries'][0]['received_minor'], 107200)
        self.assertEqual(result['summaries'][0]['unallocated_minor'], 107200)

    def test_invoice_suggestions_are_bounded_without_hiding_total_choices(self):
        for index in range(8):
            shift = IncomeShift(id=str(uuid4()), income_stream_id=self.stream,
                starts_at=datetime(2026, 4, 20, 8, tzinfo=timezone.utc),
                ends_at=datetime(2026, 4, 20, 9, tzinfo=timezone.utc), payment_mode='total',
                gross_minor=1000, currency='GBP', notes='Extra work')
            self.session.add(shift)
            self.session.commit()
            self.invoice(shifts=[shift], issue_date='2026-04-21')
        result = self.result(stream_id=self.stream)
        self.assertEqual(result['receipts'][0]['candidate_count'], 8)
        self.assertEqual(len(result['receipts'][0]['invoices']), 5)
        self.assertEqual(result['counts']['invoices'], 8)
        self.assertEqual(result['summaries'][0]['received_minor'], 100000)

    def test_safe_database_failure_and_query_validation(self):
        error = OperationalError('SQL',{},Exception('PRIVATE-DETAILS'))
        with patch('routes.reconciliation.export_session',side_effect=error), patch('routes.reconciliation.log_failure'):
            response = self.client.get('/dashboard/reconciliation')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('PRIVATE-DETAILS',response.get_data(as_text=True))
        for suffix in ('?stream=invalid','?year=2025','?stream=&stream='):
            self.assertEqual(self.client.get('/dashboard/reconciliation'+suffix).status_code,400)


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS') == '1','Requires PostgreSQL.')
class ReconciliationIsolationTests(unittest.TestCase):
    setUp = sample_support.TestDataTests.setUp
    drop_schemas = sample_support.TestDataTests.drop_schemas
    csrf = sample_support.TestDataTests.csrf
    toggle = sample_support.TestDataTests.toggle

    def test_real_and_sample_pages_are_isolated_read_only_and_escape_text(self):
        real = self.client.get('/dashboard/reconciliation')
        self.assertEqual(real.status_code,200)
        self.assertEqual(real.headers['Cache-Control'],'no-store')
        self.assertIn('REAL-INCOME-SENTINEL',real.get_data(as_text=True))
        self.toggle(True)
        with self.admin.begin() as connection:
            connection.execute(text(f'UPDATE {self.demo_schema}.income_streams SET name=:name WHERE id=:id'),
                dict(name='<script>Client</script>',id=sample_id(22)))
        with patch('services.tax.rules.start_rule_check') as refresh:
            sample = self.client.get('/dashboard/reconciliation')
            self.assertEqual(sample.status_code,200)
            html = sample.get_data(as_text=True)
            self.assertNotIn('REAL-INCOME-SENTINEL',html)
            self.assertIn('&lt;script&gt;Client&lt;/script&gt;',html)
            self.assertNotIn('<script>Client</script>',html)
            self.assertEqual(self.client.get('/dashboard/reconciliation?stream='+str(uuid4())).status_code,400)
            refresh.assert_not_called()
            self.bank.assert_not_called()
