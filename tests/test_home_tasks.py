"""Home actions reflect saved evidence, without duplicates or financial writes."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, date
from unittest.mock import patch
from uuid import uuid4
import re
from sqlalchemy import text

from app import app
from deduction_support import DeductionOwnerCase
from models import Transaction, TransactionIncome, ExpenseDeduction, SyncRun, Invoice, InvoicePayment, IncomeShift, InvoiceItem
from services.web.overview import saved_overview
from services.invoices.workflow import shift_source_version
from services.database.connection import db
from services.database.migration_connection import migration_engine


class HomeTaskTests(DeductionOwnerCase):
    def setUp(self):
        # Global action counts need an empty disposable schema, not records
        # inherited from the working database's rollback-only test connection.
        self.enterContext(app.app_context())
        runtime = db.engine
        admin = migration_engine()
        self.addCleanup(admin.dispose)
        schema = 'test_home_' + uuid4().hex
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
        def drop_schema():
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        self.addCleanup(drop_schema)
        db.metadata.create_all(admin.execution_options(schema_translate_map={'toms': schema}))
        with admin.begin() as connection:
            connection.execute(text(f'GRANT USAGE ON SCHEMA {schema} TO toms_app'))
            connection.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO toms_app'))
        self.enterContext(patch.dict(db.engines, {None: runtime.execution_options(schema_translate_map={'toms': schema})}))
        super().setUp()
        self.now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
        self.enterContext(patch('services.tax.reserves.cached_status', return_value={'status': 'verified'}))
        self.enterContext(patch('services.tax.ni_rules.cached_status', return_value={'status': 'verified'}))
        self.enterContext(patch('services.invoices.workflow.today', return_value=self.now.date()))
        self.session.query(SyncRun).delete()
        @contextmanager
        def snapshot():
            yield self.session
        self.enterContext(patch('routes.overview.export_session', snapshot))
        self.payment().confirmed_at = self.now
        self.receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        self.receipt.transaction_time = datetime(2026, 5, 1, tzinfo=timezone.utc)
        self.receipt.source = 'FASTER_PAYMENTS_IN'
        self.receipt.confirmed_at = None
        self.session.commit()

    def result(self, category='all'):
        self.session.flush()
        return saved_overview(self.session, task_filter=category, as_of=self.now)

    def test_invoice_totals_and_priority_survive_batch_and_display_limits(self):
        invoices = []
        for index in range(225):
            invoice = Invoice(id=str(uuid4()), income_stream_id=self.stream, stream_name='Business',
                state='issued', number='BATCH-' + str(index), client_name='Client', currency='GBP',
                total_minor=1000, issue_date=date(2026, 4, 12), due_date=date(2026, 4, 30), issued_at=self.now)
            self.session.add(invoice)
            invoices.append(invoice)
        priority = invoices[-1]
        priority.issue_date = priority.due_date = date(2026, 10, 10)
        self.session.flush()
        self.session.add(InvoicePayment(id=str(uuid4()), invoice_id=priority.id,
            account_uid=self.account, category_uid=self.category, feed_item_uid=self.incoming,
            cash_minor=100, tax_minor=0, ni_minor=0, payment_version='stale', payment_label='Changed receipt'))
        result = self.result('invoices')
        group = self.groups(result)['invoices']
        self.assertEqual(group['count'], 225)
        self.assertEqual(len(group['items']), 20)
        self.assertEqual(group['items'][0]['key'], 'invoice:' + priority.id)
        self.assertEqual(result['invoices']['GBP'], dict(count=225, outstanding=225000, review=1))

    def groups(self, result):
        return {group['key']: group for group in result['inbox']['categories']}

    def sync(self, status='completed', options=None, when=None, error=None):
        when = when or self.now
        self.session.add(SyncRun(run_uid=str(uuid4()), status=status, requested_options=options or {},
            snapshot_at=when, started_at=when, finished_at=when if status != 'running' else None, error=error))
        self.session.flush()

    def test_payment_is_one_action_and_disappears_when_completed(self):
        result = self.result('payments')
        self.assertEqual(self.groups(result)['payments']['count'], 1)
        item = result['inbox']['items'][0]
        self.assertIn('Complete income', item['title'])
        self.assertIn('before confirming', item['reason'])
        self.assertIn('/review', item['url'])
        self.assertIsNone(result['target'])
        self.receipt.income = TransactionIncome(income_stream_id=self.stream, income_type='other',
            gross_minor=self.receipt.amount_minor, tax_deducted_minor=0, tax_treatment='no_tax_deducted',
            recorded_currency='GBP', adjustment_minor=0, needs_review=False)
        self.receipt.confirmed_at = self.now
        result = self.result('payments')
        self.assertEqual(self.groups(result)['payments']['count'], 0)
        self.assertEqual(result['inbox']['items'], [])

    def test_all_years_pending_future_and_non_taxable_attention_matches_ledger(self):
        self.receipt.transaction_time = datetime(2025, 5, 1, tzinfo=timezone.utc)
        self.receipt.confirmed_at = self.now
        self.assertEqual(self.groups(self.result())['payments']['count'], 1)
        self.receipt.status = 'PENDING'
        self.assertEqual(self.groups(self.result())['payments']['count'], 0)
        self.receipt.confirmed_at = None
        self.assertEqual(self.groups(self.result())['payments']['count'], 1)
        self.assertIn('not required yet', self.result('payments')['inbox']['items'][0]['reason'])
        self.receipt.status = 'SETTLED'
        self.receipt.transaction_time = self.now + timedelta(days=1)
        self.assertIn('not required yet', self.result('payments')['inbox']['items'][0]['reason'])
        self.receipt.transaction_time = datetime(2025, 5, 1, tzinfo=timezone.utc)
        self.receipt.currency = 'EUR'
        self.receipt.confirmed_at = self.now
        self.receipt.income = TransactionIncome(income_type='personal_gift', tax_treatment='non_taxable',
            recorded_currency='EUR', gross_minor=None, needs_review=False)
        self.assertEqual(self.groups(self.result())['payments']['count'], 0)
        self.receipt.income.recorded_currency = 'GBP'
        self.assertEqual(self.groups(self.result())['payments']['count'], 1)
        self.assertIn('changed', self.result('payments')['inbox']['items'][0]['title'])

    def test_changed_expense_is_one_payment_action_and_confirmation_does_not_clear_it(self):
        payment = self.payment()
        payment.expense = ExpenseDeduction(income_stream_id=self.stream, amount_minor=500,
            purpose='Tools', category='general', is_tax_deductible=True, recorded_currency='GBP',
            recorded_amount_minor=payment.amount_minor + 1, recorded_time=payment.transaction_time)
        result = self.result('payments')
        changed = [item for item in result['inbox']['items'] if item['key'].endswith(self.outgoing)]
        self.assertEqual(len(changed), 1)
        self.assertIn('Correct changed', changed[0]['title'])
        self.assertIn('step=details', changed[0]['url'])
        payment.confirmed_at = self.now
        self.assertEqual(self.groups(self.result())['payments']['count'], 2)
        payment.expense.recorded_amount_minor = payment.amount_minor
        self.assertEqual(self.groups(self.result())['payments']['count'], 1)

    def test_import_failure_is_first_and_errors_are_not_exposed(self):
        self.sync(status='failed', error='PRIVATE-TOKEN bank payload')
        result = self.result()
        self.assertEqual(result['inbox']['items'][0]['key'], 'import:failed')
        self.assertEqual(self.groups(result)['imports']['count'], 1)
        html = self.client.get('/dashboard/overview').get_data(as_text=True)
        self.assertNotIn('PRIVATE-TOKEN', html)
        self.assertIn('Check the failed import', html)
        self.bank.assert_not_called()

    def test_partial_import_does_not_clear_freshness_and_running_has_no_false_failure(self):
        self.sync(options={'accountUid': self.account})
        self.assertEqual(self.groups(self.result())['imports']['count'], 1)
        self.sync(when=self.now - timedelta(hours=25))
        self.assertIn('24 hours', self.result()['inbox']['items'][0]['label'])
        self.sync(when=self.now - timedelta(minutes=5))
        self.assertEqual(self.groups(self.result())['imports']['count'], 0)
        self.sync(status='running', when=self.now - timedelta(minutes=1))
        self.assertEqual(self.groups(self.result())['imports']['count'], 0)
        self.session.query(SyncRun).delete()
        self.sync(status='running', when=self.now - timedelta(hours=2))
        self.assertIn('not proof', self.result()['inbox']['items'][0]['reason'])
        self.session.query(SyncRun).delete()
        self.sync(when=self.now + timedelta(hours=1))
        self.assertIn('ahead', self.result()['inbox']['items'][0]['label'])

    def test_later_partial_success_does_not_hide_failed_full_import(self):
        self.sync(when=self.now - timedelta(minutes=20))
        self.sync(status='failed', when=self.now - timedelta(minutes=5))
        self.sync(options={'accountUid': self.account}, when=self.now - timedelta(minutes=1))
        result = self.result('imports')
        self.assertEqual(result['import_status'], 'completed')
        self.assertEqual(result['inbox']['items'][0]['label'], 'Most recent full import failed')
        self.sync(when=self.now)
        self.assertEqual(self.groups(self.result())['imports']['count'], 0)

    def test_filter_limits_and_escaping_with_exact_full_counts(self):
        self.receipt.counterparty_name = 'X' * 10000
        for index in range(25):
            self.session.add(Transaction(account_uid=self.account, category_uid=self.category, feed_item_uid=str(uuid4()),
                currency='GBP', amount_minor=100, direction='IN', status='SETTLED',
                transaction_time=datetime(2026, 6, 1, tzinfo=timezone.utc), source_updated_at=self.now,
                source='FASTER_PAYMENTS_IN', counterparty_name='<script>Client</script>', raw_payload={}))
        result = self.result('payments')
        self.assertEqual(self.groups(result)['payments']['count'], 26)
        self.assertEqual(len(result['inbox']['items']), 20)
        self.assertEqual(len({item['key'] for item in result['inbox']['items']}), 20)
        self.assertLess(len(result['inbox']['items'][0]['label']), 220)
        response = self.client.get('/dashboard/overview?tasks=payments')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('Showing 20 of 26', html)
        self.assertIn('&lt;script&gt;Client&lt;/script&gt;', html)
        self.assertNotIn('<script>Client</script>', html)
        self.assertNotIn('X' * 10000, html)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_filter_validation_happens_before_any_snapshot(self):
        with patch('routes.overview.export_session') as read:
            for query in ('tasks=bad', 'tasks=imports&tasks=payments', 'tasks=', 'tasks=all&unexpected=1'):
                self.assertEqual(self.client.get('/dashboard/overview?' + query).status_code, 400)
            read.assert_not_called()

    def test_guided_review_returns_to_home_and_rejects_arbitrary_return_addresses(self):
        self.receipt.source = 'INTERNAL_TRANSFER'
        self.session.commit()
        url = f'/dashboard/transactions/{self.account}/{self.category}/{self.incoming}/review?return_to=home&tasks=payments'
        def submit(page_url, action, **fields):
            html = self.client.get(page_url).get_data(as_text=True)
            return self.client.post(page_url, data=dict(action=action, version=re.search(r'name="version" value="([^"]+)"', html)[1],
                csrf_token=re.search(r'name="csrf_token" value="([^"]+)"', html)[1], **fields))
        response = submit(url, 'classify', type='internal_transfer', notes='Checked transfer')
        self.assertEqual(response.status_code, 303)
        self.assertIn('return_to=home', response.location)
        response = submit(response.location, 'confirm')
        self.assertEqual(response.location, '/dashboard/overview?tasks=payments#home-actions')
        self.assertEqual(self.groups(self.result())['payments']['count'], 0)
        base = url.split('?')[0]
        for context in ('return_to=https://example.com', 'return_to=home&tasks=bad', 'tasks=payments',
                        'return_to=home&tasks=payments&account=all', 'return_to=home&return_to=home'):
            self.assertEqual(self.client.get(base + '?' + context).status_code, 400)

    def test_multiple_stale_allocations_count_correctly_and_invoice_action_is_unique(self):
        invoice = Invoice(id=str(uuid4()), income_stream_id=self.stream, stream_name='Business', client_name='Client',
            state='issued', number='TASK-1', issued_at=self.now, issue_date=date(2026, 5, 1),
            due_date=date(2026, 5, 31), currency='GBP', total_minor=10000)
        self.session.add(invoice)
        self.session.flush()
        for key in (self.incoming, self.outgoing):
            self.session.add(InvoicePayment(id=str(uuid4()), invoice_id=invoice.id, account_uid=self.account,
                category_uid=self.category, feed_item_uid=key, cash_minor=1000,
                payment_version='stale-version', payment_label='Old payment'))
        result = self.result('invoices')
        self.assertEqual(result['invoices']['GBP']['review'], 2)
        self.assertEqual(result['invoices']['GBP']['outstanding'], 10000)
        self.assertEqual(self.groups(result)['invoices']['count'], 1)
        self.assertIn('due date', result['inbox']['items'][0]['reason'])
        self.assertIn('stale', result['inbox']['items'][0]['reason'])
        self.assertEqual(result['inbox']['items'][0]['url'], '/dashboard/invoices/' + invoice.id)
        invoice.state = 'cancelled'
        invoice.cancelled_at = self.now
        self.assertEqual(self.groups(self.result())['invoices']['count'], 0)

    def test_changed_invoice_work_and_resolution_preserve_snapshot(self):
        shift = IncomeShift(id=str(uuid4()), income_stream_id=self.stream, starts_at=self.now - timedelta(days=2),
            ends_at=self.now - timedelta(days=2) + timedelta(hours=2), unpaid_break_minutes=0,
            payment_mode='total', gross_minor=10000, currency='GBP', notes='Work')
        invoice = Invoice(id=str(uuid4()), income_stream_id=self.stream, stream_name='Business', client_name='Client',
            state='issued', number='TASK-2', issued_at=self.now, issue_date=self.now.date(),
            due_date=date(2026, 11, 1), currency='GBP', total_minor=10000)
        self.session.add_all([shift, invoice])
        self.session.flush()
        original = shift_source_version(shift)
        item = InvoiceItem(id=str(uuid4()), invoice_id=invoice.id, shift_id=shift.id, active=True,
            description='Frozen work', amount_minor=10000, source_version=original, shift_snapshot={'notes': 'Work'})
        self.session.add(item)
        self.session.flush()
        shift.notes = 'Changed work'
        result = self.result('invoices')
        self.assertEqual(self.groups(result)['invoices']['count'], 1)
        self.assertIn('differs', result['inbox']['items'][0]['reason'])
        self.assertEqual(item.shift_snapshot, {'notes': 'Work'})
        self.assertEqual(item.source_version, original)
        shift.notes = 'Work'
        self.assertEqual(self.groups(self.result())['invoices']['count'], 0)

    def test_uninvoiced_work_does_not_invent_missing_pay_or_mileage(self):
        self.session.add(IncomeShift(id=str(uuid4()), income_stream_id=self.stream,
            starts_at=self.now - timedelta(days=2), ends_at=self.now - timedelta(days=2) + timedelta(hours=2),
            unpaid_break_minutes=0, payment_mode='total', gross_minor=10000, currency='GBP', notes='Payroll work'))
        result = self.result()
        self.assertEqual(self.groups(result)['invoices']['count'], 0)
        self.assertNotIn('missing mileage', str(result['inbox']))
        self.bank.assert_not_called()
