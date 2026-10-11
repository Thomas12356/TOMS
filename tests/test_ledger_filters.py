"""Literal search, London dates and consistent saved-record attention filters."""
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from models import Transaction, TransactionIncome
from services.database.connection import db
from services.transactions.ledger import filtered_transactions
from services.transactions.classification import save_classification
from deduction_support import DeductionOwnerCase


class LedgerFilterTests(DeductionOwnerCase):
    def test_search_is_literal_case_insensitive_and_escapes_html(self):
        self.payment().counterparty_name = '<script>Alpha %_\\ Client</script>'
        self.payment().reference = 'Invoice X-123'
        self.session.commit()
        for query, count in (('alpha', 1), ('%_', 1), ('\\', 1), ('x-123', 1), ("' OR 1=1 --", 0)):
            result = self.client.get('/dashboard', query_string={'q': query})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(self.session.scalars(filtered_transactions(db.select(Transaction), {'q': query})).all()), count)
        html = self.client.get('/dashboard?q=alpha').get_data(as_text=True)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>Alpha', html)

    def test_london_date_boundaries_and_classification_override(self):
        self.payment().transaction_time = datetime(2026, 7, 1, 23, 30, tzinfo=timezone.utc)
        self.session.commit()
        def ids(options):
            return {row.feed_item_uid for row in self.session.scalars(filtered_transactions(db.select(Transaction), options))}
        self.assertIn(self.outgoing, ids({'from': '2026-07-02', 'to': '2026-07-02'}))
        self.assertNotIn(self.outgoing, ids({'to': '2026-07-01'}))
        self.assertNotIn(self.outgoing, ids({'classification': 'internal_transfer'}))
        save_classification(self.payment(), 'internal_transfer', None)
        self.session.commit()
        self.assertIn(self.outgoing, ids({'classification': 'internal_transfer'}))

    def test_attention_distinguishes_confirmation_and_income_completeness(self):
        receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        save_classification(receipt, 'income', None)
        self.payment().confirmed_at = receipt.confirmed_at = datetime.now(timezone.utc)
        self.session.commit()
        def ids():
            return {row.feed_item_uid for row in self.session.scalars(filtered_transactions(db.select(Transaction), {'attention': 'needs_review'}))}
        self.assertEqual(ids(), {self.incoming})
        receipt.income = TransactionIncome(income_type='personal_gift', tax_treatment='non_taxable',
            recorded_currency='GBP', needs_review=False, adjustment_minor=0)
        self.session.commit()
        self.assertEqual(ids(), set())
        receipt.income.needs_review = True
        self.session.commit()
        self.assertEqual(ids(), {self.incoming})
        receipt.status = 'PENDING'
        self.session.commit()
        self.assertEqual(ids(), set())

    def test_changed_expense_stays_in_attention_even_after_confirmation(self):
        self.assertEqual(self.expense().status_code, 303)
        self.payment().confirmed_at = datetime.now(timezone.utc)
        self.session.commit()
        query = filtered_transactions(db.select(Transaction), {'attention': 'needs_review'})
        self.assertNotIn(self.outgoing, {row.feed_item_uid for row in self.session.scalars(query)})
        self.payment().amount_minor += 1
        self.session.commit()
        self.assertIn(self.outgoing, {row.feed_item_uid for row in self.session.scalars(query)})

    def test_validation_runs_before_ledger_query_and_blank_form_fields_are_allowed(self):
        with patch.object(db, 'paginate') as read:
            for values in ({'q': 'x'*201}, {'from': '2026-02-30'}, {'from': '2026-10-12', 'to': '2026-10-11'},
                           {'classification': 'bad'}, {'attention': 'bad'}, [('q', 'a'), ('q', 'b')]):
                self.assertEqual(self.client.get('/dashboard', query_string=values).status_code, 400)
            read.assert_not_called()
        self.assertEqual(self.client.get('/dashboard?q=&from=&to=&attention=all&classification=all').status_code, 200)

    def test_pagination_and_existing_edits_preserve_filters(self):
        now = datetime.now(timezone.utc)
        for index in range(51):
            self.session.add(Transaction(account_uid=self.account, category_uid=self.category, feed_item_uid=str(uuid4()),
                amount_minor=100, currency='GBP', direction='OUT', status='SETTLED', transaction_time=now,
                source_updated_at=now, counterparty_name='Filtered vendor', raw_payload={}))
        self.session.commit()
        context = {'q': 'Filtered vendor', 'classification': 'expense', 'attention': 'unconfirmed', 'page': '1'}
        html = self.client.get('/dashboard', query_string=context).get_data(as_text=True)
        self.assertIn('page=2', html)
        self.assertIn('q=Filtered+vendor', html)
        self.assertIn('classification=expense', html)
        self.assertIn('attention=unconfirmed', html)
        context['q'] = self.payment().counterparty_name or ''
        path = f'/dashboard/transactions/{self.account}/{self.category}/{self.outgoing}/classification'
        result = self.client.post(path, query_string={'q': 'Vendor', 'attention': 'unconfirmed', 'page': '2'},
            data={'csrf_token': self.csrf_for(path), 'action': 'save', 'type': 'expense', 'notes': 'Reviewed'})
        self.assertEqual(result.status_code, 303)
        self.assertIn('q=Vendor', result.headers['Location'])
        self.assertIn('page=2', result.headers['Location'])

    def csrf_for(self, path):
        import re
        return re.search(r'name="csrf_token" value="([^"]+)"', self.client.get(path).get_data(as_text=True))[1]
