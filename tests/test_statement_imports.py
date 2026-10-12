"""CSV onboarding against disposable real/sample schemas, with bank traffic forbidden."""
from datetime import datetime, timedelta, timezone
import io
import os
import re
import unittest
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import Conflict, Gone
from werkzeug.datastructures import MultiDict
from models import Account, AccountHistory, CashBalance, StatementImport, StatementImportRow, Transaction
from services import statement_accounts as accounts, statement_imports as imports, cash_planning as cash
from services.database.connection import db
from services.tax.export import evidence_rows, FIELDS
from services.tax.reserves import current_plan
from services.transactions.classification import save_classification
from services.invoices.workflow import LOCAL_TIME
import test_test_data as sample_support


class AmountTests(unittest.TestCase):
    def test_numeric_grouping_exact_and_bounded(self):
        self.assertEqual(imports.money('1,234.56','GBP','point'),123456)
        self.assertEqual(imports.money('-1.234,56','EUR','comma'),-123456)
        self.assertEqual(imports.money('0.01','GBP','point'),1)
        self.assertEqual(imports.money('1,234','JPY','point'),1234)
        for value in ('1,23.45','£12.50','NaN','1e5','1.234','--1','92233720368547758.08'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                imports.money(value,'GBP','point')


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS')=='1','Requires PostgreSQL.')
class StatementTests(unittest.TestCase):
    drop_schemas=sample_support.TestDataTests.drop_schemas
    csrf=sample_support.TestDataTests.csrf
    def setUp(self):
        sample_support.TestDataTests.setUp(self)
        self.row=accounts.save(str(uuid4()),dict(category=str(uuid4()),name='Earlier bank',bank_name='Any bank',currency='GBP',opened_on='2026-04-06'))
        db.session.commit()
        self.id=self.row.account_uid
        self.mapping=dict(date='0',amount='1',payer='2',reference='3',date_format='iso',amount_mode='signed',decimal='point')
        self.data=b'Date,Amount,Payment,Reference\n2026-04-10,100.50,Employer,pay\n2026-04-11,-20,Shop,food\n'

    def stage(self,data=None,mapping=None):
        saved=imports.upload(accounts.account(self.id),data or self.data)
        db.session.flush()
        saved=imports.map_rows(saved,mapping or self.mapping)
        db.session.commit()
        return saved

    def confirm(self,saved,**changes):
        rows,fingerprint=imports.preview(saved)
        form=dict(fingerprint=fingerprint,confirm='1',**{f"row_{item['row_number']}":'import' for item in rows})
        form.update(changes)
        result=imports.confirm(saved,form)
        db.session.commit()
        return result

    def count(self):
        return db.session.scalar(db.select(db.func.count()).select_from(Transaction).where(Transaction.account_uid==self.id))

    def test_preview_import_replay_and_exact_file_idempotency(self):
        saved=self.stage()
        self.assertEqual(self.count(),0)
        self.assertEqual(saved.headers,[])
        self.assertEqual(saved.rows[0]['amount_minor'],10050)
        self.confirm(saved)
        self.assertEqual(self.count(),2)
        self.confirm(saved)
        self.assertEqual(self.count(),2)
        self.assertEqual(self.stage().id,saved.id)
        tx=db.session.scalar(db.select(Transaction).where(Transaction.account_uid==self.id,Transaction.direction=='IN'))
        self.assertEqual(tx.classification.type,'other')
        self.assertIsNone(tx.confirmed_at)
        self.assertEqual(tx.raw_payload,{})
        self.assertIsNotNone(tx.transaction_time.utcoffset())
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(StatementImportRow)),2)

    def test_overlapping_and_repeated_rows_require_decisions_without_silent_loss(self):
        self.confirm(self.stage())
        saved=self.stage(b'Date,Amount,Payment,Reference\n2026-04-10,100.50,Employer,other\n2026-04-10,100.50,Employer,other\n')
        rows,_=imports.preview(saved)
        self.assertEqual([item['duplicates'] for item in rows],[1,1])
        self.assertTrue(rows[1]['repeated'])
        with self.assertRaises(ValueError):
            self.confirm(saved,row_2='')
        db.session.rollback()
        self.assertEqual(self.count(),2)
        self.confirm(saved,row_2='skip')
        self.assertEqual(self.count(),3)
        self.assertEqual(saved.skipped_count,1)

    def test_stale_transactions_and_account_revision_are_rejected(self):
        saved=self.stage()
        _,fingerprint=imports.preview(saved)
        item=dict(saved.rows[0],amount_minor=5)
        imports.create_transaction(accounts.account(self.id),item,str(uuid4()),'STATEMENT_IMPORT')
        db.session.commit()
        with self.assertRaises(Conflict):
            self.confirm(saved,fingerprint=fingerprint)
        db.session.rollback()
        accounts.account(self.id).revision+=1
        db.session.commit()
        with self.assertRaises(Conflict):
            self.confirm(saved)

    def test_invalid_file_rows_and_currency_never_partially_import(self):
        for data in (b'Header\n',b'A,B\n1\n',b'A\n'+b'x'*2001+b'\n',b'A\n\0\n',b'A\n'+b'1\n'*1001):
            with self.subTest(data=data[:30]),self.assertRaises(ValueError):
                imports.upload(accounts.account(self.id),data)
            db.session.rollback()
        for data,mapping in ((b'Date,Amount,Payment,Reference\n2026-04-10,1,A,B\n2026-04-31,2,C,D\n',self.mapping),
            (b'Date,Amount,Currency\n2026-04-10,1,EUR\n',dict(self.mapping,payer='',reference='',currency='2')),
            (b'Date,Amount,Payment,Reference\n2999-01-01,1,A,B\n',self.mapping)):
            with self.subTest(data=data),self.assertRaises(ValueError):
                self.stage(data,mapping)
            db.session.rollback()
        self.assertEqual(self.count(),0)

    def test_separate_debit_credit_and_explicit_date_formats(self):
        mapping=dict(date='0',credit='1',debit='2',date_format='dmy',amount_mode='separate',decimal='comma')
        saved=imports.upload(accounts.account(self.id),'Date;Credit;Debit\n10/04/2026;1.234,56;\n11/04/2026;;20,50\n'.encode(),delimiter=';')
        db.session.flush()
        saved=imports.map_rows(saved,mapping)
        self.assertEqual([(item['amount_minor'],item['direction']) for item in saved.rows],[(123456,'IN'),(2050,'OUT')])
        with self.assertRaises(ValueError):
            imports.normalize(accounts.account(self.id),['10/04/2026','5','10'],mapping,2)

    def test_history_and_balance_are_separate_and_transfers_are_not_earnings(self):
        row=accounts.account(self.id)
        db.session.add(CashBalance(account_uid=self.id,amount_minor=500000,recorded_currency='GBP',revision=1,
            observed_at=datetime.now(timezone.utc),recorded_at=datetime.now(timezone.utc)))
        db.session.commit()
        self.assertEqual(self.count(),0)
        estimate=current_plan(refresh_rules=False)
        self.assertTrue(any('Earlier bank' in item for item in estimate['blockers']))
        saved=self.stage()
        self.confirm(saved)
        row=accounts.account(self.id)
        today=str(datetime.now(LOCAL_TIME).date())
        form=dict(history_version='0',from_on='2026-04-06',through_on=today,confirm='1')
        with self.assertRaises(ValueError):
            accounts.confirm_history(row,form)
        for tx in db.session.scalars(db.select(Transaction).where(Transaction.account_uid==self.id)):
            save_classification(tx,'internal_transfer',None)
            tx.confirmed_at=datetime.now(timezone.utc)
        accounts.confirm_history(row,form)
        db.session.commit()
        self.assertEqual(accounts.history_issues(db.session),[])
        estimate=current_plan(refresh_rules=False)
        from services.tax.records import tax_records
        from services.tax.rules import reviewed_rules
        payments=db.session.scalars(db.select(Transaction).where(Transaction.account_uid==self.id)).all()
        self.assertEqual(tax_records(reviewed_rules(),payments=payments)['received'],{})
        self.assertFalse(any('Earlier bank' in item for item in estimate['blockers']))
        self.confirm(self.stage(b'Date,Amount,Payment,Reference\n2026-04-12,5,X,Y\n'))
        self.assertFalse(db.session.get(AccountHistory,(self.id,'2026-27')).complete)

    def test_close_preserves_history_and_removes_current_cash_requirement(self):
        row=accounts.account(self.id)
        with self.assertRaises(ValueError):
            accounts.close(row,dict(closed_on='2026-04-06',confirm='1'))
        db.session.add(CashBalance(account_uid=self.id,amount_minor=0,recorded_currency='GBP',revision=1,
            observed_at=datetime.now(timezone.utc),recorded_at=datetime.now(timezone.utc)))
        self.confirm(self.stage())
        with self.assertRaises(ValueError):
            accounts.close(row,dict(closed_on='2026-04-06',confirm='1'))
        accounts.close(row,dict(closed_on=str(datetime.now(LOCAL_TIME).date()),confirm='1'))
        db.session.commit()
        self.assertEqual(self.count(),2)
        self.assertNotIn(self.id,[item.account_uid for item in cash.snapshot(db.session)['accounts']])

    def test_expired_preview_and_private_allowlisted_export(self):
        saved=self.stage()
        saved.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        db.session.commit()
        with self.assertRaises(Gone):
            imports.batch(saved.id)
        saved.expires_at=datetime.now(timezone.utc)+timedelta(minutes=1)
        db.session.commit()
        self.confirm(saved)
        now=datetime.now(timezone.utc)
        rows=list(evidence_rows(db.session,'statement_imports',now-timedelta(days=365),now+timedelta(days=365),now))
        self.assertEqual(set(rows[0]),set(FIELDS['statement_imports'].split()))
        self.assertNotIn('rows',rows[0])
        self.assertNotIn('raw_payload',FIELDS['accounts'])

    def csrf_for(self,path):
        response=self.client.get(path)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True)[:400])
        return re.search(r'name="csrf_token" value="([^"]+)"',response.get_data(as_text=True))[1]

    def test_browser_upload_owner_csrf_sample_isolation_and_provider_guard(self):
        path=f'/dashboard/accounts/{self.id}'
        html=self.client.get(path).get_data(as_text=True)
        self.assertIn('Import a CSV statement',html)
        self.assertIn('no-store',self.client.get(path).headers['Cache-Control'])
        self.assertEqual(self.client.post(path,data={'action':'edit'}).status_code,400)
        anonymous=self.client.application.test_client()
        self.assertEqual(anonymous.get(path).status_code,302)
        self.assertEqual(anonymous.get(path,headers={'Authorization':'Bearer test-key'}).status_code,302)
        response=self.client.get('/dashboard/balances?account='+self.id)
        self.assertIn('Statement account',response.json['balances'][0]['error'])
        self.bank.assert_not_called()
        html=self.client.get('/dashboard?account='+self.id).get_data(as_text=True)
        self.assertNotIn('data-sync-url',html)
        self.assertNotIn('id="account-balances"',html)
        self.assertIn('Import statements',html)
        token=self.csrf_for(path+'/import')
        response=self.client.post(path+'/import',data={'csrf_token':token,'version':'1','encoding':'utf-8-sig','delimiter':',',
            'statement':(io.BytesIO(self.data),'private.csv')})
        self.assertEqual(response.status_code,303)
        mapping_url=response.location
        token=self.csrf_for(mapping_url)
        response=self.client.post(mapping_url,data=dict(self.mapping,csrf_token=token))
        self.assertEqual(response.status_code,303)
        token=self.csrf_for(response.location)
        html=self.client.get(response.location).get_data(as_text=True)
        fingerprint=re.search(r'name="fingerprint" value="([^"]+)"',html)[1]
        result=self.client.post(response.location,data=dict(csrf_token=token,fingerprint=fingerprint,confirm='1',row_2='import',row_3='import'))
        self.assertEqual(result.status_code,303,result.get_data(as_text=True)[:4000])
        self.assertEqual(self.count(),2)
        self.assertEqual(sample_support.TestDataTests.toggle(self,True).status_code,303)
        self.assertEqual(self.client.get(path).status_code,404)
        sample_identity=str(uuid4())
        token=self.csrf_for('/dashboard/accounts/new')
        result=self.client.post('/dashboard/accounts/new',data=dict(csrf_token=token,identity=sample_identity,category=str(uuid4()),
            name='Sample only',bank_name='Sample bank',currency='GBP',opened_on=''))
        self.assertEqual(result.status_code,303)
        self.assertIsNone(db.session.get(Account,sample_identity))

    def test_duplicate_fields_stale_form_and_manual_missing_record(self):
        path=f'/dashboard/accounts/{self.id}/entry'
        token=self.csrf_for(path)
        data=dict(csrf_token=token,entry_id=str(uuid4()),version='1',date='2026-04-10',amount='4.25',direction='OUT',payer='Missing receipt',reference='cash')
        response=self.client.post(path,data=data)
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.count(),0)
        saved=db.session.scalar(db.select(StatementImport).where(StatementImport.account_uid==self.id))
        self.assertEqual(saved.rows[0]['direction'],'OUT')
        self.assertEqual(saved.rows[0]['amount_minor'],425)
        repeated=MultiDict(data)
        repeated.add('amount','10')
        self.assertEqual(self.client.post(path,data=repeated).status_code,400)
        self.assertEqual(self.client.post(path,data=dict(data,version='0')).status_code,409)

    def test_manual_identical_payments_are_distinct_but_repeated_submission_is_not(self):
        path=f'/dashboard/accounts/{self.id}/entry'
        token=self.csrf_for(path)
        form=dict(csrf_token=token,entry_id=str(uuid4()),version='1',date='2026-04-10',amount='0',direction='OUT',payer='Example',reference='')
        response=self.client.post(path,data=form)
        first=db.session.scalar(db.select(StatementImport).where(StatementImport.account_uid==self.id))
        self.assertEqual(first.rows[0]['direction'],'OUT')
        self.confirm(first)
        response=self.client.post(path,data=form)
        self.assertEqual(response.location,f'/dashboard/accounts/imports/{first.id}/preview')
        self.assertEqual(self.count(),1)
        response=self.client.post(path,data=dict(form,entry_id=str(uuid4())))
        second=db.session.get(StatementImport,response.location.split('/')[-2])
        self.assertNotEqual(first.id,second.id)
        rows,_=imports.preview(second)
        self.assertEqual(rows[0]['duplicates'],1)
        self.confirm(second)
        self.assertEqual(self.count(),2)

    def test_import_database_failure_rolls_back_all_rows_and_keeps_preview(self):
        saved=self.stage()
        path=f'/dashboard/accounts/imports/{saved.id}/preview'
        token=self.csrf_for(path)
        _,fingerprint=imports.preview(saved)
        original=imports.create_transaction
        calls=[]
        def fail_second(*args):
            calls.append(1)
            if len(calls)==2:
                raise OperationalError('private SQL',{},Exception('private database details'))
            return original(*args)
        with patch('services.statement_imports.create_transaction',side_effect=fail_second):
            response=self.client.post(path,data=dict(csrf_token=token,fingerprint=fingerprint,confirm='1',row_2='import',row_3='import'))
        self.assertEqual(response.status_code,503)
        self.assertNotIn('private database details',response.get_data(as_text=True))
        self.assertEqual(self.count(),0)
        self.assertEqual(db.session.get(StatementImport,saved.id).status,'preview')
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(StatementImportRow)),0)

    def test_expired_raw_staging_discard_and_windows_encoding(self):
        first=imports.upload(accounts.account(self.id),self.data)
        first.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        db.session.commit()
        identity=first.id
        second=imports.upload(accounts.account(self.id),'Date,Amount,Payment,Reference\n2026-04-10,2,Café,example\n'.encode('cp1252'),encoding='cp1252')
        db.session.flush()
        second=imports.map_rows(second,self.mapping)
        db.session.commit()
        self.assertIsNone(db.session.get(StatementImport,identity,populate_existing=True))
        self.assertEqual(second.rows[0]['payer'],'Café')
        path=f'/dashboard/accounts/imports/{second.id}/preview'
        response=self.client.post(f'/dashboard/accounts/imports/{second.id}/discard',data={'csrf_token':self.csrf_for(path)})
        self.assertEqual(response.status_code,303)
        discarded=db.session.get(StatementImport,second.id,populate_existing=True)
        self.assertEqual(discarded.rows,[])
        self.assertEqual(discarded.status,'discarded')
        self.assertEqual(self.count(),0)

    def test_account_dates_currency_and_sync_identity_are_protected(self):
        from services.transactions.store import SyncStore
        row=accounts.account(self.id)
        with self.assertRaises(ValueError):
            accounts.close(row,dict(closed_on='9999-12-31',confirm='1'))
        with self.assertRaises(ValueError):
            accounts.save(str(uuid4()),dict(category=str(uuid4()),name='X',bank_name='Y',currency='GBP',opened_on='0001-01-01'))
        with self.assertRaises(ValueError):
            accounts.save(self.id,dict(name='X',bank_name='Y',currency='EUR',opened_on='2026-04-06'),str(row.revision))
        with self.assertRaises(ValueError):
            SyncStore().save_account(dict(uid=self.id,category=row.default_category_uid,currency='GBP',opened=None,raw={}))
        self.assertEqual(accounts.account(self.id).provider,'statement')

    def test_cash_page_uses_real_calculator_with_incomplete_statement_history(self):
        estimate=current_plan(refresh_rules=False)
        self.assertEqual(estimate['tax_year'],'2026-27')
        response=self.client.get('/dashboard/cash-planning')
        self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        self.assertIn('Earlier bank',html)
        self.assertIn('Unavailable',html)
