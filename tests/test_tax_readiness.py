"""Readiness distinguishes missing evidence from harmless unconfirmed spending."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from app import app
from services.tax.records import tax_records
from services.tax.rules import reviewed_rules


def payment(**changes):
    values = dict(account_uid='a', category_uid='b', feed_item_uid='c', counterparty_name='<script>Example</script>',
        transaction_time=datetime(2026, 4, 10, tzinfo=timezone.utc), status='SETTLED', currency='GBP',
        direction='IN', amount_minor=10000, confirmed_at=datetime(2026, 4, 11, tzinfo=timezone.utc),
        classification=None, income=None, expense=None, source='FASTER_PAYMENTS_IN')
    values.update(changes)
    return SimpleNamespace(**values)


def income(**changes):
    values = dict(needs_review=False, recorded_currency='GBP', income_stream_id='work', gross_minor=10000,
                  tax_deducted_minor=0, ni_deducted_minor=None, tax_treatment='no_tax_deducted')
    values.update(changes)
    return SimpleNamespace(**values)


class ReadinessTests(unittest.TestCase):
    def records(self, payments):
        with app.app_context(), patch('services.tax.records.db.session.scalars', side_effect=[
                SimpleNamespace(all=lambda: payments), SimpleNamespace(all=lambda: [])]):
            return tax_records(reviewed_rules(), as_of=datetime(2026, 10, 8, tzinfo=timezone.utc))

    def test_complete_confirmed_receipt_counts_once(self):
        result = self.records([payment(income=income())])
        self.assertEqual(result['received'], {'work': 10000})
        self.assertEqual(result['received_issues'], [])
        self.assertEqual(result['attention'], [])

    def test_unconfirmed_or_incomplete_income_blocks_received_target_only(self):
        for item in (payment(), payment(income=income(), confirmed_at=None),
                     payment(income=income(needs_review=True)), payment(income=income(gross_minor=None)),
                     payment(income=income(income_stream_id=None)), payment(income=income(tax_treatment='unknown'))):
            result = self.records([item])
            self.assertEqual(result['received'], {})
            self.assertTrue(result['received_issues'])
            self.assertEqual(result['issues'], [])
        result = self.records([payment()])
        self.assertEqual(result['attention'][0]['items'][0]['url'], '/dashboard/transactions/a/b/c/income')

    def test_unconfirmed_spending_does_not_block_tax(self):
        result = self.records([payment(direction='OUT', income=None, confirmed_at=None)])
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['received_issues'], [])
        self.assertEqual(result['attention'][0]['count'], 1)

    def test_pending_future_transfers_and_non_taxable_income_not_received(self):
        for item in (payment(status='PENDING', income=income()),
                     payment(transaction_time=datetime(2027, 1, 1, tzinfo=timezone.utc), income=income()),
                     payment(source='INTERNAL_TRANSFER'),
                     payment(income=income(tax_treatment='non_taxable', gross_minor=None, income_stream_id=None))):
            result = self.records([item])
            self.assertEqual(result['received'], {})
            self.assertEqual(result['received_issues'], [])

    def test_pending_linked_expense_is_excluded_and_requires_review(self):
        item = payment(status='PENDING', direction='OUT')
        item.expense = SimpleNamespace(transaction=item)
        result = self.records([item])
        self.assertEqual(result['deductions'], {})
        self.assertTrue(result['issues'])
        self.assertEqual(result['attention'][0]['url'], '/dashboard/deductions')

    def test_large_import_counts_every_record_but_bounds_display(self):
        result = self.records([payment() for _ in range(100)])
        self.assertEqual(result['attention'][0]['count'], 100)
        self.assertEqual(len(result['attention'][0]['items']), 20)
