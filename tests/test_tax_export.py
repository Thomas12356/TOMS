"""Evidence completeness, isolation, ownership and spreadsheet safety."""
import csv
import io
import json
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from uuid import uuid4
from models import IncomeStream, Invoice, InvoiceItem, InvoicePayment, InvoiceDelivery, Transaction, TransactionIncome
from services.transactions.review import payment_version
from services.tax.export import package, csv_bytes, export_session
from services.tax.reserves import current_plan
from deduction_support import DeductionOwnerCase
from support import ApiTestCase
import test_test_data as sample_support


class ExportAuthTests(ApiTestCase):
    def test_owner_required_for_page_and_download(self):
        with patch('routes.tax_export.export_session') as read:
            for path in ('/dashboard/tax-export', '/dashboard/tax-export/download'):
                for headers in ({}, self.headers):
                    self.assertEqual(self.client.get(path, headers=headers).status_code, 302)
            read.assert_not_called()

    def test_csv_formula_protection_preserves_numbers_and_blanks(self):
        for value in ('=SUM(A1)', ' +cmd', '\ttext', '\rtext', '-formula', '@formula'):
            rows = list(csv.DictReader(io.StringIO(csv_bytes([{'text': value, 'money': 123, 'unknown': None}], ['text','money','unknown']).decode('utf-8-sig'))))
            self.assertEqual(rows[0], {'text': "'"+value, 'money': '123', 'unknown': ''})


