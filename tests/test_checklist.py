"""Checklist actions follow the existing planning rules and never call Starling."""
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.exc import OperationalError

from app import app
from models import IncomeStream, Transaction, TransactionIncome, ExpenseDeduction
from services.tax.checklist import guided_checklist
from services.tax.reserves import current_plan
from deduction_support import DeductionOwnerCase
from support import ApiTestCase


class ChecklistAuthTests(ApiTestCase):
    def test_owner_required_before_reading_records(self):
        with patch.dict(app.config, SECRET_KEY='a'*64), patch('routes.checklist.guided_checklist') as read:
            for headers in ({}, self.headers):
                self.assertEqual(self.client.get('/dashboard/checklist', headers=headers).status_code, 302)
            read.assert_not_called()


class ChecklistTests(DeductionOwnerCase):
    def test_shared_payment_snapshot_matches_independent_checks_at_boundaries(self):
        from services.tax.records import planning_payments
        from services.tax.rules import reviewed_rules
        now = datetime(2026, 10, 11, tzinfo=timezone.utc)
        self.assertEqual(self.expense().status_code, 303)
        # A claim saved in this year must remain visible after its bank date moves.
        self.payment().transaction_time = datetime(2025, 4, 1, tzinfo=timezone.utc)
        self.receipt.transaction_time = datetime(2027, 3, 1, tzinfo=timezone.utc)
        self.session.commit()
        shared = planning_payments(reviewed_rules(), self.session)
        compact = list(planning_payments(reviewed_rules(), self.session, stream=True))
        independent = current_plan(refresh_rules=False, session=self.session, as_of=now)
        reused = current_plan(refresh_rules=False, session=self.session, as_of=now, payments=shared)
        self.assertEqual(independent, reused)
        self.assertEqual(independent, current_plan(refresh_rules=False, session=self.session, as_of=now, payments=compact))
        self.assertEqual(guided_checklist(independent, as_of=now, session=self.session),
                         guided_checklist(reused, as_of=now, session=self.session, payments=shared))
        checks = guided_checklist(reused, as_of=now, session=self.session, payments=shared)
        self.assertEqual(next(step for step in checks['steps'] if step['key'] == 'deductions')['count'], 1)

    def setUp(self):
        super().setUp()
        self.enterContext(patch('services.tax.reserves.cached_status', return_value={'status': 'verified'}))
        self.enterContext(patch('services.tax.ni_rules.cached_status', return_value={'status': 'verified'}))
        self.receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        self.receipt.source = 'FASTER_PAYMENTS_IN'
        self.receipt.transaction_time = datetime(2026, 4, 12, 12, tzinfo=timezone.utc)
        self.receipt.confirmed_at = datetime.now(timezone.utc)
        self.receipt.income = TransactionIncome(income_stream_id=self.stream, income_type='other',
            gross_minor=self.receipt.amount_minor, tax_deducted_minor=0, tax_treatment='no_tax_deducted',
            recorded_currency='GBP', adjustment_minor=0, needs_review=False)
        self.payment().confirmed_at = datetime.now(timezone.utc)
        self.session.commit()

    def result(self):
        return guided_checklist(current_plan(refresh_rules=False))

    def steps(self):
        return {step['key']: step for step in self.result()['steps']}

    def test_clear_checks_and_no_bank_or_rule_fetch(self):
        with patch('services.tax.rules.start_rule_check') as rules:
            response = self.client.get('/dashboard/checklist')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            html = response.get_data(as_text=True)
            self.assertIn('5 of 5 checks clear', html)
            self.assertIn('not a completed tax return', html)
            self.assertTrue(self.result()['received_ready'])
            self.assertIsNone(self.result()['next_step'])
            rules.assert_not_called()
            self.bank.assert_not_called()

    def test_missing_forecast_and_actual_withholding_have_specific_actions(self):
        stream = self.session.get(IncomeStream, self.stream)
        stream.expected_gross_minor = None
        stream.expected_gross_period = None
        stream.expected_gross_currency = None
        self.receipt.counterparty_name = '<script>Client</script>'
        self.receipt.income.gross_minor = None
        self.receipt.income.tax_treatment = 'cis'
        self.receipt.income.tax_deducted_minor = None
        self.receipt.confirmed_at = None
        self.session.commit()
        steps = self.steps()
        self.assertEqual(steps['forecasts']['count'], 1)
        self.assertIn('configure='+self.stream, steps['forecasts']['items'][0]['url'])
        self.assertIn('actual tax withheld', steps['income']['items'][0]['reason'])
        self.assertIn('gross pay', steps['income']['items'][0]['reason'])
        self.assertEqual(steps['review']['count'], 1)
        result = self.result()
        self.assertEqual(result['next_step']['key'], 'forecasts')
        self.assertFalse(result['annual_ready'])
        self.assertFalse(result['received_ready'])
        html = self.client.get('/dashboard/checklist').get_data(as_text=True)
        self.assertIn('&lt;script&gt;Client&lt;/script&gt;', html)
        self.assertNotIn('<script>Client</script>', html)

    def test_unconfirmed_spending_does_not_block_tax_targets(self):
        self.payment().confirmed_at = None
        self.session.commit()
        result = self.result()
        self.assertEqual(self.steps()['review']['count'], 1)
        self.assertTrue(result['annual_ready'])
        self.assertTrue(result['received_ready'])
        self.assertEqual(result['next_step']['key'], 'review')

    def test_pending_future_and_transfers_do_not_create_income_actions(self):
        for changes in (dict(status='PENDING'), dict(transaction_time=datetime(2027, 1, 1, tzinfo=timezone.utc)),
                        dict(source='INTERNAL_TRANSFER')):
            with self.subTest(changes=changes):
                original = {key: getattr(self.receipt, key) for key in changes}
                self.receipt.income = None
                for key, value in changes.items():
                    setattr(self.receipt, key, value)
                self.session.commit()
                self.assertEqual(self.steps()['income']['count'], 0)
                for key, value in original.items():
                    setattr(self.receipt, key, value)
                self.session.commit()

    def test_non_taxable_receipt_does_not_require_stream_or_withholding(self):
        self.receipt.income.tax_treatment = 'non_taxable'
        self.receipt.income.gross_minor = None
        self.receipt.income.income_stream_id = None
        self.session.commit()
        self.assertEqual(self.steps()['income']['count'], 0)
        self.assertTrue(self.result()['received_ready'])

    def test_unknown_treatment_unassigned_income_and_changed_currency_are_actionable(self):
        self.receipt.income.income_stream_id = None
        self.receipt.income.tax_treatment = 'unknown'
        self.receipt.income.tax_deducted_minor = None
        self.receipt.income.needs_review = True
        self.receipt.currency = 'EUR'
        self.session.commit()
        reason = self.steps()['income']['items'][0]['reason']
        for text in ('Assign', 'tax treatment', 'payment changed', 'supports GBP'):
            self.assertIn(text, reason)
        self.assertFalse(self.result()['received_ready'])

    def test_changed_claim_is_linked_even_when_bank_date_moves_outside_year(self):
        self.assertEqual(self.expense().status_code, 303)
        claim = self.session.get(ExpenseDeduction, (self.account, self.category, self.outgoing))
        self.payment().transaction_time = datetime(2025, 4, 10, tzinfo=timezone.utc)
        self.session.commit()
        step = self.steps()['deductions']
        self.assertEqual(step['count'], 1)
        self.assertIn(self.outgoing, step['items'][0]['url'])
        self.assertFalse(self.result()['annual_ready'])
        claim.is_tax_deductible = False
        self.session.commit()
        self.assertEqual(self.steps()['deductions']['count'], 0)
        self.assertTrue(self.result()['annual_ready'])

    def test_large_import_counts_all_but_bounds_items(self):
        for _ in range(25):
            self.session.add(Transaction(account_uid=self.account, category_uid=self.category,
                feed_item_uid=str(uuid4()), direction='IN', amount_minor=100, currency='GBP',
                source='FASTER_PAYMENTS_IN', status='SETTLED', raw_payload={},
                transaction_time=datetime(2026, 4, 12, tzinfo=timezone.utc),
                source_updated_at=datetime(2026, 4, 12, tzinfo=timezone.utc)))
        self.session.commit()
        step = self.steps()['income']
        self.assertEqual(step['count'], 25)
        self.assertEqual(len(step['items']), 20)
        self.assertIn('Showing the first 20 of 25', self.client.get('/dashboard/checklist').get_data(as_text=True))

    def test_invalid_query_and_database_error_do_not_claim_checks_clear(self):
        self.assertEqual(self.client.get('/dashboard/checklist?unknown=1').status_code, 400)
        with patch('routes.checklist.current_plan', side_effect=OperationalError('hidden', {}, Exception('private'))):
            response = self.client.get('/dashboard/checklist')
        self.assertEqual(response.status_code, 503)
        html = response.get_data(as_text=True)
        self.assertIn('checklist is unavailable', html)
        self.assertNotIn('checks clear', html)
        self.assertNotIn('private', html)
