"""Independent cash budgeting cases and protected owner form workflows."""
from datetime import date, datetime, timedelta, timezone
import io
import re
from types import SimpleNamespace
from unittest.mock import patch, Mock
from uuid import uuid4
from zipfile import ZipFile
from contextlib import nullcontext
from werkzeug.exceptions import Conflict
import unittest
import os

from models import Account, AccountReserve, CashBalance, CashCommitment, CashCoverage, CashPayment
from services import cash_planning as cash
from services.cash_export import package
from services.invoices.workflow import LOCAL_TIME
import test_test_data as sample_support
from services.web.test_data import sample_id
from services.database.connection import db


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS')=='1','Requires PostgreSQL.')
class CashPlanningTests(unittest.TestCase):
    drop_schemas=sample_support.TestDataTests.drop_schemas
    def setUp(self):
        sample_support.TestDataTests.setUp(self)
        self.session=db.session
        self.account=sample_id(1)
        self.now = datetime.now(timezone.utc)
        self.today = self.now.astimezone(LOCAL_TIME).date()
        self.enterContext(patch('routes.cash_planning.current_plan',return_value=dict(
            tax_year='2026-27',received_reserve_minor=30000,blockers=[],received_issues=[])))
        self.enterContext(patch('routes.cash_planning.export_session',side_effect=lambda:nullcontext(self.session)))

    def form(self,url='/dashboard/cash-planning'):
        response = self.client.get(url)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True)[:250])
        html=response.get_data(as_text=True)
        return dict(re.findall(r'name="(csrf_token|identity|version|fingerprint)" value="([^"]*)"',html))

    def add(self,**changes):
        form=dict(kind='bill',name='Rent',currency='GBP',amount='100',due_date=str(self.today),
            recurrence='once',end_date='',tax_year='',tax_component='',notes='')
        form.update(changes)
        if form['kind']=='hmrc' and form.get('tax_component')!='other':
            form.setdefault('supported_charge_confirmed','1')
        identity=str(uuid4())
        row=cash.save_commitment(identity,form)
        self.session.commit()
        return row

    def balance(self,value=100000):
        observed_payments=cash.payment_version(self.session,'GBP')
        row=self.session.get(CashBalance,self.account)
        if row is None:
            row=CashBalance(account_uid=self.account,revision=1)
            self.session.add(row)
        else:
            row.revision+=1
        row.amount_minor=value
        row.recorded_currency='GBP'
        row.payment_fingerprint=observed_payments
        row.observed_at=row.recorded_at=datetime.now(timezone.utc)
        self.session.commit()
        return row

    def result(self,target=30000,**changes):
        return cash.snapshot(self.session,tax_target=target,**changes)

    def complete(self):
        fingerprint=self.result()['fingerprint']
        row=self.session.get(CashCoverage,1)
        if row is None:
            row=CashCoverage(id=1)
            self.session.add(row)
        row.fingerprint=fingerprint
        row.confirmed_at=datetime.now(timezone.utc)
        self.session.commit()

    def pay(self,item,value='50',**changes):
        form=dict(payment_id=str(uuid4()),occurrence_date=str(item.due_date),paid_on=str(self.today),amount=value,reference='Direct payment')
        if item.kind=='hmrc' and item.tax_year=='2026-27' and item.tax_component!='other':
            form['tax_credit']=value
        form.update(changes)
        cash.record_payment(cash.commitment(item.id,str(item.revision)),form)
        self.session.commit()
        return form

    def test_correction_after_clock_regression_still_requires_new_balances(self):
        item=self.add()
        form=self.pay(item)
        balance=self.balance()
        from models import CashPayment
        payment=self.session.get(CashPayment,form['payment_id'])
        payment.active=False
        payment.changed_at=balance.recorded_at-timedelta(seconds=2)
        self.session.commit()
        self.complete()
        result=self.result()
        task=next(row for row in result['balance_tasks'] if row['account'].account_uid==self.account)
        self.assertIn('Check balance after payment or correction',task['reasons'])
        self.assertIsNone(result['groups'][0]['available'])
        self.balance()
        self.complete()
        self.assertFalse(any(row['account'].account_uid==self.account for row in self.result()['balance_tasks']))

    def test_month_end_and_leap_year_anchor_dates_are_preserved(self):
        item=SimpleNamespace(active=True,due_date=date(2026,1,31),end_date=None,recurrence='monthly')
        self.assertEqual(list(cash.occurrences(item,date(2026,3,31))),[date(2026,1,31),date(2026,2,28),date(2026,3,31)])
        item.due_date,item.recurrence=date(2024,2,29),'yearly'
        self.assertEqual(list(cash.occurrences(item,date(2028,2,29)))[-1],date(2028,2,29))
        item.recurrence,item.due_date,item.end_date='weekly',self.today,self.today+timedelta(days=14)
        self.assertEqual(len(list(cash.occurrences(item,self.today+timedelta(days=30)))),3)
        item.due_date, item.end_date = date.max, None
        for recurrence in ('weekly', 'monthly', 'yearly'):
            item.recurrence = recurrence
            self.assertEqual(list(cash.occurrences(item, date.max)), [date.max])

    def test_unknown_stale_missing_balance_and_unconfirmed_coverage_withhold_figure(self):
        self.add(amount='')
        self.assertIsNone(self.result()['groups'][0]['available'])
        balance=self.balance()
        self.complete()
        self.assertIsNone(self.result()['groups'][0]['available'])
        item=self.session.scalar(cash.db.select(CashCommitment))
        item.amount_minor=10000
        self.session.commit()
        self.assertFalse(self.result()['complete'])
        balance.observed_at=datetime.now(timezone.utc)-timedelta(hours=25)
        self.session.commit()
        self.complete()
        self.assertIsNone(self.result()['groups'][0]['available'])
        self.balance()
        self.complete()
        self.assertIsNone(self.result(target=None)['groups'][0]['available'])

    def test_bill_tax_target_and_earmarks_are_not_double_counted(self):
        self.add()
        self.balance()
        self.session.add(AccountReserve(account_uid=self.account,tax_minor=20000,ni_minor=15000,revision=1))
        self.session.commit()
        self.complete()
        row=self.result()['groups'][0]
        self.assertEqual(row['protected'],35000)
        self.assertEqual(row['available'],55000,row['reasons'])  # 1000 cash - 100 bill - 350 provision

    def test_previous_year_bill_is_added_to_current_year_tax_provision(self):
        self.add(kind='hmrc',tax_year='2025-26',tax_component='balancing',statement_confirmed='1',amount='200')
        self.add(kind='hmrc',tax_year='2026-27',tax_component='first_on_account',statement_confirmed='1',amount='250')
        self.balance()
        self.complete()
        row=self.result()['groups'][0]
        self.assertEqual(row['hmrc'],45000)
        self.assertEqual(row['protected'],50000)  # max(300 target,250 current bill)+200 old bill
        self.assertEqual(row['available'],50000)

    def test_current_year_direct_payment_reduces_cash_provision_once_and_requires_balance_update(self):
        item=self.add(kind='hmrc',tax_year='2026-27',tax_component='first_on_account',statement_confirmed='1',amount='300')
        self.balance()
        self.complete()
        self.pay(item)
        self.complete()
        self.assertIsNone(self.result()['groups'][0]['available'])
        self.balance(95000)
        self.complete()
        row=self.result()['groups'][0]
        self.assertEqual(row['tax_paid'],5000)
        self.assertEqual(row['tax_target'],25000)
        self.assertEqual(row['protected'],25000)
        self.assertEqual(row['available'],70000)

    def test_unknown_currencies_stay_separate_and_shortfall_stays_negative(self):
        self.add(currency='EUR')
        self.add(amount='1500')
        self.balance()
        self.complete()
        rows={r['currency']:r for r in self.result()['groups']}
        self.assertIsNone(rows['EUR']['available'])
        self.assertEqual(rows['GBP']['available'],-80000)

    def test_complete_totals_include_more_than_visible_occurrences(self):
        self.add(amount='1',recurrence='weekly',due_date=str(self.today-timedelta(weeks=110)))
        self.balance()
        self.complete()
        result=self.result()
        self.assertEqual(result['count'],115)
        self.assertEqual(len(result['upcoming']),100)
        self.assertEqual(result['groups'][0]['bills'],11500)

    def test_payment_budget_replay_future_date_and_invalid_occurrence_rejected(self):
        item=self.add()
        form=self.pay(item)
        for changes in ({},dict(payment_id=str(uuid4()),amount='51'),dict(payment_id=str(uuid4()),paid_on=str(self.today+timedelta(days=1))),dict(payment_id=str(uuid4()),occurrence_date=str(self.today+timedelta(days=1)))):
            with self.assertRaises((ValueError,Conflict)):
                cash.record_payment(item,{**form,**changes})
        self.assertEqual(self.session.query(CashPayment).count(),1)

    def test_observed_balance_validation_and_revision(self):
        form=dict(account=self.account,version='0',amount='-10',observed_at=self.now.astimezone(LOCAL_TIME).strftime('%Y-%m-%dT%H:%M'))
        cash.save_balance(form)
        self.session.commit()
        self.assertEqual(self.session.get(CashBalance,self.account).amount_minor,-1000)
        with self.assertRaises(Conflict):
            cash.save_balance(form)
        self.session.rollback()
        form['version']='1'
        form['observed_at']=(datetime.now(LOCAL_TIME)+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        with self.assertRaises(ValueError):
            cash.save_balance(form)

    def test_edit_preserves_payment_history_and_cannot_reduce_below_paid(self):
        item=self.add()
        self.pay(item)
        form=dict(kind='bill',name='Rent',currency='GBP',amount='10',due_date=str(item.due_date),recurrence='once',end_date='',notes='')
        with self.assertRaises(ValueError):
            cash.save_commitment(item.id,form,str(item.revision))
        form['amount']='100'
        form['currency']='EUR'
        with self.assertRaises(ValueError):
            cash.save_commitment(item.id,form,str(item.revision))

    def test_owner_forms_csrf_coverage_staleness_and_no_bank_traffic(self):
        fields=self.form('/dashboard/cash-planning/new?kind=hmrc')
        form=dict(**fields,kind='hmrc',name='Statement <script>charge</script>',currency='GBP',amount='300',due_date=str(self.today),recurrence='once',end_date='',tax_year='2026-27',tax_component='first_on_account',notes='',statement_confirmed='1',supported_charge_confirmed='1')
        self.assertEqual(self.client.post('/dashboard/cash-planning/new',data=form).status_code,303)
        self.assertEqual(self.client.post('/dashboard/cash-planning/new',data=form).status_code,409)
        self.assertIn('&lt;script&gt;charge&lt;/script&gt;',self.client.get('/dashboard/cash-planning').get_data(as_text=True))
        coverage=self.form()
        self.add(name='New bill')
        self.assertEqual(self.client.post('/dashboard/cash-planning',data=dict(action='coverage',confirm='1',csrf_token=coverage['csrf_token'],fingerprint=coverage['fingerprint'])).status_code,409)
        self.assertEqual(self.client.post('/dashboard/cash-planning',data=dict(action='coverage',confirm='1')).status_code,400)
        self.bank.assert_not_called()

    def test_export_is_private_formula_safe_and_excludes_bank_payloads(self):
        self.add(name='=Unsafe formula')
        self.balance()
        output=package(self.session)
        self.addCleanup(output.close)
        with ZipFile(output) as archive:
            self.assertEqual(len(archive.namelist()),4)
            self.assertIn("'=Unsafe formula",archive.read('commitments.csv').decode('utf-8-sig'))
            self.assertNotIn(b'raw_payload',b''.join(archive.read(name) for name in archive.namelist()))
            self.assertIn('currency,account_name',archive.read('balance_observations.csv').decode('utf-8-sig'))
        response=self.client.get('/dashboard/cash-planning/export')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        with ZipFile(io.BytesIO(response.data)) as archive:
            self.assertEqual(len(archive.namelist()),4)

    def test_void_cancel_duplicate_submit_and_payment_history_are_preserved(self):
        item=self.add()
        item_id=item.id
        url='/dashboard/cash-planning/'+item_id
        fields=self.form(url)
        form=dict(action='pay',version=str(item.revision),csrf_token=fields['csrf_token'],payment_id=str(uuid4()),
            occurrence_date=str(item.due_date),paid_on=str(self.today),amount='50',reference='Actually paid')
        self.assertEqual(self.client.post(url,data=form).status_code,303)
        self.assertEqual(self.client.post(url,data=form).status_code,409)
        payment_id=self.session.scalar(db.select(CashPayment.id).where(CashPayment.commitment_id==item_id))
        revision=self.session.get(CashCommitment,item_id).revision
        self.assertEqual(self.client.post(url,data=dict(action='void',version=str(revision),payment_id=payment_id,csrf_token=fields['csrf_token'])).status_code,303)
        self.assertFalse(self.session.get(CashPayment,payment_id).active)
        revision=self.session.get(CashCommitment,item_id).revision
        self.assertEqual(self.client.post(url,data=dict(action='cancel',version=str(revision),confirm='1',csrf_token=fields['csrf_token'])).status_code,303)
        self.assertFalse(self.session.get(CashCommitment,item_id).active)
        self.assertEqual(self.session.query(CashPayment).count(),1)
        self.assertEqual(self.result()['count'],0)

    def test_new_day_and_new_account_invalidate_coverage(self):
        self.balance()
        self.complete()
        self.assertTrue(self.result()['complete'])
        self.assertFalse(self.result(as_of=datetime.now(timezone.utc)+timedelta(days=1))['complete'])
        self.session.add(Account(account_uid=str(uuid4()),default_category_uid=str(uuid4()),currency='GBP',raw_payload={}))
        self.session.commit()
        self.assertFalse(self.result()['complete'])
        self.assertIsNone(self.result()['groups'][0]['available'])

    def test_other_year_and_penalty_payments_do_not_reduce_current_income_tax_target(self):
        for changes in (dict(tax_year='2025-26',tax_component='balancing'),dict(tax_year='2026-27',tax_component='other')):
            item=self.add(kind='hmrc',statement_confirmed='1',amount='100',**changes)
            self.pay(item)
        self.balance()
        self.complete()
        row=self.result()['groups'][0]
        self.assertEqual(row['tax_paid'],0)
        self.assertEqual(row['tax_target'],30000)
        self.assertEqual(row['protected'],40000)

    def test_owner_api_and_query_validation_protect_planning(self):
        for suffix in ('?unknown=1','?page=1'):
            self.assertEqual(self.client.get('/dashboard/cash-planning'+suffix).status_code,400)
        with self.client.session_transaction() as cookie:
            cookie.clear()
        for url in ('/dashboard/cash-planning','/dashboard/cash-planning/new','/dashboard/cash-planning/export'):
            for headers in ({},{'Authorization':'Bearer test-key'}):
                self.assertEqual(self.client.get(url,headers=headers).status_code,302)

    def test_changed_account_currency_withholds_old_balance_units(self):
        self.balance()
        self.session.get(Account,self.account).currency='EUR'
        self.session.commit()
        self.complete()
        row=self.result()['groups'][0]
        self.assertIsNone(row['available'])
        self.assertIn('An account currency changed. Record a new balance in its current currency.',row['reasons'])

    def test_browser_fixture_binding_matches_actual_login_identifier(self):
        from app import app
        from browser_owner import install_owner_cookie
        self.form()
        cookie=self.client.get_cookie(app.config['SESSION_COOKIE_NAME'])
        old=app.test_client()
        signer=app.session_interface.get_signing_serializer(app)
        initial=signer.loads(cookie.value)
        initial.pop('_fresh',None)
        initial.pop('_id',None)
        old.set_cookie(cookie.key,signer.dumps(initial))
        response=old.get('/dashboard/sync-status',headers={'User-Agent':'Synthetic browser'})
        self.assertEqual(response.status_code,200)
        self.assertIn('Set-Cookie',response.headers)
        context=Mock()
        context.pages=[]
        context.new_page.return_value.evaluate.return_value='Synthetic browser'
        install_owner_cookie(context,self,'http://127.0.0.1:5000')
        value=context.add_cookies.call_args.args[0][0]['value']
        browser=app.test_client()
        browser.set_cookie(cookie.key,value)
        response=browser.get('/dashboard/sync-status',headers={'User-Agent':'Synthetic browser'})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('Set-Cookie',response.headers)

    def test_unattributed_or_partial_tax_credit_is_never_guessed_from_full_payment(self):
        item=self.add(kind='hmrc',tax_year='2026-27',tax_component='balancing',statement_confirmed='1',amount='300')
        self.pay(item,tax_credit='')
        self.assertEqual(self.result()['groups'][0]['tax_paid'],0)
        self.assertEqual(self.result()['groups'][0]['tax_target'],30000)
        self.pay(item,tax_credit='20')
        self.assertEqual(self.result()['groups'][0]['tax_paid'],2000)
        self.assertEqual(self.result()['groups'][0]['tax_target'],28000)
        from werkzeug.exceptions import BadRequest
        for credit in ('51','-1'):
            with self.assertRaises((ValueError,BadRequest)):
                self.pay(item,tax_credit=credit)

    def test_mixed_statement_charges_require_other_or_explicit_supported_scope(self):
        form=dict(kind='hmrc',name='Mixed bill',currency='GBP',amount='100',due_date=str(self.today),
            recurrence='once',end_date='',tax_year='2026-27',tax_component='balancing',notes='',statement_confirmed='1')
        with self.assertRaises(ValueError):
            cash.save_commitment(str(uuid4()),form)
        form['tax_component']='other'
        item=cash.save_commitment(str(uuid4()),form)
        self.session.commit()
        with self.assertRaises(ValueError):
            self.pay(item,tax_credit='10')

    def test_review_saves_multiple_balances_and_coverage_together(self):
        second = sample_id(2)
        self.session.add(Account(account_uid=second,default_category_uid=str(uuid4()),currency='GBP',name='Second account',raw_payload={}))
        self.session.commit()
        values = dict(action='review', **self.form(), confirm='1')
        for identity in (self.account, second):
            values.update({f'amount_{identity}':'1000', f'version_{identity}':'0',
                           f'observed_{identity}':datetime.now(LOCAL_TIME).strftime('%Y-%m-%dT%H:%M')})
        self.assertEqual(self.client.post('/dashboard/cash-planning',data=values).status_code,303)
        self.assertTrue(self.result()['complete'])
        self.assertEqual(self.result()['groups'][0]['available'],170000)
        self.assertFalse(self.result()['balance_tasks'])
        self.assertEqual(self.client.post('/dashboard/cash-planning',data=values).status_code,409)

        values = dict(action='review', **self.form())
        values.update({f'amount_{self.account}':'0', f'version_{self.account}':'1',
                       f'observed_{self.account}':datetime.now(LOCAL_TIME).strftime('%Y-%m-%dT%H:%M')})
        self.assertEqual(self.client.post('/dashboard/cash-planning',data=values).status_code,303)
        self.assertEqual(self.session.get(CashBalance,self.account).amount_minor,0)
        self.assertFalse(self.result()['complete'])

    def test_review_rolls_back_all_balances_and_retains_entries_on_error(self):
        second = sample_id(2)
        self.session.add(Account(account_uid=second,default_category_uid=str(uuid4()),currency='GBP',name='Second account',raw_payload={}))
        self.session.commit()
        values = dict(action='review', **self.form(), confirm='1')
        for identity, value in ((self.account,'1000'),(second,'unknown')):
            values.update({f'amount_{identity}':value, f'version_{identity}':'0',
                           f'observed_{identity}':datetime.now(LOCAL_TIME).strftime('%Y-%m-%dT%H:%M')})
        response = self.client.post('/dashboard/cash-planning',data=values)
        self.assertEqual(response.status_code,400)
        self.assertIn('value="1000"',response.get_data(as_text=True))
        self.assertIsNone(self.session.get(CashBalance,self.account))
        self.assertIsNone(self.session.get(CashCoverage,1))

    def test_blank_review_keeps_balance_age_and_does_not_unblock_stale_cash(self):
        balance = self.balance()
        balance.observed_at = self.now-timedelta(days=2)
        self.session.commit()
        before = balance.observed_at
        self.assertEqual(self.client.post('/dashboard/cash-planning',data=dict(action='review', **self.form(), confirm='1')).status_code,303)
        result = self.result()
        self.assertTrue(result['complete'])
        self.assertIsNone(result['groups'][0]['available'])
        self.assertEqual(self.session.get(CashBalance,self.account).observed_at,before)
        self.assertEqual(len(result['balance_tasks']),1)

    def test_review_rejects_unknown_fields_and_changed_records(self):
        values = dict(action='review', **self.form(), confirm='1', amount_unknown='0')
        self.assertEqual(self.client.post('/dashboard/cash-planning',data=values).status_code,400)
        values.pop('amount_unknown')
        self.add(name='New bill')
        self.assertEqual(self.client.post('/dashboard/cash-planning',data=values).status_code,409)
