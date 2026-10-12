"""CSV onboarding against disposable real/sample schemas, with bank traffic forbidden."""
from datetime import datetime, timedelta, timezone
import io
import os
import re
import unittest
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import BadRequest, Conflict, Gone
from werkzeug.datastructures import MultiDict
from models import Account, AccountHistory, CashBalance, StatementImport, StatementImportRow, StatementMapping, StatementChange, Transaction
from services import statement_accounts as accounts, statement_imports as imports, cash_planning as cash, statement_review as review
from services.database.connection import db
from services.tax.export import evidence_rows, FIELDS
from services.tax.reserves import current_plan
from services.transactions.classification import save_classification
from services.invoices.workflow import LOCAL_TIME
import test_test_data as sample_support


class AmountTests(unittest.TestCase):
    def test_header_suggestions_do_not_guess_ambiguous_columns_or_date_formats(self):
        suggested=imports.mapping_defaults(['Date','Credit','Debit','Name'])
        self.assertEqual(suggested,dict(date='0',credit='1',debit='2',payer='3',amount_mode='separate'))
        self.assertNotIn('date',imports.mapping_defaults(['Date','Date','Amount']))
        self.assertNotIn('date_format',suggested)
        self.assertNotIn('credit',imports.mapping_defaults(['Date','Amount','Credit','Debit']))
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

    def transfer_pair(self):
        saved=self.stage(b'Date,Amount,Payment,Reference\n2026-04-10,-100,Own account,transfer\n')
        self.confirm(saved)
        other=accounts.save(str(uuid4()),dict(category=str(uuid4()),name='Receiving bank',bank_name='CSV',currency='GBP',opened_on='2026-04-06'))
        staged=imports.upload(other,b'Date,Amount,Payment,Reference\n2026-04-11,100,Own account,transfer\n')
        db.session.flush()
        staged=imports.map_rows(staged,self.mapping)
        db.session.commit()
        self.confirm(staged)
        pairs,_=review.suggestions(self.id)
        self.assertEqual(len(pairs),1)
        pair=pairs[0]
        return saved,pair,dict(left=pair['left_id'],right=pair['right_id'],left_version=pair['left_version'],right_version=pair['right_version'],confirm='1')

    def test_transfer_pair_reviews_both_without_creating_income_and_replay_stale(self):
        _,pair,form=self.transfer_pair()
        review.match(self.id,form)
        db.session.commit()
        for tx in (pair['left'],pair['right']):
            db.session.refresh(tx)
            self.assertEqual(tx.classification.type,'internal_transfer')
            self.assertIsNotNone(tx.confirmed_at)
            self.assertIsNone(tx.income)
        self.assertEqual(review.suggestions(self.id)[0],[])
        change=db.session.scalar(db.select(StatementChange))
        self.assertEqual(len(change.before),2)
        self.assertEqual(change.before[0]['classification']['type'],'other')
        self.assertEqual(change.after[0]['classification']['type'],'internal_transfer')
        from routes.statement_accounts import recent_changes
        self.assertEqual(recent_changes(pair['right'].account_uid,transfers_only=True)[0].id,change.id)
        from services.tax.records import tax_records
        from services.tax.rules import reviewed_rules
        figures=tax_records(reviewed_rules(),payments=[pair['left'],pair['right']])
        for name in ('credits','deductions','received','ni_paid'):
            self.assertEqual(figures[name],{},f'Transfer pair must not create {name}.')
        with self.assertRaises(Conflict):
            review.match(self.id,form)
        db.session.rollback()
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(StatementChange)),1)

    def test_transfer_guard_currency_amount_dates_confirmation_and_manual_choice(self):
        _,pair,form=self.transfer_pair()
        with self.assertRaises(ValueError):
            review.match(self.id,dict(form,confirm=''))
        db.session.rollback()
        right=review.transaction(pair['right_id'])
        for field,value in [('currency','EUR'),('amount_minor',10001),('transaction_time',right.transaction_time+timedelta(days=5))]:
            with self.subTest(field=field):
                tx=review.transaction(pair['right_id'])
                setattr(tx,field,value)
                db.session.commit()
                self.assertEqual(review.suggestions(self.id)[0],[])
                with self.assertRaises(ValueError):
                    review.match(self.id,dict(form,right_version=review.version(tx)))
                db.session.rollback()
                setattr(tx,field,dict(currency='GBP',amount_minor=10000,transaction_time=datetime(2026,4,10,23,tzinfo=timezone.utc))[field])
                db.session.commit()
        right=review.transaction(pair['right_id'])
        save_classification(right,'income','User chose income')
        db.session.commit()
        self.assertEqual(review.suggestions(self.id)[0],[])
        with self.assertRaises(ValueError):
            review.match(self.id,dict(form,right_version=review.version(right)))

    def correction_form(self,saved):
        item=review.imported_rows(saved.id)[0]
        return item['tx'],dict(transaction=item['identity'],version=item['version'],action='edit',
            date='2026-04-12',amount='125.25',direction='IN',payer='Corrected payer',reference='Corrected reference',reason='Checked statement',confirm='1')

    def test_correction_retains_original_audit_and_invalidates_review(self):
        saved=self.confirm(self.stage())
        original=list(saved.rows)
        tx,form=self.correction_form(saved)
        tx.confirmed_at=datetime.now(timezone.utc)
        db.session.commit()
        with self.assertRaises(Conflict):
            review.correct(saved,form)
        db.session.rollback()
        form['version']=review.version(tx)
        review.correct(saved,form)
        db.session.commit()
        self.assertEqual(tx.amount_minor,12525)
        self.assertIsNone(tx.confirmed_at)
        self.assertEqual(saved.rows,original)
        history=db.session.scalar(db.select(StatementChange))
        self.assertEqual(history.before[0]['amount_minor'],10050)
        self.assertEqual(history.after[0]['amount_minor'],12525)
        with self.assertRaises(Conflict):
            review.correct(saved,form)
        db.session.rollback()
        self.assertEqual(self.count(),2)

    def test_exclude_restore_reversible_and_linked_evidence_protected(self):
        saved=self.confirm(self.stage())
        tx,form=self.correction_form(saved)
        review.correct(saved,dict(form,action='exclude'))
        db.session.commit()
        self.assertEqual(tx.status,'REVERSED')
        self.assertIsNotNone(tx.confirmed_at)
        self.assertEqual(tx.amount_minor,10050)
        self.assertEqual(accounts.coverage_view(accounts.account(self.id),db.session)['unreviewed'],1)
        review.correct(saved,dict(form,action='restore',version=review.version(tx)))
        db.session.commit()
        self.assertEqual(tx.status,'SETTLED')
        self.assertIsNone(tx.confirmed_at)
        from models import TransactionIncome
        tx.income=TransactionIncome(income_type='employment',tax_treatment='no_tax_deducted',gross_minor=10050,recorded_currency='GBP',needs_review=False)
        db.session.commit()
        with self.assertRaises(ValueError):
            review.correct(saved,dict(form,version=review.version(tx)))
        db.session.rollback()
        self.assertEqual(tx.amount_minor,10050)
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(StatementChange)),2)

    def test_correction_rejects_foreign_rows_dates_bad_amounts_reason_and_no_confirm(self):
        saved=self.confirm(self.stage())
        tx,form=self.correction_form(saved)
        for changes in (dict(date='2027-05-01'),dict(amount='-2'),dict(amount='1.234'),dict(reason=''),dict(confirm=''),dict(transaction='not/a/uuid')):
            with self.subTest(changes=changes),self.assertRaises((ValueError,BadRequest)):
                review.correct(saved,dict(form,**changes))
            db.session.rollback()
            self.assertEqual(review.transaction(form['transaction']).amount_minor,10050)
        another=self.confirm(self.stage(b'Date,Amount,Payment,Reference\n2026-04-13,2,Other,other\n'))
        _,foreign=self.correction_form(another)
        with self.assertRaises(ValueError):
            review.correct(saved,foreign)

    def test_saved_mapping_exact_format_account_isolation_and_invalid_fallback(self):
        saved=imports.upload(accounts.account(self.id),self.data)
        db.session.flush()
        saved=imports.map_rows(saved,dict(self.mapping,remember='1'))
        db.session.commit()
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(StatementMapping)),0)
        self.confirm(saved)
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(StatementMapping)),1)
        fresh=imports.upload(accounts.account(self.id),self.data.replace(b'100.50',b'101.50'))
        db.session.flush()
        reused,found=imports.reuse_mapping(fresh)
        self.assertTrue(found)
        self.assertEqual(reused.status,'preview')
        self.assertEqual(self.count(),2)
        renamed=imports.upload(accounts.account(self.id),self.data.replace(b'Amount',b'Value'))
        db.session.flush()
        self.assertFalse(imports.reuse_mapping(renamed)[1])
        invalid=imports.upload(accounts.account(self.id),self.data.replace(b'100.50',b'not-money'))
        db.session.flush()
        self.assertFalse(imports.reuse_mapping(invalid)[1])
        self.assertEqual(invalid.status,'mapping')
        self.assertEqual(invalid.headers[0],'Date')
        other=accounts.save(str(uuid4()),dict(category=str(uuid4()),name='Other',bank_name='CSV',currency='GBP',opened_on='2026-04-06'))
        separate=imports.upload(other,self.data)
        db.session.flush()
        self.assertFalse(imports.reuse_mapping(separate)[1])

    def test_review_pages_csrf_bounds_owner_and_allowlisted_export(self):
        saved,_,_=self.transfer_pair()
        for path in (f'/dashboard/accounts/{self.id}/transfers',f'/dashboard/accounts/imports/{saved.id}/corrections'):
            response=self.client.get(path)
            self.assertEqual(response.status_code,200,response.get_data(as_text=True))
            self.assertIn('no-store',response.headers['Cache-Control'])
            self.assertEqual(self.client.post(path,data={}).status_code,400)
            self.assertEqual(self.client.get(path,headers={'Authorization':'Bearer test-key'}).status_code,302)
            from app import app
            self.assertEqual(app.test_client().get(path).status_code,302)
        correction=f'/dashboard/accounts/imports/{saved.id}/corrections'
        for suffix in ('?page=0','?page=21','?page=1&page=2','?unknown=1'):
            self.assertEqual(self.client.get(correction+suffix).status_code,400)
        _,form=self.correction_form(saved)
        review.correct(saved,form)
        db.session.commit()
        evidence=list(evidence_rows(db.session,'statement_changes',datetime(2026,4,6,tzinfo=timezone.utc),datetime(2027,4,6,tzinfo=timezone.utc),datetime.now(timezone.utc)))
        self.assertEqual(len(evidence),1)
        self.assertEqual(set(evidence[0]),set(FIELDS['statement_changes'].split()))
        self.assertNotIn('raw_payload',str(evidence))

    def test_bulk_exclusion_is_atomic_stale_guarded_and_preserves_rows(self):
        saved=self.confirm(self.stage())
        records=review.imported_rows(saved.id)
        selected=[item['identity'] for item in records]
        form=dict(selection_version=review.selection_version(records),reason='Duplicate statement records',confirm='1')
        with self.assertRaises(ValueError):
            review.bulk_exclude(saved,form,selected+[str(uuid4())],1)
        db.session.rollback()
        self.assertTrue(all(item['tx'].status=='SETTLED' for item in review.imported_rows(saved.id)))
        review.bulk_exclude(saved,form,selected,1)
        db.session.commit()
        self.assertTrue(all(item['tx'].status=='REVERSED' for item in review.imported_rows(saved.id)))
        self.assertEqual(self.count(),2)
        self.assertEqual(len(saved.rows),2)
        self.assertEqual(accounts.coverage_view(accounts.account(self.id),db.session)['unreviewed'],0)
        change=db.session.scalar(db.select(StatementChange))
        self.assertEqual(change.kind,'bulk-exclude')
        self.assertEqual(len(change.before),2)
        with self.assertRaises(Conflict):
            review.bulk_exclude(saved,form,selected,1)
        db.session.rollback()
        tx,edit=self.correction_form(saved)
        review.correct(saved,edit)
        db.session.commit()
        self.assertEqual(tx.status,'REVERSED','Editing excluded details must not restore the payment implicitly.')
        large=self.confirm(self.stage(('Date,Amount,Payment,Reference\n'+''.join(
            f'2026-04-13,{number}.01,Payment {number},reference\n' for number in range(1,61))).encode()))
        self.assertEqual(len(review.imported_rows(large.id,1)),50)
        self.assertEqual(len(review.imported_rows(large.id,2)),10)
        first=review.imported_rows(large.id,1)
        second=review.imported_rows(large.id,2)
        with self.assertRaises(ValueError):
            review.bulk_exclude(large,dict(form,selection_version=review.selection_version(first)),[second[0]['identity']],1)
        db.session.rollback()

    def test_invoice_allocations_even_inactive_block_correction_and_pairing(self):
        from models import IncomeStream, Invoice, InvoicePayment
        saved,pair,form=self.transfer_pair()
        right=review.transaction(pair['right_id'])
        stream=IncomeStream(id=str(uuid4()),name='Synthetic invoiced source',kind='self_employed')
        db.session.add(stream)
        db.session.flush()
        invoice=Invoice(id=str(uuid4()),income_stream_id=stream.id,stream_name=stream.name,
            client_name='Synthetic client',issue_date=datetime(2026,4,10).date(),due_date=datetime(2026,4,20).date(),currency='GBP',total_minor=10000)
        db.session.add(invoice)
        db.session.flush()
        allocation=InvoicePayment(id=str(uuid4()),invoice_id=invoice.id,account_uid=right.account_uid,
            category_uid=right.category_uid,feed_item_uid=right.feed_item_uid,cash_minor=10000,
            payment_version='original-version',payment_label='Original receipt',active=False)
        db.session.add(allocation)
        db.session.commit()
        self.assertEqual(review.suggestions(self.id)[0],[])
        with self.assertRaises(ValueError):
            review.match(self.id,dict(form,right_version=review.version(right)))
        db.session.rollback()
        # Protect original allocations even after the active allocation is removed.
        provenance=db.session.scalar(db.select(StatementImportRow).where(StatementImportRow.feed_item_uid==right.feed_item_uid))
        receipt_batch=db.session.get(StatementImport,provenance.batch_id)
        _,correction=self.correction_form(receipt_batch)
        with self.assertRaises(ValueError):
            review.correct(receipt_batch,correction)
        db.session.rollback()
        self.assertEqual(db.session.get(InvoicePayment,allocation.id).payment_label,'Original receipt')
        self.assertEqual(review.transaction(pair['right_id']).amount_minor,10000)

    def test_excluded_mistaken_later_rows_do_not_block_account_closure(self):
        saved=self.confirm(self.stage())
        records=review.imported_rows(saved.id)
        review.bulk_exclude(saved,dict(selection_version=review.selection_version(records),
            reason='Imported into wrong account; retain evidence',confirm='1'),[item['identity'] for item in records],1)
        db.session.add(CashBalance(account_uid=self.id,amount_minor=0,recorded_currency='GBP',revision=1,
            observed_at=datetime.now(timezone.utc),recorded_at=datetime.now(timezone.utc)))
        db.session.commit()
        accounts.close(accounts.account(self.id),dict(closed_on='2026-04-06',confirm='1'))
        db.session.commit()
        self.assertEqual(self.count(),2)
        self.assertEqual(accounts.account(self.id).closed_on.isoformat(),'2026-04-06')
