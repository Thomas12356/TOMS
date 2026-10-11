"""One owner journey, explicit confirmation and stale financial-evidence checks."""
import re
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app import app
from models import Transaction
from services.transactions.guided_review import version
from deduction_support import DeductionOwnerCase
from support import ApiTestCase


class GuidedReviewAuthTests(ApiTestCase):
    def test_owner_required_before_reading_or_selecting_records(self):
        with patch('routes.guided_review.query') as read:
            for headers in ({}, self.headers):
                self.assertEqual(self.client.get('/dashboard/review/start', headers=headers).status_code, 302)
            read.assert_not_called()


class GuidedReviewTests(DeductionOwnerCase):
    def path(self, item=None):
        return f'/dashboard/transactions/{self.account}/{self.category}/{item or self.incoming}/review'

    def form(self, item=None, step='classification'):
        response = self.client.get(self.path(item), query_string={'step': step})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:200])
        html = response.get_data(as_text=True)
        return {'csrf_token': re.search(r'name="csrf_token" value="([^"]+)"', html)[1],
                'version': re.search(r'name="version" value="([^"]+)"', html)[1]}

    def post(self, action, item=None, step='classification', form=None, **fields):
        return self.client.post(self.path(item), query_string={'step': step, 'q': 'filtered', 'page': '2'},
            data={**(form or self.form(item, step)), 'action': action, **fields})

    def test_income_classification_details_and_confirmation_are_separate_evidence_steps(self):
        receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        self.assertEqual(self.post('classify', type='income', notes='Client receipt').status_code, 303)
        self.assertIsNone(receipt.confirmed_at)
        result = self.post('details', step='details', income_stream_id=self.stream, tax_treatment='no_tax_deducted',
            gross='25.00', tax_deducted='0', ni_deducted='', adjustment='', adjustment_notes='', source_name='Client')
        self.assertEqual(result.status_code, 303, result.get_data(as_text=True)[:300])
        self.assertIn('step=check', result.headers['Location'])
        self.assertIn('q=filtered', result.headers['Location'])
        self.assertIsNone(receipt.confirmed_at)
        html = self.client.get(self.path(), query_string={'step': 'check'}).get_data(as_text=True)
        self.assertIn('GBP 25.00', html)
        self.assertIn('No tax deducted', html)
        result = self.post('confirm', step='check')
        self.assertEqual(result.status_code, 303)
        self.assertIn('/review/start?', result.headers['Location'])
        self.assertIsNotNone(receipt.confirmed_at)
        self.assertEqual(receipt.income.gross_minor, 2500)

    def test_currency_changed_expense_requires_reentry_instead_of_conversion(self):
        self.assertEqual(self.expense().status_code, 303)
        self.payment().currency = 'EUR'
        self.session.commit()
        html = self.client.get(self.path(self.outgoing), query_string={'step': 'details'}).get_data(as_text=True)
        self.assertIn('no conversion has been applied', html)
        self.assertIn('id="expense-amount" name="amount" value=""', html)

    def test_non_taxable_receipt_can_be_completed_without_inventing_a_stream_or_amount(self):
        self.post('classify', type='income', notes='Gift')
        result = self.post('details', step='details', income_stream_id='', income_type='personal_gift',
            tax_treatment='non_taxable', gross='', tax_deducted='', ni_deducted='', adjustment='', adjustment_notes='')
        self.assertEqual(result.status_code, 303)
        receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        self.assertIsNone(receipt.income.gross_minor)
        self.assertIsNone(receipt.income.income_stream_id)
        self.assertEqual(receipt.income.income_type, 'personal_gift')

    def test_expense_tracking_and_tax_claim_validation_reuse_existing_rules(self):
        result = self.post('details', item=self.outgoing, step='details', stream_id=self.stream,
            amount='5.00', purpose='Tools', category='general', tax_status='deduction')
        self.assertEqual(result.status_code, 400)
        self.assertIn('eligible, unreimbursed', result.get_data(as_text=True))
        self.assertIsNone(self.payment().expense)
        result = self.post('details', item=self.outgoing, step='details', stream_id=self.stream,
            amount='5.00', purpose='Tools', category='general', tax_status='tracking')
        self.assertEqual(result.status_code, 303)
        self.assertFalse(self.payment().expense.is_tax_deductible)
        self.assertEqual(self.payment().expense.amount_minor, 500)
        self.assertEqual(self.post('confirm', item=self.outgoing, step='check').status_code, 303)

    def test_stale_forms_retain_entries_without_issuing_a_new_overwrite_token(self):
        old = self.form()
        receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        receipt.reference = 'Changed at bank'
        self.session.commit()
        result = self.post('classify', form=old, type='income', notes='Retain my notes')
        self.assertEqual(result.status_code, 409)
        html = result.get_data(as_text=True)
        self.assertIn('Retain my notes', html)
        self.assertIn(old['version'], html)
        self.assertNotEqual(old['version'], version(receipt))
        self.assertIsNone(receipt.classification)
        self.assertIsNone(receipt.confirmed_at)

    def test_confirmation_covers_expense_changes_and_requires_the_check_step(self):
        self.assertEqual(self.expense().status_code, 303)
        old = self.form(self.outgoing, 'check')
        self.payment().expense.purpose = 'Updated purpose'
        self.session.commit()
        self.assertEqual(self.post('confirm', item=self.outgoing, step='check', form=old).status_code, 409)
        self.assertIsNone(self.payment().confirmed_at)
        self.assertEqual(self.post('confirm', item=self.outgoing).status_code, 400)

    def test_skip_pending_details_and_transfer_review_do_not_create_income(self):
        receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        receipt.status = 'PENDING'
        self.session.commit()
        self.assertEqual(self.post('skip', step='details').status_code, 303)
        html = self.client.get(self.path(), query_string={'step': 'check'}).get_data(as_text=True)
        self.assertIn('has not settled', html)
        self.assertEqual(self.post('confirm', step='check').status_code, 303)
        self.assertIsNone(receipt.income)

    def test_empty_filtered_queue_and_form_guards(self):
        response = self.client.get('/dashboard/review/start?q=no-such-payer&classification=expense&page=2')
        self.assertEqual(response.status_code, 200)
        self.assertIn('No transactions awaiting', response.get_data(as_text=True))
        self.assertIn('q=no-such-payer', response.get_data(as_text=True))
        self.assertEqual(self.client.post(self.path(), data={'action': 'confirm', 'version': 'a'*64}).status_code, 400)
        self.assertEqual(self.post('classify', type='income', notes='', surprise='bad').status_code, 400)
        self.assertEqual(self.client.get(self.path() + '?step=invalid').status_code, 400)

    def test_commit_failure_rolls_back_evidence_and_hides_private_errors(self):
        old = self.form()
        with patch.object(self.session, 'commit', side_effect=OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-SECRET'))):
            response = self.post('classify', form=old, type='income', notes='Proposed')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('PRIVATE-', response.get_data(as_text=True))
        self.assertIsNone(self.session.get(Transaction, (self.account, self.category, self.incoming)).classification)
