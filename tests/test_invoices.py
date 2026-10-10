"""Invoices: owner forms, immutable issue snapshots and actual settlement budgets."""
from datetime import datetime, timedelta, timezone
import re
from unittest.mock import patch
from uuid import uuid4

from werkzeug.exceptions import Conflict

from app import app
from models import IncomeShift, IncomeStream, InvoicePayment, Transaction, TransactionIncome
from services.database.connection import db
from services.invoices import workflow as work
from services.transactions.income_streams import annual_gross
from support import ApiTestCase
from deduction_support import DeductionOwnerCase


class InvoiceAuthTests(ApiTestCase):
    def test_owner_login_required_for_all_invoice_pages_and_writes(self):
        with patch.dict(app.config, SECRET_KEY='a' * 64), patch('routes.invoices.activate_test_data') as seed:
            for path in ('/dashboard/invoices', '/dashboard/invoices/new', '/dashboard/invoices/' + str(uuid4())):
                for headers in ({}, self.headers):
                    self.assertEqual(self.client.get(path, headers=headers).status_code, 302)
            self.assertEqual(self.client.post('/dashboard/invoices/new', headers=self.headers).status_code, 302)
            seed.assert_not_called()


class InvoiceWorkflowTests(DeductionOwnerCase):
    def setUp(self):
        super().setUp()
        self.bank = self.enterContext(patch('services.banking.client.http_client.send', side_effect=AssertionError('Invoices must not contact the bank')))
        self.ids = [str(uuid4()), str(uuid4())]
        for index, identity in enumerate(self.ids):
            self.session.add(IncomeShift(id=identity, income_stream_id=self.stream,
                starts_at=datetime(2026, 4, 10 + index, 8, tzinfo=timezone.utc),
                ends_at=datetime(2026, 4, 10 + index, 16, tzinfo=timezone.utc),
                unpaid_break_minutes=0, payment_mode='total', gross_minor=50000, currency='GBP', notes='Client shift', is_overtime=False))
        transaction = self.session.get(Transaction, (self.account, self.category, self.incoming))
        transaction.source='FASTER_PAYMENTS_IN'
        transaction.amount_minor=80000
        transaction.transaction_time=datetime(2026,4,12,12,tzinfo=timezone.utc)
        transaction.confirmed_at=datetime.now(timezone.utc)
        transaction.counterparty_name='Test client'
        transaction.reference='Client payment'
        transaction.income = TransactionIncome(income_stream_id=self.stream, income_type='other', tax_treatment='cis',
            gross_minor=100000, tax_deducted_minor=20000, ni_deducted_minor=0, recorded_currency='GBP',
            adjustment_minor=0, needs_review=False)
        self.session.commit()
        self.key=(self.account,self.category,self.incoming)

    def fields(self, **changes):
        values=dict(client_name='Test client',client_address='A client address',reference='Work reference',
            issue_date='2026-04-12',due_date='2026-04-20')
        values.update(changes)
        return values

    def draft(self, ids=None, **changes):
        invoice=work.save_draft(str(uuid4()),self.stream,self.fields(**changes),self.ids if ids is None else ids)
        self.session.commit()
        return work.get_invoice(invoice.id)

    def issued(self, ids=None):
        invoice=self.draft(ids)
        invoice=work.get_invoice(invoice.id,str(invoice.revision))
        work.issue(invoice)
        self.session.commit()
        return work.get_invoice(invoice.id)

    def csrf(self, path='/dashboard/invoices'):
        html=self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"',html)[1]

    def post(self, invoice, action, **values):
        path='/dashboard/invoices/'+invoice.id
        data=dict(action=action,version=str(invoice.revision),csrf_token=self.csrf(path))
        data.update(values)
        return self.client.post(path,data=data)

    def test_draft_reserves_multiple_shifts_and_does_not_add_forecast_income(self):
        before=annual_gross(self.session.get(IncomeStream,self.stream))
        invoice=self.draft()
        self.assertEqual(invoice.total_minor,100000)
        self.assertEqual(len(invoice.items),2)
        self.assertEqual(work.available_shifts(self.stream),[])
        self.assertEqual(annual_gross(self.session.get(IncomeStream,self.stream)),before)
        with self.assertRaises(Conflict): self.draft()
        self.session.rollback()

    def test_issue_numbers_are_sequential_and_cancelled_numbers_are_not_reused(self):
        first=self.issued([self.ids[0]])
        self.assertEqual(first.number,'INV-000001')
        work.cancel(work.get_invoice(first.id,str(first.revision)))
        self.session.commit()
        replacement=self.issued([self.ids[0]])
        self.assertEqual(replacement.number,'INV-000002')
        self.assertEqual(work.settlement(work.get_invoice(first.id))['label'],'Cancelled')

    def test_stale_shift_requires_resaving_draft_then_issued_snapshot_survives_edit(self):
        invoice=self.draft([self.ids[0]])
        shift=self.session.get(IncomeShift,self.ids[0])
        shift.gross_minor=60000
        self.session.commit()
        with self.assertRaises(Conflict): work.issue(work.get_invoice(invoice.id,str(invoice.revision)))
        self.session.rollback()
        invoice=work.save_draft(invoice.id,self.stream,self.fields(),[self.ids[0]],str(invoice.revision))
        self.session.commit()
        invoice=work.get_invoice(invoice.id,str(invoice.revision))
        work.issue(invoice)
        self.session.commit()
        shift.gross_minor=90000
        shift.notes='Changed later'
        self.session.commit()
        invoice=work.get_invoice(invoice.id)
        self.assertEqual(invoice.total_minor,60000)
        self.assertNotIn('Changed later',invoice.items[0].description)
        with self.assertRaises(ValueError): work.save_draft(invoice.id,self.stream,self.fields(),self.ids,str(invoice.revision))
        self.session.rollback()

    def test_cis_cash_plus_actual_deduction_settles_gross_without_new_tax_record(self):
        invoice=self.issued()
        before=self.session.scalar(db.select(db.func.count()).select_from(TransactionIncome))
        work.allocate(work.get_invoice(invoice.id,str(invoice.revision)),self.key,80000,20000,0)
        self.session.commit()
        summary=work.settlement(work.get_invoice(invoice.id))
        self.assertEqual((summary['label'],summary['credited'],summary['outstanding']),('Paid',100000,0))
        self.assertEqual(self.session.scalar(db.select(db.func.count()).select_from(TransactionIncome)),before)

    def test_one_payment_can_cover_two_invoices_without_reusing_cash_or_tax(self):
        first=self.issued([self.ids[0]])
        second=self.issued([self.ids[1]])
        work.allocate(work.get_invoice(first.id,str(first.revision)),self.key,40000,10000,0)
        self.session.commit()
        with self.assertRaises(ValueError): work.allocate(work.get_invoice(second.id,str(second.revision)),self.key,40000,10001,0)
        self.session.rollback()
        work.allocate(work.get_invoice(second.id,str(second.revision)),self.key,40000,10000,0)
        self.session.commit()
        for invoice in (first,second): self.assertEqual(work.settlement(work.get_invoice(invoice.id))['label'],'Paid')

    def test_partial_payment_and_multiple_receipts_and_overdue(self):
        invoice=self.issued()
        work.allocate(work.get_invoice(invoice.id,str(invoice.revision)),self.key,30000,0,0)
        self.session.commit()
        summary=work.settlement(work.get_invoice(invoice.id))
        self.assertEqual((summary['label'],summary['outstanding']),('Part-paid',70000))
        self.assertTrue(summary['overdue'])
        second_key=(self.account,self.category,str(uuid4()))
        stamp=datetime(2026,4,13,12,tzinfo=timezone.utc)
        second=Transaction(account_uid=second_key[0],category_uid=second_key[1],feed_item_uid=second_key[2],
            amount_minor=40000,currency='GBP',direction='IN',status='SETTLED',transaction_time=stamp,
            source_updated_at=stamp,source='FASTER_PAYMENTS_IN',confirmed_at=stamp,raw_payload={})
        second.income=TransactionIncome(income_stream_id=self.stream,income_type='other',tax_treatment='cis',
            gross_minor=50000,tax_deducted_minor=10000,ni_deducted_minor=0,adjustment_minor=0,recorded_currency='GBP',needs_review=False)
        self.session.add(second)
        self.session.commit()
        work.allocate(work.get_invoice(invoice.id,str(invoice.revision)),second_key,40000,10000,0)
        self.session.commit()
        self.assertEqual(work.settlement(work.get_invoice(invoice.id))['outstanding'],20000)
        work.allocate(work.get_invoice(invoice.id,str(invoice.revision)),self.key,20000,0,0)
        self.session.commit()
        self.assertEqual(work.settlement(work.get_invoice(invoice.id))['label'],'Paid')

    def test_actual_employee_ni_can_settle_an_invoice_without_estimation(self):
        stream=self.session.get(IncomeStream,self.stream)
        stream.kind='employed'
        income=self.session.get(Transaction,self.key).income
        income.income_type='employment'
        income.tax_treatment='paye'
        income.tax_deducted_minor=19000
        income.ni_deducted_minor=1000
        self.session.commit()
        invoice=self.issued()
        work.allocate(work.get_invoice(invoice.id,str(invoice.revision)),self.key,80000,19000,1000)
        self.session.commit()
        self.assertEqual(work.settlement(work.get_invoice(invoice.id))['label'],'Paid')

    def test_overallocation_missing_cash_and_negative_or_boolean_amounts_rejected(self):
        invoice=self.issued([self.ids[0]])
        for amounts in ((80001,0,0),(40000,20001,0),(1,0,1),(50001,0,0),(-1,0,0),(0,0,0),(True,0,0)):
            with self.subTest(amounts=amounts),self.assertRaises(ValueError):
                work.allocate(work.get_invoice(invoice.id,str(invoice.revision)),self.key,*amounts)
            self.session.rollback()

    def test_changed_bank_payment_invalidates_paid_state_and_can_be_removed(self):
        invoice=self.issued()
        allocation=work.allocate(work.get_invoice(invoice.id,str(invoice.revision)),self.key,80000,20000,0)
        self.session.commit()
        transaction=self.session.get(Transaction,self.key)
        transaction.reference='Corrected bank reference'
        self.session.commit()
        summary=work.settlement(work.get_invoice(invoice.id))
        self.assertEqual(summary['label'],'Payment needs review')
        self.assertEqual(summary['outstanding'],100000)
        with self.assertRaises(Conflict): work.allocate(work.get_invoice(invoice.id,str(invoice.revision)),self.key,1,0,0)
        self.session.rollback()
        invoice=work.get_invoice(invoice.id,str(invoice.revision))
        work.remove_payment(invoice,allocation.id)
        self.session.commit()
        self.assertEqual(work.settlement(work.get_invoice(invoice.id))['label'],'Unpaid')
        self.assertFalse(self.session.get(InvoicePayment,allocation.id).active)

    def test_unconfirmed_pending_wrong_stream_and_currency_payments_rejected(self):
        invoice=self.issued()
        for field,value in (('confirmed_at',None),('status','PENDING'),('direction','OUT'),('currency','EUR')):
            transaction=self.session.get(Transaction,self.key)
            old=getattr(transaction,field)
            setattr(transaction,field,value)
            self.session.flush()
            self.assertFalse(work.receipt_valid(transaction,invoice))
            setattr(transaction,field,old)
        transaction=self.session.get(Transaction,self.key)
        old=transaction.income.income_stream_id
        transaction.income.income_stream_id=None
        self.assertFalse(work.receipt_valid(transaction,invoice))
        transaction.income.income_stream_id=old
        transaction.income.needs_review=True
        self.assertFalse(work.receipt_valid(transaction,invoice))

    def test_foreign_future_duplicate_and_empty_shift_selection_rejected(self):
        other=str(uuid4())
        self.session.add(IncomeStream(id=other,name='Other work',kind='self_employed'))
        self.session.commit()
        shift=self.session.get(IncomeShift,self.ids[0])
        shift.income_stream_id=other
        self.session.commit()
        for ids in ([],[self.ids[1],self.ids[1]],[self.ids[0]]):
            with self.assertRaises((ValueError,Conflict)): self.draft(ids)
            self.session.rollback()
        shift=self.session.get(IncomeShift,self.ids[1])
        shift.starts_at=datetime.now(timezone.utc)+timedelta(days=1)
        shift.ends_at=shift.starts_at+timedelta(hours=8)
        self.session.commit()
        with self.assertRaises(Conflict): self.draft([self.ids[1]])
        self.session.rollback()

    def test_draft_update_releases_removed_shifts_and_checks_revision(self):
        invoice=self.draft()
        old=str(invoice.revision)
        invoice=work.save_draft(invoice.id,self.stream,self.fields(client_name='Updated'),[self.ids[0]],old)
        self.session.commit()
        self.assertEqual(invoice.total_minor,50000)
        self.assertEqual([row.id for row in work.available_shifts(self.stream)],[self.ids[1]])
        with self.assertRaises(Conflict): work.save_draft(invoice.id,self.stream,self.fields(),[self.ids[1]],old)
        self.session.rollback()

    def test_http_csrf_stale_forms_cancel_confirmation_and_payment_ownership(self):
        invoice=self.issued()
        path='/dashboard/invoices/'+invoice.id
        self.assertEqual(self.client.post(path,data=dict(action='cancel',version=invoice.revision)).status_code,400)
        self.assertEqual(self.post(invoice,'cancel').status_code,400)
        self.assertEqual(self.post(invoice,'issue',version='0').status_code,409)
        self.assertEqual(self.post(invoice,'remove_payment',payment_id=str(uuid4())).status_code,400)
        self.assertEqual(self.post(invoice,'allocate',payment='|'.join(self.key),cash='800',tax='200',ni='0').status_code,303)
        invoice=work.get_invoice(invoice.id)
        self.assertEqual(self.post(invoice,'cancel',confirm_cancel='1').status_code,400)
        payment=invoice.payments[0]
        self.assertEqual(self.post(invoice,'remove_payment',payment_id=payment.id).status_code,303)
        invoice=work.get_invoice(invoice.id)
        self.assertEqual(self.post(invoice,'cancel',confirm_cancel='1').status_code,303)

    def test_escaped_client_fields_search_validation_and_invoice_links(self):
        invoice=self.draft(client_name='<script>client</script>')
        response=self.client.get('/dashboard/invoices/'+invoice.id)
        self.assertIn('&lt;script&gt;client&lt;/script&gt;',response.get_data(as_text=True))
        self.assertNotIn('<script>client</script>',response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertIn('View invoice',self.client.get(f'/dashboard/income-streams/{self.stream}/shifts').get_data(as_text=True))
        for query in ('payment_search='+'x'*101,'payment_search=x&payment_search=y','unknown=1'):
            self.assertEqual(self.client.get('/dashboard/invoices/'+invoice.id+'?'+query).status_code,400)
        self.bank.assert_not_called()

    def test_active_invoice_prevents_shift_deletion_but_cancel_releases_it(self):
        invoice=self.draft([self.ids[0]])
        from services.transactions.shifts import shift_version
        shift=self.session.get(IncomeShift,self.ids[0])
        path=f'/dashboard/income-streams/{self.stream}/shifts?edit={shift.id}'
        form=dict(action='delete',shift_id=shift.id,version=shift_version(shift),csrf_token=self.csrf(path))
        self.assertEqual(self.client.post(path,data=form).status_code,400)
        work.cancel(work.get_invoice(invoice.id,str(invoice.revision)))
        self.session.commit()
        form['csrf_token']=self.csrf(path)
        self.assertEqual(self.client.post(path,data=form).status_code,303)
        self.session.expire_all()
        self.assertIsNone(work.get_invoice(invoice.id).items[0].shift_id)

    def test_currency_mismatch_future_issue_and_invalid_dates(self):
        shift=self.session.get(IncomeShift,self.ids[1])
        shift.currency='EUR'
        self.session.commit()
        with self.assertRaises(ValueError): self.draft()
        self.session.rollback()
        for changes in (dict(issue_date='invalid'),dict(due_date='2026-04-01'),dict(client_name='')):
            with self.assertRaises(ValueError): self.draft([self.ids[0]],**changes)
            self.session.rollback()
        tomorrow=(work.today()+timedelta(days=1)).isoformat()
        invoice=self.draft([self.ids[0]],issue_date=tomorrow,due_date=tomorrow)
        with self.assertRaises(ValueError): work.issue(work.get_invoice(invoice.id,str(invoice.revision)))
        self.session.rollback()
