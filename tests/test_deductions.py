"""Synthetic mileage boundaries, actual-tax credits, owner forms and isolation guards."""
import json
import re
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from werkzeug.datastructures import MultiDict
from sqlalchemy.exc import IntegrityError
from models import IncomeStream, MileageEntry, TransactionIncome
from services.tax.mileage import mileage_allowances
from services.tax.mileage_rules import reviewed_rules, validate_publication, refresh_rules, cached_status
from services.tax.records import tax_records
from services.tax.rules import reviewed_rules as income_rules
from deduction_support import DeductionOwnerCase


def journey(miles, kind='self_employed', vehicle='car_van', **changes):
    values = dict(id=str(uuid4()), income_stream_id='stream-1', income_stream=SimpleNamespace(kind=kind),
                  miles=Decimal(str(miles)), vehicle_type=vehicle, mileage_group='',
                  journey_date=date(2026, 4, 6), location='england', reimbursed_minor=0)
    values.update(changes)
    return SimpleNamespace(**values)


def publication(employee=False):
    rows = [('Cars and goods vehicles first 10,000 miles', '55p', '45p'),
            ('Cars and goods vehicles after 10,000 miles', '25p', '25p'), ('Motorcycles', '24p', '24p')]
    if employee:
        rows.append(('Bicycles', '20p', '20p'))
    body = '<table><tr><th>Vehicle</th><th>2026 to 2027</th><th>Before 6 April 2026</th></tr>' + ''.join(
        '<tr>' + ''.join('<td>' + item + '</td>' for item in row) + '</tr>' for row in rows) + '</table>'
    return dict(base_path='/tax-relief-for-employees' if employee else '/simpler-income-tax-simplified-expenses',
                details={'parts': [dict(slug='vehicles-you-use-for-work' if employee else 'vehicles', body=body)]})


class MileageTests(unittest.TestCase):
    def test_current_year_rates_and_boundaries(self):
        for miles, expected in ((1,55), (9999,549945), (10000,550000), (10001,550025), (12000,600000)):
            self.assertEqual(mileage_allowances([journey(miles)]), {'stream-1': expected})
        self.assertEqual(mileage_allowances([journey(10, vehicle='motorcycle')]), {'stream-1':240})
        self.assertEqual(mileage_allowances([journey(10, kind='employed', vehicle='bicycle')]), {'stream-1':200})

    def test_threshold_shared_between_vehicles_same_business(self):
        first, second = journey(8000), journey(4000, id='later', journey_date=date(2026,5,1))
        self.assertEqual(mileage_allowances([second,first]), {'stream-1':600000})

    def test_shared_trade_across_streams_and_separate_jobs(self):
        a = journey(8000, mileage_group='My trade')
        b = journey(4000, mileage_group='MY TRADE', income_stream_id='stream-2', journey_date=date(2026,5,1))
        self.assertEqual(sum(mileage_allowances([a,b]).values()),600000)
        a.income_stream.kind = b.income_stream.kind = 'employed'
        a.mileage_group = b.mileage_group = ''
        self.assertEqual(sum(mileage_allowances([a,b]).values()),660000)
        a.mileage_group = b.mileage_group = 'Associated employers'
        self.assertEqual(sum(mileage_allowances([a,b]).values()),600000)

    def test_reimbursement_offsets_whole_annual_group(self):
        a = journey(100, kind='employed', reimbursed_minor=10000)
        b = journey(100, kind='employed')
        self.assertEqual(mileage_allowances([a,b]), {'stream-1':1000})
        a.reimbursed_minor=20000
        self.assertEqual(mileage_allowances([a,b]), {'stream-1':0})

    def test_decimal_miles_single_rounding(self):
        self.assertEqual(mileage_allowances([journey('.01'),journey('.01')]), {'stream-1':1})

    def test_locations_and_unsupported_year_fail_closed(self):
        for location in ('england','wales','northern_ireland','scotland'):
            self.assertEqual(mileage_allowances([journey(1,location=location)]),{'stream-1':55})
        for changes in ({'location':'france'}, {'journey_date':date(2026,4,5)}, {'journey_date':date(2027,4,6)}):
            with self.assertRaises(ValueError):
                mileage_allowances([journey(1,**changes)])
        with self.assertRaises(ValueError):
            mileage_allowances([journey(10, vehicle='bicycle')])

    def test_both_official_publications_checked_and_daily_cached(self):
        with tempfile.TemporaryDirectory() as directory, patch('services.tax.mileage_rules.fetch_publication', side_effect=[publication(),publication(True)]) as fetch:
            result=refresh_rules(directory)
            self.assertEqual(result['status'],'verified')
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(refresh_rules(directory),result)
            self.assertEqual(fetch.call_count,2)
            self.assertEqual(cached_status(directory),result)

    def test_changed_rates_year_and_withdrawal_rejected(self):
        for payload in (dict(publication(), withdrawn_notice={'explanation':'withdrawn'}),
                        json.loads(json.dumps(publication()).replace('55p','45p')),
                        json.loads(json.dumps(publication()).replace('2026 to 2027','2027 to 2028'))):
            with self.assertRaises(ValueError):
                validate_publication(payload, reviewed_rules())
        with tempfile.TemporaryDirectory() as directory, patch('services.tax.mileage_rules.fetch_publication',return_value={}):
            self.assertEqual(refresh_rules(directory)['status'],'needs_review')
        self.assertEqual(reviewed_rules()['car_van_first_pence'],55)


