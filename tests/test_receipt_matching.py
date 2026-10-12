"""Guided previews do not write; confirmation retains allocation safeguards."""
import re
from unittest.mock import patch

from models import InvoicePayment
from services.invoices import workflow as work
import test_reconciliation


class ReceiptMatchingTests(test_reconciliation.ReconciliationTests):
    # Reuse setup/helpers, without rerunning the inherited reconciliation tests.
    def setUp(self):
        super().setUp()
        self.match_url = '/dashboard/reconciliation/payment/' + '/'.join(work.payment_key(self.receipt))

    def fields(self, response):
        return dict(re.findall(r'name="(csrf_token|preview)" value="([^"]+)"', response.get_data(as_text=True)))

    def preview(self, invoice, **changes):
        page = self.client.get(self.match_url)
        data = dict(action='preview', invoice=invoice.id, cash='800', tax='200', ni='0', **self.fields(page))
        data.update(changes)
        return self.client.post(self.match_url, data=data)

    def confirm(self, preview):
        return self.client.post(self.match_url, data=dict(action='confirm', **self.fields(preview)))

    def count(self):
        return self.session.query(InvoicePayment).count()

    def test_preview_is_read_only_confirm_once_and_retains_tax_total(self):
        invoice = self.invoice()
        before = self.count()
        response = self.preview(invoice)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Check this match', response.get_data(as_text=True))
        self.assertEqual(self.count(), before)
        confirmed = self.confirm(response)
        self.assertEqual(confirmed.status_code, 303)
        self.assertEqual(self.count(), before + 1)
        self.assertEqual(work.settlement(work.get_invoice(invoice.id))['outstanding'], 0)
        self.assertEqual(self.confirm(response).status_code, 409)
        self.assertEqual(self.count(), before + 1)
        self.bank.assert_not_called()

    def test_partial_match_leaves_separate_cash_and_withholding_budgets(self):
        invoice = self.invoice()
        response = self.preview(invoice, cash='400', tax='100')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.confirm(response).status_code, 303)
        self.assertEqual(work.remaining_receipt(self.receipt), dict(cash=40000, tax=10000, ni=0))
        self.assertEqual(work.settlement(work.get_invoice(invoice.id))['outstanding'], 50000)
        page = self.client.get(self.match_url)
        self.assertIn('Remaining to match', page.get_data(as_text=True))

    def test_changed_payment_and_changed_invoice_reject_confirmation(self):
        invoice = self.invoice()
        preview = self.preview(invoice)
        self.receipt.reference = 'Changed after preview'
        self.session.commit()
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertEqual(self.count(), 0)
        preview = self.preview(invoice)
        work.get_invoice(invoice.id).revision += 1
        self.session.commit()
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertEqual(self.count(), 0)

    def test_confirmation_requires_signed_preview_and_csrf(self):
        invoice = self.invoice()
        response = self.preview(invoice)
        fields = self.fields(response)
        self.assertEqual(self.client.post(self.match_url, data=dict(action='confirm', preview=fields['preview'])).status_code, 400)
        fields['preview'] += 'tampered'
        self.assertEqual(self.client.post(self.match_url, data=dict(action='confirm', **fields)).status_code, 409)
        self.assertEqual(self.count(), 0)

    def test_expired_or_other_data_mode_preview_is_rejected(self):
        invoice = self.invoice()
        response = self.preview(invoice)
        from app import app
        from itsdangerous import URLSafeTimedSerializer
        signer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='receipt-match-preview-v1')
        fields = self.fields(response)
        saved = signer.loads(fields['preview'])
        saved['sample'] = not saved['sample']
        fields['preview'] = signer.dumps(saved)
        self.assertEqual(self.client.post(self.match_url, data=dict(action='confirm', **fields)).status_code, 409)
        from itsdangerous import SignatureExpired
        with patch('routes.reconciliation.URLSafeTimedSerializer') as serializer:
            serializer.return_value.loads.side_effect = SignatureExpired('expired')
            self.assertEqual(self.confirm(response).status_code, 409)
        self.assertEqual(self.count(), 0)

    def test_invalid_receipt_and_excessive_budget_cannot_preview(self):
        invoice = self.invoice()
        for changes in (dict(cash='801'), dict(tax='201'), dict(cash='-1'), dict(cash='0', tax='0')):
            self.assertEqual(self.preview(invoice, **changes).status_code, 400)
        self.receipt.confirmed_at = None
        self.session.commit()
        self.assertEqual(self.preview(invoice).status_code, 400)
        self.assertIn('Review payment details', self.client.get(self.match_url).get_data(as_text=True))
        self.assertEqual(self.count(), 0)

    def test_suggestions_explain_reference_and_amount_search_escapes_text(self):
        invoice = self.invoice()
        self.receipt.reference = invoice.number
        invoice.client_name = '<script>Client</script>'
        self.session.commit()
        response = self.client.get(self.match_url)
        html = response.get_data(as_text=True)
        self.assertIn('Payment reference matches', html)
        self.assertIn('Outstanding amount equals', html)
        self.assertIn('&lt;script&gt;Client&lt;/script&gt;', html)
        self.assertNotIn('<script>Client</script>', html)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertNotIn(invoice.number, self.client.get(self.match_url + '?q=no-such-invoice').get_data(as_text=True).split('Choose an invoice')[1])
        self.assertEqual(self.client.get(self.match_url + '?q=a&q=b').status_code, 400)

    def test_owner_required_on_get_and_post(self):
        self.client.get('/logout')
        with self.client.session_transaction() as cookie:
            cookie.clear()
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(self.match_url).status_code, 302)

    def test_inconsistent_gross_and_wrong_currency_or_source_are_rejected(self):
        invoice = self.invoice()
        self.receipt.income.gross_minor += 100
        self.session.commit()
        self.assertEqual(self.preview(invoice).status_code, 400)
        self.assertIn('Review payment details', self.client.get(self.match_url).get_data(as_text=True))
        self.receipt.income.gross_minor -= 100
        self.receipt.currency = self.receipt.income.recorded_currency = 'EUR'
        self.session.commit()
        self.assertEqual(self.preview(invoice).status_code, 400)
        self.receipt.currency = self.receipt.income.recorded_currency = 'GBP'
        self.receipt.income.income_stream_id = None
        self.session.commit()
        self.assertEqual(self.preview(invoice).status_code, 400)
        self.assertEqual(self.count(), 0)


# These are fixture inheritance helpers, not additional test executions.
for name in tuple(dir(test_reconciliation.ReconciliationTests)):
    if name.startswith('test_') and name not in ReceiptMatchingTests.__dict__:
        setattr(ReceiptMatchingTests, name, None)