class ExportTests(DeductionOwnerCase):
    def test_package_agrees_with_plan_and_excludes_raw_payload(self):
        self.payment().counterparty_name = '=dangerous'
        self.payment().raw_payload = {'secret': 'RAW-MUST-NOT-EXPORT'}
        self.session.commit()
        with patch('services.tax.rules.start_rule_check') as fetch:
            output, manifest = package(self.session)
            plan = current_plan(refresh_rules=False)
            self.assertEqual(manifest['calculation']['reserve_minor'], plan['reserve_minor'])
            self.assertEqual(manifest['calculation']['gross_minor'], plan['gross_minor'])
            archive = ZipFile(output)
            data = json.loads(archive.read('report.json'))
            self.assertEqual(data['records']['transactions'][0]['counterparty_name'], '=dangerous')
            self.assertNotIn('RAW-MUST-NOT-EXPORT', archive.read('report.json').decode())
            csv_rows = list(csv.DictReader(io.StringIO(archive.read('transactions.csv').decode('utf-8-sig'))))
            self.assertEqual(csv_rows[0]['counterparty_name'], "'=dangerous")
            self.assertEqual(len(archive.namelist()), 13)
            fetch.assert_not_called()
            self.bank.assert_not_called()

    def test_unknown_forecast_remains_unknown_with_blockers(self):
        stream = self.session.get(IncomeStream, self.stream)
        stream.expected_gross_minor = stream.expected_gross_period = stream.expected_gross_currency = None
        self.session.commit()
        output, manifest = package(self.session)
        self.assertTrue(manifest['calculation']['blockers'])
        self.assertIsNone(manifest['calculation']['calculation'])
        self.assertIsNone(json.loads(ZipFile(output).read('report.json'))['records']['streams'][0]['expected_gross_minor'])
        self.assertIn('Unavailable', ZipFile(output).read('report.html').decode())

    def test_report_escapes_user_text_and_preserves_forecast_currency(self):
        stream = self.session.get(IncomeStream, self.stream)
        stream.name = '<script>Unsafe</script>'
        stream.expected_gross_currency = 'EUR'
        self.session.commit()
        output, manifest = package(self.session)
        report = ZipFile(output).read('report.html').decode()
        self.assertIn('EUR 30,000.00', report)
        self.assertIn('&lt;script&gt;Unsafe&lt;/script&gt;', report)
        self.assertNotIn('<script>Unsafe</script>', report)
        self.assertIsNone(manifest['calculation']['calculation'])

    def test_london_year_boundaries_and_pending_records(self):
        payment = self.payment()
        for timestamp, included in ((datetime(2026,4,5,22,59,tzinfo=timezone.utc),False),
                                    (datetime(2026,4,5,23,0,tzinfo=timezone.utc),True),
                                    (datetime(2027,4,5,22,59,tzinfo=timezone.utc),True),
                                    (datetime(2027,4,5,23,0,tzinfo=timezone.utc),False)):
            payment.transaction_time = timestamp
            self.session.commit()
            _, manifest = package(self.session)
            ids = [row['feed_item_uid'] for row in manifest['records']['transactions']]
            self.assertEqual(self.outgoing in ids, included)
        payment.transaction_time = datetime(2026,4,10,tzinfo=timezone.utc)
        payment.status = 'PENDING'
        self.session.commit()
        _, manifest = package(self.session)
        self.assertFalse(manifest['records']['transactions'][0]['settled_and_not_future'])

    def test_invoice_history_and_cross_year_evidence_are_complete(self):
        now = datetime.now(timezone.utc)
        invoice = Invoice(id=str(uuid4()), income_stream_id=self.stream, stream_name='Business',
            client_name='Client', issue_date=now.date(), due_date=now.date(), currency='GBP', total_minor=10000)
        self.session.add(invoice)
        self.session.flush()
        receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        receipt.transaction_time = datetime(2026, 4, 1, tzinfo=timezone.utc)
        receipt.source = 'FASTER_PAYMENTS_IN'
        receipt.confirmed_at = now
        receipt.income = TransactionIncome(income_stream_id=self.stream, income_type='other',
            gross_minor=receipt.amount_minor, tax_deducted_minor=0, tax_treatment='no_tax_deducted',
            adjustment_minor=0, recorded_currency='GBP', needs_review=False)
        self.session.flush()
        self.session.add(InvoiceItem(id=str(uuid4()), invoice_id=invoice.id, active=False,
            description='Removed item', amount_minor=10000, source_version='snapshot', shift_snapshot={'notes': 'Saved work evidence'}))
        allocation = InvoicePayment(id=str(uuid4()), invoice_id=invoice.id, account_uid=self.account,
            category_uid=self.category, feed_item_uid=self.incoming, cash_minor=1000, tax_minor=0,
            ni_minor=0, active=False, payment_version=payment_version(receipt), payment_label='Old payment')
        self.session.add(allocation)
        self.session.add(InvoiceDelivery(id=str(uuid4()), invoice_id=invoice.id, sent_at=now,
            method='email', recipient='client@example.invalid', notes='Owner logged'))
        self.session.commit()
        _, manifest = package(self.session)
        evidence = manifest['records']
        self.assertEqual(len(evidence['sending_history']), 1)
        self.assertEqual(evidence['invoice_items'][0]['shift_snapshot']['notes'], 'Saved work evidence')
        self.assertFalse(evidence['allocations'][0]['active'])
        self.assertTrue(evidence['allocations'][0]['current'])
        row = next(row for row in evidence['transactions'] if row['feed_item_uid'] == self.incoming)
        self.assertFalse(row['in_tax_year'])
        receipt.income.needs_review = True
        self.session.commit()
        _, manifest = package(self.session)
        self.assertFalse(manifest['records']['allocations'][0]['current'])

    def test_changed_expense_stays_visible_and_foreign_currency_is_preserved(self):
        self.assertEqual(self.expense().status_code, 303)
        payment = self.payment()
        payment.transaction_time = datetime(2026, 4, 1, tzinfo=timezone.utc)
        payment.currency = 'EUR'
        self.session.commit()
        output, manifest = package(self.session)
        self.assertEqual(manifest['counts']['expenses'], 1)
        self.assertFalse(manifest['records']['expenses'][0]['current'])
        self.assertEqual(manifest['records']['transactions'][0]['currency'], 'EUR')
        self.assertIn('Deductions need review', [item['title'] for item in manifest['calculation']['attention']])
        archive = ZipFile(output)
        for name, count in manifest['counts'].items():
            rows = list(csv.DictReader(io.StringIO(archive.read(name+'.csv').decode('utf-8-sig'))))
            self.assertEqual(len(rows), count)

    def test_database_failure_shows_safe_retry_page(self):
        error = OperationalError('private SQL', {}, Exception('PRIVATE-ERROR-CONTENT'))
        with patch('routes.tax_export.export_session', side_effect=error), patch('routes.tax_export.log_failure'):
            response = self.client.get('/dashboard/tax-export/download')
        self.assertEqual(response.status_code, 503)
        self.assertIn('The export is unavailable', response.get_data(as_text=True))
        self.assertNotIn('PRIVATE-ERROR-CONTENT', response.get_data(as_text=True))

    def test_page_and_query_validation(self):
        response = self.client.get('/dashboard/tax-export')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Download 2026-27 ZIP', response.get_data(as_text=True))
        for path in ('/dashboard/tax-export', '/dashboard/tax-export/download'):
            self.assertEqual(self.client.get(path+'?year=2025').status_code, 400)


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS') == '1', 'Requires PostgreSQL.')
class ExportIsolationTests(unittest.TestCase):
    setUp = sample_support.TestDataTests.setUp
    drop_schemas = sample_support.TestDataTests.drop_schemas
    csrf = sample_support.TestDataTests.csrf
    toggle = sample_support.TestDataTests.toggle
    # Use disposable schemas: the actual independent snapshot must see committed
    # records and must never route sample evidence to the owner's schema.
    def test_real_and_sample_downloads(self):
        real = self.client.get('/dashboard/tax-export/download')
        self.assertEqual(real.status_code, 200)
        self.assertEqual(real.headers['Cache-Control'], 'no-store')
        data = json.loads(ZipFile(io.BytesIO(real.data)).read('report.json'))
        self.assertFalse(data['sample_data'])
        self.assertIn('REAL-INCOME-SENTINEL', json.dumps(data))
        self.toggle(True)
        sample = self.client.get('/dashboard/tax-export/download')
        self.assertEqual(sample.status_code, 200)
        self.assertIn('TOMS-SAMPLE', sample.headers['Content-Disposition'])
        data = json.loads(ZipFile(io.BytesIO(sample.data)).read('report.json'))
        self.assertTrue(data['sample_data'])
        self.assertNotIn('REAL-INCOME-SENTINEL', json.dumps(data))
        self.assertEqual(data['counts']['streams'], 3)
        self.assertGreater(data['counts']['invoices'], 0)
        self.bank.assert_not_called()

    def test_export_has_consistent_read_only_snapshot(self):
        with export_session() as session:
            first = session.scalar(text('SHOW transaction_read_only'))
            self.assertEqual(first, 'on')
            stream = session.scalar(sample_support.db.select(IncomeStream))
            self.assertEqual(stream.name, 'REAL-INCOME-SENTINEL')
            with self.admin.begin() as writer:
                writer.execute(text(f"UPDATE {self.real_schema}.income_streams SET name = 'CHANGED'"))
            session.expire_all()
            self.assertEqual(session.scalar(sample_support.db.select(IncomeStream)).name, 'REAL-INCOME-SENTINEL')
