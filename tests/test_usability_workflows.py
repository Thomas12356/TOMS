"""Account money, reusable entry defaults and deliberate payment suggestions."""
from contextlib import contextmanager
from datetime import datetime, timezone
import re
from unittest.mock import patch
from uuid import uuid4

from app import app
from deduction_support import DeductionOwnerCase
from models import IncomeShift, IncomeStream, MileageEntry, Transaction, TransactionClassification, TransactionIncome
from services.database.connection import db
from services.transactions.suggestions import payment_suggestions
from sqlalchemy.exc import OperationalError
from support import ApiTestCase


class AccountMoneyAuthTests(ApiTestCase):
    def test_api_keys_cannot_open_account_money(self):
        with patch.dict(app.config, SECRET_KEY='a' * 64):
            for headers in ({}, self.headers):
                self.assertEqual(self.client.get('/dashboard/account-money', headers=headers).status_code, 302)


class UsabilityWorkflowTests(DeductionOwnerCase):
    def test_account_money_errors_do_not_render_a_misleading_financial_summary(self):
        with patch('services.tax.export.export_session', side_effect=OperationalError('query', {}, Exception('private payload'))):
            response = self.client.get('/dashboard/account-money')
        self.assertEqual(response.status_code, 503)
        html = response.get_data(as_text=True)
        self.assertIn('Account money is unavailable', html)
        self.assertNotIn('private payload', html)
        self.assertNotIn('Still to earmark', html)
        self.assertEqual(self.client.get('/dashboard/account-money?unexpected=1').status_code, 400)

    def setUp(self):
        super().setUp()
        @contextmanager
        def snapshot():
            yield self.session
        self.enterContext(patch('services.tax.export.export_session', snapshot))
        self.enterContext(patch('services.tax.reserves.cached_status', return_value={'status': 'verified'}))
        self.enterContext(patch('services.tax.ni_rules.cached_status', return_value={'status': 'verified'}))
        self.receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        self.receipt.counterparty_name = 'Known client'
        self.receipt.reference = 'Monthly statement'
        self.receipt.source = 'FASTER_PAYMENTS_IN'
        self.receipt.transaction_time = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.session.commit()

    def token(self, path):
        html = self.client.get(path).get_data(as_text=True)
        return dict(csrf_token=re.search(r'name="csrf_token" value="([^"]+)"', html)[1],
                    version=re.search(r'name="version" value="([^"]+)"', html)[1])

    def review(self, step='classification'):
        return f'/dashboard/transactions/{self.account}/{self.category}/{self.incoming}/review?step={step}'

    def peers(self, count=2, kind='income', source=None, **changes):
        result = []
        for index in range(count):
            values = dict(account_uid=self.account, category_uid=self.category, feed_item_uid=str(uuid4()),
                amount_minor=2500, currency='GBP', direction='IN', status='SETTLED', source='FASTER_PAYMENTS_IN',
                transaction_time=datetime(2026, 5, index + 1, tzinfo=timezone.utc),
                source_updated_at=datetime(2026, 5, index + 1, tzinfo=timezone.utc),
                confirmed_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
                counterparty_name='Known client', reference='Monthly statement', raw_payload={})
            values.update(changes)
            record = Transaction(**values, classification=TransactionClassification(type=kind))
            if source:
                record.income = TransactionIncome(income_stream_id=source, income_type='other',
                    gross_minor=2500, tax_treatment='no_tax_deducted', recorded_currency='GBP',
                    adjustment_minor=0, needs_review=False)
            self.session.add(record)
            result.append(record)
        self.session.commit()
        return result

    def test_account_money_reads_saved_state_without_bank_or_rule_refresh(self):
        with patch('services.tax.rules.start_rule_check') as rules:
            response = self.client.get('/dashboard/account-money')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        html = response.get_data(as_text=True)
        self.assertIn('data-manual="true"', html)
        self.assertIn('not an available-to-spend figure', html)
        self.assertIn('not a bank balance timestamp', html)
        self.assertNotIn('Loading account balances', html)
        self.bank.assert_not_called()
        rules.assert_not_called()
        index = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        self.assertNotIn('id="account-balances"', index)
        self.assertIn('Open Account money', index)

    def test_earmarks_redirect_to_account_money_and_keep_stale_version_checks(self):
        data = dict(csrf_token=self.token(self.review())['csrf_token'], account=self.account, version='0', tax='10', ni='2')
        response = self.client.post('/dashboard/account-reserves', data=data)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, '/dashboard/account-money#account-reserves')
        self.assertEqual(self.client.post('/dashboard/account-reserves', data=data).status_code, 409)

    def test_empty_reference_or_one_peer_does_not_generate_suggestions(self):
        self.peers(1)
        self.assertEqual(payment_suggestions(self.receipt), {})
        self.peers(1)
        self.receipt.reference = ''
        self.assertEqual(payment_suggestions(self.receipt), {})

    def test_classification_preview_is_explicit_and_does_not_write_or_confirm(self):
        self.peers(kind='refund')
        suggestion = payment_suggestions(self.receipt)['classification']
        self.assertEqual(suggestion['value'], 'refund')
        response = self.client.post(self.review(), data={**self.token(self.review()),
            'action': 'preview_classification', 'suggestion_version': suggestion['version']})
        self.assertEqual(response.status_code, 200)
        self.assertIn('value="refund" selected', response.get_data(as_text=True))
        self.assertIsNone(self.receipt.classification)
        self.assertIsNone(self.receipt.confirmed_at)
        self.assertIsNone(self.receipt.income)
        saved = self.client.post(self.review(), data={**self.token(self.review()), 'action': 'classify', 'type': 'refund', 'notes': ''})
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(self.receipt.classification.type, 'refund')
        self.assertIsNone(self.receipt.confirmed_at)

    def test_ambiguous_classifications_and_automatic_only_choices_are_suppressed(self):
        peers = self.peers(kind='refund')
        self.peers(1, kind='income')
        self.assertNotIn('classification', payment_suggestions(self.receipt))
        for peer in peers:
            peer.classification = None
        self.session.commit()
        self.assertNotIn('classification', payment_suggestions(self.receipt))

    def test_cross_currency_unconfirmed_and_future_history_are_not_learned(self):
        self.peers(currency='EUR')
        self.peers(confirmed_at=None)
        self.peers(transaction_time=datetime(2027, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(payment_suggestions(self.receipt), {})

    def test_suggestions_do_not_replace_existing_manual_choices(self):
        self.peers(kind='refund')
        self.receipt.classification = TransactionClassification(type='income')
        self.session.commit()
        self.assertNotIn('classification', payment_suggestions(self.receipt))
        self.receipt.income = TransactionIncome(income_type='personal_gift', tax_treatment='non_taxable',
            recorded_currency='GBP', adjustment_minor=0, needs_review=False)
        self.session.commit()
        self.assertNotIn('source', payment_suggestions(self.receipt))

    def test_source_preview_keeps_amounts_and_withholding_unknown_until_saved(self):
        self.peers(source=self.stream)
        suggestion = payment_suggestions(self.receipt)['source']
        path = self.review('details')
        response = self.client.post(path, data={**self.token(path), 'action': 'preview_source',
                                              'suggestion_version': suggestion['version']})
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'value="{self.stream}" selected', response.get_data(as_text=True))
        self.assertIsNone(self.receipt.income)
        self.assertIsNone(self.receipt.confirmed_at)
        saved = self.client.post(path, data={**self.token(path), 'action': 'details',
            'income_stream_id': self.stream, 'tax_treatment': 'no_tax_deducted', 'gross': '25',
            'tax_deducted': '0', 'ni_deducted': '', 'adjustment': '', 'adjustment_notes': '', 'source_name': ''})
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(self.receipt.income.income_stream_id, self.stream)
        self.assertIsNone(self.receipt.confirmed_at)

    def test_ambiguous_or_archived_sources_remain_unassigned(self):
        self.peers(source=self.stream)
        stream = self.session.get(IncomeStream, self.stream)
        stream.archived = True
        self.session.commit()
        self.assertNotIn('source', payment_suggestions(self.receipt))
        stream.archived = False
        other = IncomeStream(id=str(uuid4()), name='Other client', kind='self_employed')
        self.session.add(other)
        self.session.commit()
        for peer in self.peers(source=other.id):
            peer.income.gross_minor = None
            peer.income.tax_treatment = 'unknown'
        self.session.commit()
        self.assertNotIn('source', payment_suggestions(self.receipt))

    def test_changed_suggestion_and_changed_payment_are_rejected(self):
        peers = self.peers(kind='refund')
        suggestion = payment_suggestions(self.receipt)['classification']
        token = self.token(self.review())
        peers[0].classification.type = 'income'
        self.session.commit()
        data = {**token, 'action': 'preview_classification', 'suggestion_version': suggestion['version']}
        self.assertEqual(self.client.post(self.review(), data=data).status_code, 409)
        self.receipt.amount_minor += 1
        self.session.commit()
        self.assertEqual(self.client.post(self.review(), data=data).status_code, 409)
        self.assertIsNone(self.receipt.classification)

    def test_work_defaults_leave_dates_totals_and_existing_record_unchanged(self):
        record = IncomeShift(id=str(uuid4()), income_stream_id=self.stream, currency='GBP',
            starts_at=datetime(2026, 5, 1, 8, tzinfo=timezone.utc), ends_at=datetime(2026, 5, 1, 16, tzinfo=timezone.utc),
            unpaid_break_minutes=30, payment_mode='hourly', hourly_rate_minor=2000, gross_minor=15000,
            notes='Old block reference', is_overtime=True)
        self.session.add(record)
        self.session.commit()
        before = record.as_dict()
        response = self.client.get(f'/dashboard/income-streams/{self.stream}/shifts?reuse={record.id}')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('name="hourly_rate" inputmode="decimal" maxlength="30" value="20.00"', html)
        self.assertIn('name="starts_at" value=""', html)
        self.assertIn('name="total_payment" inputmode="decimal" maxlength="30" value=""', html)
        self.assertNotEqual(re.search(r'name="shift_id" value="([^"]+)"', html)[1], record.id)
        self.assertEqual(before, record.as_dict())
        other = IncomeStream(id=str(uuid4()), name='Other work', kind='self_employed')
        self.session.add(other)
        self.session.commit()
        self.assertEqual(self.client.get(f'/dashboard/income-streams/{other.id}/shifts?reuse={record.id}').status_code, 400)

    def test_mileage_defaults_require_new_actuals_and_eligibility(self):
        self.assertEqual(self.mileage().status_code, 303)
        record = self.session.scalar(db.select(MileageEntry).where(MileageEntry.income_stream_id == self.stream))
        before = record.as_dict()
        response = self.client.get('/dashboard/deductions?type=mileage&reuse=' + record.id)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(f'value="{record.vehicle_key}"', html)
        self.assertIn('name="reimbursed" inputmode="decimal" maxlength="30" value=""', html)
        self.assertNotIn('name="eligible" value="1" required checked', html)
        self.assertNotEqual(re.search(r'name="entry_id" value="([^"]+)"', html)[1], record.id)
        self.assertEqual(before, record.as_dict())
        self.assertEqual(self.client.get('/dashboard/deductions?type=expense&reuse=' + record.id).status_code, 400)