class DeductionFormsTests(DeductionOwnerCase):
    def test_partial_expense_edit_and_remove(self):
        self.assertEqual(self.expense().status_code,303)
        self.assertEqual(self.payment().expense.amount_minor,1000)
        self.assertEqual(tax_records(income_rules())['deductions'][self.stream],1000)
        self.assertEqual(self.expense(amount='5.00',purpose='Eligible portion').status_code,303)
        self.assertEqual(self.payment().expense.amount_minor,500)
        self.assertEqual(self.expense(action='delete_expense').status_code,303)
        self.session.expire_all()
        self.assertIsNone(self.payment().expense)

    def test_invalid_expense_values_fail_without_writes(self):
        for changes in ({'amount':'12.51'},{'amount':'0'},{'amount':'-1'},{'amount':'1.001'}, {'eligible':'0'},
                        {'stream_id':str(uuid4())},{'category':'bad'},{'purpose':''},{'purpose':'x'*1001},
                        {'extra':'x'}, {'category':'vehicle_running','vehicle_key':''}):
            with self.subTest(changes=changes):
                self.assertEqual(self.expense(**changes).status_code,400)
                self.assertIsNone(self.payment().expense)

    def test_changed_bank_expense_is_excluded_and_needs_review(self):
        self.assertEqual(self.expense().status_code,303)
        self.payment().amount_minor=1500
        self.session.commit()
        records=tax_records(income_rules())
        self.assertEqual(records['deductions'],{})
        self.assertTrue(records['issues'])
        self.assertIn('Needs review',self.html())
        self.assertEqual(self.expense(amount='10.00').status_code,303)
        self.assertEqual(tax_records(income_rules())['deductions'][self.stream],1000)

    def test_stale_version_and_duplicate_fields_rejected(self):
        self.assertEqual(self.expense(version='0'*64).status_code,400)
        self.assertEqual(self.expense(version='é'*64).status_code,400)
        html=self.html()
        data=MultiDict(dict(action='save_mileage',csrf_token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1]))
        data.add('action','delete_mileage')
        self.assertEqual(self.client.post('/dashboard/deductions',data=data).status_code,400)

    def test_mileage_saved_and_calculated_and_deleted(self):
        identity=str(uuid4())
        self.assertEqual(self.mileage(entry_id=identity).status_code,303)
        self.assertEqual(tax_records(income_rules())['deductions'][self.stream],5500)
        self.assertEqual(self.mileage(entry_id=identity).status_code,400)
        html=self.html()
        token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1]
        self.assertEqual(self.client.post('/dashboard/deductions',data=dict(action='delete_mileage',entry_id=identity,csrf_token=token)).status_code,303)
        self.assertIsNone(self.session.get(MileageEntry,identity))

    def test_ineligible_mileage_fails_and_retains_form(self):
        for changes in ({'miles':'-1'},{'miles':'0'},{'miles':'nan'},{'miles':'1.001'}, {'miles':'100001'},
                        {'journey_date':'2026-04-05'},{'journey_date':'2027-04-06'}, {'journey_date':'2026-99-01'},
                        {'location':'france'},{'vehicle_type':'bicycle'},{'reimbursed':'1'}, {'reimbursed':'-1'},
                        {'eligible':'0'},{'vehicle_key':'<script>'},{'start_postcode':''}):
            self.assertEqual(self.mileage(**changes).status_code,400,changes)
        response=self.mileage(miles='oops')
        self.assertIn('value="oops"',response.get_data(as_text=True))
        self.assertIsNone(self.session.scalar(self.session.query(MileageEntry).statement))

    def test_mileage_and_running_costs_cannot_double_claim_either_order(self):
        self.assertEqual(self.mileage().status_code,303)
        self.assertEqual(self.expense(category='vehicle_running',vehicle_key='ab12cde').status_code,400)
        self.assertEqual(self.expense(category='parking_tolls').status_code,303)
        self.assertEqual(self.expense(category='vehicle_running',vehicle_key='OTHER').status_code,303)
        self.assertEqual(self.mileage(vehicle_key='OTHER').status_code,400)

    def test_logged_paye_cis_filter_year_currency_pending_and_stale(self):
        payment=self.payment()
        payment.direction='IN'
        payment.source='FASTER_PAYMENTS_IN'
        payment.amount_minor=80000
        payment.income=TransactionIncome(income_stream_id=self.stream,income_type='other',tax_treatment='cis',gross_minor=100000,
            tax_deducted_minor=20000,recorded_currency='GBP')
        self.session.commit()
        self.assertEqual(tax_records(income_rules())['credits'],{self.stream:20000})
        payment.income.needs_review=True
        self.session.commit()
        self.assertEqual(tax_records(income_rules())['credits'],{})
        payment.income.needs_review=False
        payment.status='PENDING'
        self.session.commit()
        self.assertEqual(tax_records(income_rules())['credits'],{})
        payment.status='SETTLED'
        payment.transaction_time=datetime(2026,4,5,12,tzinfo=timezone.utc)
        self.session.commit()
        self.assertEqual(tax_records(income_rules())['credits'],{})

    def test_owner_csrf_api_boundary_and_safe_escaping(self):
        self.assertEqual(self.client.post('/dashboard/deductions',data={'action':'save_mileage'}).status_code,400)
        self.assertEqual(self.client.get('/dashboard/deductions',headers=self.headers).status_code,302)
        self.assertEqual(self.expense(purpose='<script>alert(1)</script>').status_code,303)
        html=self.html()
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.bank.assert_not_called()
        with self.client.session_transaction() as cookie:
            cookie.clear()
        self.assertEqual(self.client.get('/dashboard/deductions').status_code,302)

    def test_archived_stream_rule_changes_and_scottish_scope(self):
        self.session.get(IncomeStream,self.stream).archived=True
        self.session.commit()
        self.assertEqual(self.mileage().status_code,400)
        self.session.get(IncomeStream,self.stream).archived=False
        self.session.commit()
        with patch('routes.deductions.cached_status',return_value={'status':'needs_review'}):
            self.assertEqual(self.mileage().status_code,400)
        self.assertEqual(self.mileage(location='scotland').status_code,303)
        self.assertTrue(any('Scottish' in issue for issue in tax_records(income_rules())['issues']))

    def test_database_rejects_negative_allowance_inputs(self):
        entry=MileageEntry(id=str(uuid4()),income_stream_id=self.stream,journey_date=date(2026,4,10),location='england',
                          vehicle_type='car_van',vehicle_key='TEST',miles=-1,purpose='Test',start_postcode='A',end_postcode='B')
        with self.session.begin_nested():
            with self.assertRaises(IntegrityError):
                self.session.add(entry)
                self.session.flush()

    def test_uk_tax_year_boundary_uses_local_midnight(self):
        payment=self.payment()
        payment.direction='IN'
        payment.source='FASTER_PAYMENTS_IN'
        payment.amount_minor=80000
        payment.income=TransactionIncome(income_stream_id=self.stream,income_type='other',tax_treatment='cis',gross_minor=100000,
            tax_deducted_minor=20000,recorded_currency='GBP')
        for stamp,included in ((datetime(2026,4,5,22,59,tzinfo=timezone.utc),False),
                               (datetime(2026,4,5,23,0,tzinfo=timezone.utc),True),
                               (datetime(2027,4,5,22,59,tzinfo=timezone.utc),True),
                               (datetime(2027,4,5,23,0,tzinfo=timezone.utc),False)):
            payment.transaction_time=stamp
            self.session.commit()
            self.assertEqual(bool(tax_records(income_rules())['credits']),included)

    def test_group_must_be_consistent_for_the_same_stream(self):
        self.assertEqual(self.mileage(mileage_group='Business').status_code,303)
        self.assertEqual(self.mileage(mileage_group='Other').status_code,400)
        self.assertEqual(self.mileage(mileage_group='BUSINESS').status_code,303)

    def test_employee_reimbursements_and_bicycle_are_supported(self):
        stream=self.session.get(IncomeStream,self.stream)
        stream.kind='employed'
        self.session.commit()
        self.assertEqual(self.mileage(reimbursed='45.00').status_code,303)
        self.assertEqual(tax_records(income_rules())['deductions'][self.stream],1000)
        self.assertEqual(self.mileage(vehicle_type='bicycle',vehicle_key='BIKE',miles='10',reimbursed='1').status_code,303)
        self.assertEqual(tax_records(income_rules())['deductions'][self.stream],1100)

    def test_deduction_prevents_incompatible_reclassification(self):
        from services.transactions.classification import save_classification
        self.assertEqual(self.expense().status_code,303)
        with self.assertRaises(ValueError):
            save_classification(self.payment(),'internal_transfer',None)

    def test_changed_publication_excludes_recorded_mileage(self):
        self.assertEqual(self.mileage().status_code,303)
        with patch('services.tax.records.cached_status',return_value={'status':'needs_review'}):
            result=tax_records(income_rules())
        self.assertEqual(result['deductions'],{})
        self.assertTrue(result['issues'])
