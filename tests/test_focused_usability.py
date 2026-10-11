"""History bounds must not truncate financial evidence or unsafe return contexts."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import re
from uuid import uuid4

from flask import template_rendered

from app import app
from deduction_support import DeductionOwnerCase
from models import IncomeShift, IncomeStream, MileageEntry
from services.database.connection import db
from services.tax.mileage import mileage_allowances


@contextmanager
def rendered():
    contexts = []
    def capture(sender, template, context, **extra):
        contexts.append(context)
    template_rendered.connect(capture, app)
    try:
        yield contexts
    finally:
        template_rendered.disconnect(capture, app)


class FocusedUsabilityTests(DeductionOwnerCase):
    def work(self, count=25):
        records = []
        for index in range(count):
            starts = datetime(2026, 5, 1, 8, tzinfo=timezone.utc) + timedelta(days=index)
            row = IncomeShift(id=str(uuid4()), income_stream_id=self.stream, starts_at=starts,
                ends_at=starts + timedelta(hours=2), unpaid_break_minutes=0, payment_mode='hourly',
                hourly_rate_minor=1250, gross_minor=2500, currency='GBP', is_overtime=True, notes=f'Work {index}')
            self.session.add(row)
            records.append(row)
        self.session.commit()
        return records

    def journeys(self, count=25):
        records = []
        for index in range(count):
            row = MileageEntry(id=str(uuid4()), income_stream_id=self.stream,
                journey_date=date(2026, 5, 1) + timedelta(days=index), location='england', vehicle_type='car_van',
                vehicle_key='AB12CDE', miles=Decimal('500'), purpose=f'Journey {index}',
                start_postcode='SW1A 1AA', end_postcode='SW1A 2AA', reimbursed_minor=0)
            self.session.add(row)
            records.append(row)
        self.session.commit()
        return records

    def csrf(self, path):
        return re.search(r'name="csrf_token" value="([^"]+)"', self.client.get(path).get_data(as_text=True))[1]

    def test_work_pagination_keeps_full_integer_total(self):
        records = self.work()
        path = f'/dashboard/income-streams/{self.stream}/shifts'
        for page, length in ((1,20),(2,5),(3,0)):
            with rendered() as contexts:
                response = self.client.get(path+f'?history_page={page}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(contexts[-1]['entries']), length)
            self.assertEqual(contexts[-1]['total'], 62500)
            self.assertIsInstance(contexts[-1]['total'], int)
            self.assertEqual(contexts[-1]['record_count'], len(records))

    def test_work_history_uses_london_date_without_changing_total(self):
        row = self.work(1)[0]
        row.starts_at = datetime(2026, 5, 1, 23, 30, tzinfo=timezone.utc)
        row.ends_at = datetime(2026, 5, 2, 1, 30, tzinfo=timezone.utc)
        self.session.commit()
        path = f'/dashboard/income-streams/{self.stream}/shifts'
        for day, count in (('2026-05-01',0), ('2026-05-02',1)):
            with rendered() as contexts:
                self.assertEqual(self.client.get(path+f'?history_from={day}&history_to={day}').status_code,200)
            self.assertEqual(len(contexts[-1]['entries']),count)
            self.assertEqual(contexts[-1]['total'],2500)

    def test_mileage_pages_and_filters_keep_full_threshold_and_reimbursement(self):
        rows = self.journeys()
        rows[-1].reimbursed_minor=10000
        self.session.commit()
        expected=mileage_allowances(rows)
        for query,count in (('history_page=1',20),('history_page=2',5),
                            ('history_from=2026-05-25',1),('history_from=2026-06-01',0)):
            with rendered() as contexts:
                response=self.client.get('/dashboard/deductions?'+query)
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(contexts[-1]['journeys']),count)
            self.assertEqual(contexts[-1]['allowance'],expected)

    def test_history_source_filter_isolates_display_not_calculation(self):
        rows=self.journeys(1)
        other=str(uuid4())
        self.session.add(IncomeStream(id=other,name='Other',kind='self_employed'))
        self.session.commit()
        with rendered() as contexts:
            response=self.client.get('/dashboard/deductions?history_source='+other)
        self.assertEqual(response.status_code,200)
        self.assertEqual(contexts[-1]['journeys'],[])
        self.assertEqual(contexts[-1]['allowance'],mileage_allowances(rows))

    def test_invalid_and_external_return_and_history_filters_rejected(self):
        for query in ('history_page=0','history_page=2147483648','history_page=1&history_page=2',
                      'history_from=2026-02-30','history_from=2026-05-02&history_to=2026-05-01',
                      'history_source='+str(uuid4()),'return_to=https://example.com',
                      'return_to=source&source='+str(uuid4()), 'source='+self.stream):
            self.assertEqual(self.client.get('/dashboard/deductions?'+query).status_code,400,query)
        path=f'/dashboard/income-streams/{self.stream}/shifts'
        self.assertEqual(self.client.get(path+'?return_to=source&source='+str(uuid4())).status_code,400)

    def test_work_save_returns_to_source_and_error_retains_context(self):
        path=f'/dashboard/income-streams/{self.stream}/shifts?return_to=source&source={self.stream}&add=1'
        data=dict(action='create',shift_id=str(uuid4()),csrf_token=self.csrf(path),income_mode='forecast',
            starts_at='2026-05-20T08:00',ends_at='2026-05-20T10:00',unpaid_break_minutes='0',
            payment_mode='hourly',hourly_rate='oops',total_payment='',notes='Retain me')
        bad=self.client.post(path,data=data)
        self.assertEqual(bad.status_code,400)
        self.assertIn('Retain me',bad.get_data(as_text=True))
        data['hourly_rate']='12.50'
        response=self.client.post(path,data=data)
        self.assertEqual(response.status_code,303)
        self.assertEqual(response.location,'/dashboard/income-streams/'+self.stream)

    def test_mileage_save_and_validation_return_to_source(self):
        path=f'/dashboard/deductions?type=mileage&return_to=source&source={self.stream}'
        data=dict(action='save_mileage',entry_id=str(uuid4()),csrf_token=self.csrf(path),stream_id=self.stream,
            journey_date='2026-05-20',location='england',vehicle_type='car_van',vehicle_key='AB12CDE',
            miles='12',purpose='Client visit',start_postcode='SW1A 1AA',end_postcode='SW1A 2AA',reimbursed='0')
        bad=self.client.post(path,data=data)
        self.assertEqual(bad.status_code,400)
        self.assertIn('Back to income source',bad.get_data(as_text=True))
        self.assertIn('Client visit',bad.get_data(as_text=True))
        data['eligible']='1'
        response=self.client.post(path,data=data)
        self.assertEqual(response.status_code,303)
        self.assertEqual(response.location,'/dashboard/income-streams/'+self.stream)

    def test_focused_setup_hides_existing_list_and_retains_invalid_input(self):
        path='/dashboard/income-streams?setup=1'
        html=self.client.get(path).get_data(as_text=True)
        self.assertNotIn('id="your-streams-heading"',html)
        self.assertIn('Continue to income source',html)
        bad=self.client.post(path,data=dict(action='start',csrf_token=self.csrf(path),name='Keep source',kind='invalid'))
        self.assertEqual(bad.status_code,400)
        self.assertIn('Keep source',bad.get_data(as_text=True))
        self.assertNotIn('id="your-streams-heading"',bad.get_data(as_text=True))

    def test_old_shift_links_remain_available_outside_recent_selector_window(self):
        records=self.work(205)
        journey=self.journeys(1)[0]
        journey.shift_id=records[0].id
        self.session.commit()
        with rendered() as contexts:
            self.assertEqual(self.client.get('/dashboard/deductions').status_code,200)
        self.assertEqual(len(contexts[-1]['available_shifts']),201)
        self.assertIn(records[0].id,contexts[-1]['shift_names'])
        self.assertEqual(self.session.scalar(db.select(db.func.count()).select_from(IncomeShift)),205)
