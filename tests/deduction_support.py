"""Rollback-only owner, business and expense fixture for deduction/shift tests."""
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from app import app
from models import BrowserSession, IncomeStream
from services.web.sessions import token_hash
from support import SavedTransactionTestCase

class DeductionOwnerCase(SavedTransactionTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(app.config,SECRET_KEY='d'*64,SESSION_COOKIE_SECURE=False))
        self.enterContext(patch('services.tax.rules.start_rule_check'))
        self.enterContext(patch('routes.deductions.cached_status',return_value={'status':'verified'}))
        self.enterContext(patch('services.tax.records.cached_status',return_value={'status':'verified'}))
        now=datetime.now(timezone.utc)
        self.session.add(BrowserSession(token_hash=token_hash('e'*64),created_at=now,last_seen_at=now,expires_at=now+timedelta(hours=8)))
        self.stream=str(uuid4())
        self.session.add(IncomeStream(id=self.stream,name='Test business',kind='self_employed',expected_gross_minor=3000000,expected_gross_period='yearly',expected_gross_currency='GBP'))
        payment=self.payment()
        payment.transaction_time=datetime(2026,4,10,12,tzinfo=timezone.utc)
        self.session.commit()
        with self.client.session_transaction() as cookie:
            cookie['_user_id']='e'*64
        self.key=f'{self.account}:{self.category}:{self.outgoing}'
        self.url='/dashboard/deductions?expense='+self.key

    def payment(self):
        from models import Transaction
        return self.session.get(Transaction,(self.account,self.category,self.outgoing))

    def html(self):
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True)[:100])
        return response.get_data(as_text=True)

    def expense(self,**changes):
        html=self.html()
        data=dict(action='save_expense',payment=self.key,version=re.search(r'name="version" value="([^"]+)"',html)[1],
                  stream_id=self.stream,amount='10.00',purpose='Business tools',category='general',vehicle_key='',eligible='1',
                  csrf_token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1])
        data.update(changes)
        return self.client.post('/dashboard/deductions',data=data)

    def mileage(self,**changes):
        html=self.html()
        data=dict(action='save_mileage',entry_id=str(uuid4()),stream_id=self.stream,journey_date='2026-04-10',location='england',
                  vehicle_type='car_van',vehicle_key='AB12 CDE',miles='100',purpose='Customer visit',start_postcode='SW1A 1AA',
                  end_postcode='SW1A 2AA',reimbursed='0',mileage_group='',eligible='1',
                  csrf_token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1])
        data.update(changes)
        return self.client.post('/dashboard/deductions',data=data)
