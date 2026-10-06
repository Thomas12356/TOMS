"""Irregular shifts, exact gross pay, forecast isolation and deduction ownership."""
import re
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import BadRequest
from models import IncomeShift, IncomeStream
from services.transactions.income_streams import annual_gross, stream_fields
from services.transactions.shifts import local_time, shift_fields
from deduction_support import DeductionOwnerCase


def fields(**changes):
    values = dict(starts_at='2026-10-06T09:00', ends_at='2026-10-06T12:00', unpaid_break_minutes='0',
                  payment_mode='hourly', hourly_rate='12.50', total_payment='', notes='Test block')
    values.update(changes)
    return MultiDict(values)


class ShiftCalculationTests(unittest.TestCase):
    def setUp(self):
        self.stream=SimpleNamespace(forecast_tax_year='2026-27', expected_gross_currency='GBP')

    def test_hourly_rate_breaks_total_and_rounding(self):
        self.assertEqual(shift_fields(fields(),self.stream)['gross_minor'],3750)
        self.assertEqual(shift_fields(fields(unpaid_break_minutes='30'),self.stream)['gross_minor'],3125)
        self.assertEqual(shift_fields(fields(ends_at='2026-10-06T09:01',hourly_rate='10'),self.stream)['gross_minor'],17)
        result=shift_fields(fields(payment_mode='total',hourly_rate='',total_payment='51.27'),self.stream)
        self.assertEqual(result['gross_minor'],5127)
        self.assertIsNone(result['hourly_rate_minor'])

    def test_overnight_and_dst_elapsed_time(self):
        overnight=shift_fields(fields(starts_at='2026-10-06T22:00',ends_at='2026-10-07T02:00'),self.stream)
        self.assertEqual(overnight['gross_minor'],5000)
        # Clocks go back in October: 00:00 to 03:00 is four actual hours.
        autumn=shift_fields(fields(starts_at='2026-10-25T00:00',ends_at='2026-10-25T03:00'),self.stream)
        self.assertEqual(autumn['gross_minor'],5000)
        spring=shift_fields(fields(starts_at='2027-03-28T00:00',ends_at='2027-03-28T03:00'),self.stream)
        self.assertEqual(spring['gross_minor'],2500)
        self.assertEqual(local_time('2026-10-06T09:00'),datetime(2026,10,6,8,tzinfo=timezone.utc))

    def test_ambiguous_nonexistent_invalid_times_rejected(self):
        for value in ('2026-10-25T01:30','2027-03-28T01:30','2026-02-30T09:00','2026-10-06T09:00:00','bad'):
            with self.assertRaises(ValueError):
                local_time(value)

    def test_invalid_duration_amounts_modes_and_tax_year(self):
        for changes in ({'ends_at':'2026-10-06T09:00'}, {'ends_at':'2026-10-06T08:59'},
                        {'ends_at':'2026-10-07T09:01'}, {'unpaid_break_minutes':'180'}, {'unpaid_break_minutes':'-1'},
                        {'hourly_rate':'0'}, {'hourly_rate':'-1'}, {'hourly_rate':'1.001'}, {'hourly_rate':'1e3'},
                        {'total_payment':'12'}, {'payment_mode':'invalid'}, {'notes':'x'*1001},
                        {'starts_at':'2026-04-05T09:00','ends_at':'2026-04-05T12:00'},
                        {'starts_at':'2027-04-05T22:00','ends_at':'2027-04-06T01:00'},
                        {'hourly_rate':'999999999999999999999999'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                shift_fields(fields(**changes),self.stream)
        duplicate=fields();duplicate.add('hourly_rate','1')
        with self.assertRaises(ValueError):
            shift_fields(duplicate,self.stream)

    def test_shift_pattern_requires_no_regular_rate_or_holiday(self):
        form=MultiDict(dict(action='create',name='Flex',kind='self_employed',income_mode='shifts',
                            expected_gross_currency='GBP',forecast_tax_year='2026-27'))
        _,_,_,forecast=stream_fields(form)
        self.assertEqual(forecast['income_mode'],'shifts')
        self.assertEqual(forecast['expected_gross_minor'],0)
        self.assertEqual(forecast['unpaid_holiday_weeks'],0)
        self.assertEqual(annual_gross(SimpleNamespace(**forecast,shift_gross_minor=5127)),5127)
        form['income_mode']='bad'
        with self.assertRaises(BadRequest):
            stream_fields(form)

    def test_overtime_added_once_after_regular_absence_adjustment(self):
        stream = SimpleNamespace(income_mode='forecast', expected_gross_minor=10000,
            expected_gross_period='weekly', unpaid_holiday_weeks=2,
            overtime_gross_minor=5127, forecast_tax_year='2026-27')
        self.assertEqual(annual_gross(stream), 505127)
        stream.expected_gross_minor = None
        self.assertIsNone(annual_gross(stream))

    def test_employment_overtime_raises_business_reserve_using_combined_bands(self):
        from services.tax.estimate import estimate_streams
        from services.tax.rules import reviewed_rules
        employment = SimpleNamespace(id='job', name='Employment', kind='employed', income_mode='forecast',
            expected_gross_minor=4000000, expected_gross_period='yearly', expected_gross_currency='GBP',
            unpaid_holiday_weeks=0, overtime_gross_minor=2000000, forecast_tax_year='2026-27')
        business = SimpleNamespace(id='work', name='Business', kind='self_employed', income_mode='forecast',
            expected_gross_minor=2000000, expected_gross_period='yearly', expected_gross_currency='GBP',
            unpaid_holiday_weeks=0, forecast_tax_year='2026-27')
        result = estimate_streams([employment, business], reviewed_rules(), credits={'work': 10000})
        self.assertEqual(result['gross_minor'], 8000000)
        self.assertEqual(result['reserve_minor'], 790000)
        self.assertEqual(str(result['reserve_percent']), '39.70')


class ShiftPageTests(DeductionOwnerCase):
    """Reuse the rollback-only owner/stream fixture, not its inherited tests."""
    def setUp(self):
        super().setUp()
        self.shift_url=f'/dashboard/income-streams/{self.stream}/shifts'

    def post_shift(self,action='create',identity=None,**changes):
        if action != 'create':
            path=self.shift_url+'?edit='+identity
        else:
            path=self.shift_url
        html=self.client.get(path).get_data(as_text=True)
        values=dict(fields())
        mode = re.search(r'name="income_mode" value="([^"]*)"', html)
        values.update(action=action,shift_id=identity or str(uuid4()),
                      income_mode=mode[1] if mode else 'forecast',
                      version=(re.search(r'name="version" value="([^"]*)"',html)[1] if 'name="version"' in html else ''),
                      csrf_token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1])
        values.update(changes)
        return self.client.post(self.shift_url,data=values)

    def test_create_edit_delete_and_duplicate_submission(self):
        identity=str(uuid4())
        self.assertEqual(self.post_shift(identity=identity).status_code,303)
        self.assertEqual(self.session.get(IncomeShift,identity).gross_minor,3750)
        self.assertEqual(self.post_shift(identity=identity).status_code,400)
        self.assertEqual(self.post_shift('update',identity,hourly_rate='15').status_code,303)
        self.assertEqual(self.session.get(IncomeShift,identity).gross_minor,4500)
        self.assertEqual(self.post_shift('delete',identity).status_code,303)
        self.assertIsNone(self.session.get(IncomeShift,identity))

    def test_overtime_updates_forecast_and_tax_without_bank_receipts(self):
        from services.transactions.shifts import attach_shift_totals
        from services.tax.estimate import estimate_streams, income_tax
        from services.tax.rules import reviewed_rules
        stream = self.session.get(IncomeStream, self.stream)
        stream.kind = 'employed'
        self.session.commit()
        identity = str(uuid4())
        self.assertEqual(self.post_shift(identity=identity).status_code, 303)
        self.assertTrue(self.session.get(IncomeShift, identity).is_overtime)
        attach_shift_totals([stream])
        self.assertEqual(annual_gross(stream), 3003750)
        estimate = estimate_streams([stream], reviewed_rules())
        self.assertEqual(estimate['gross_minor'], 3003750)
        self.assertEqual(estimate['calculation']['tax_minor'], income_tax(3003750, reviewed_rules())['tax_minor'])
        self.assertEqual(self.post_shift('update', identity, hourly_rate='15').status_code, 303)
        attach_shift_totals([stream])
        self.assertEqual(annual_gross(stream), 3004500)
        self.assertEqual(self.post_shift('delete', identity).status_code, 303)
        attach_shift_totals([stream])
        self.assertEqual(annual_gross(stream), 3000000)

    def test_regular_forecast_excludes_ordinary_work_records_and_mode_switch_does_not_duplicate(self):
        from services.transactions.shifts import attach_shift_totals
        stream = self.session.get(IncomeStream, self.stream)
        stream.income_mode = 'shifts'
        self.session.commit()
        ordinary, extra = str(uuid4()), str(uuid4())
        self.assertEqual(self.post_shift(identity=ordinary).status_code, 303)
        self.assertFalse(self.session.get(IncomeShift, ordinary).is_overtime)
        stream.income_mode = 'forecast'
        self.session.commit()
        self.assertEqual(self.post_shift(identity=extra).status_code, 303)
        attach_shift_totals([stream])
        self.assertEqual(annual_gross(stream), 3003750)
        stream.income_mode = 'shifts'
        self.session.commit()
        attach_shift_totals([stream])
        self.assertEqual(annual_gross(stream), 7500)

    def test_overtime_button_opens_form_and_notes_are_escaped(self):
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        self.assertTrue('>Add overtime</a>' in html)
        self.assertFalse('>Manage shifts</a>' in html)
        html = self.client.get(self.shift_url + '?add=1').get_data(as_text=True)
        self.assertTrue('<details class="shift-create" open>' in html)
        attack = '<img src=x onerror="alert(1)">'
        self.assertEqual(self.post_shift(notes=attack).status_code, 303)
        html = self.client.get(self.shift_url).get_data(as_text=True)
        self.assertFalse('<img' in html)
        self.assertTrue('&lt;img' in html)
        self.assertEqual(self.client.get(self.shift_url + '?add=bad').status_code, 400)
        self.assertEqual(self.post_shift(is_overtime='0').status_code, 400)
        self.assertEqual(self.post_shift(income_mode='shifts').status_code, 400)

    def test_locked_shift_refresh_rejects_change_after_form_snapshot(self):
        from unittest.mock import patch
        identity = str(uuid4())
        self.assertEqual(self.post_shift(identity=identity).status_code, 303)
        original = self.session.scalar
        changed = False

        def read_after_change(statement, *args, **kwargs):
            nonlocal changed
            if statement._for_update_arg is not None and 'income_shifts' in str(statement) and not changed:
                changed = True
                # Bypass the identity map, like a competing committed writer.
                self.session.connection().execute(IncomeShift.__table__.update().where(
                    IncomeShift.id == identity).values(gross_minor=4000))
            return original(statement, *args, **kwargs)

        from services.database.connection import db
        with patch.object(db.session, 'scalar', side_effect=read_after_change):
            self.assertEqual(self.post_shift('update', identity, hourly_rate='15').status_code, 400)
        self.assertTrue(changed)

    def test_shift_totals_replace_regular_forecast_without_bank_income_added(self):
        identity=str(uuid4())
        self.assertEqual(self.post_shift(identity=identity,hourly_rate='',payment_mode='total',total_payment='30000').status_code,303)
        self.assertEqual(self.client.get('/dashboard/tax-estimate').status_code,200)
        stream=self.session.get(IncomeStream,self.stream)
        stream.income_mode='shifts'
        self.session.commit()
        from services.transactions.shifts import attach_shift_totals
        attach_shift_totals([stream])
        self.assertEqual(annual_gross(stream),3000000)
        self.assertEqual(self.client.get('/dashboard/tax-estimate').status_code,200)
        html=self.client.get('/dashboard/income-streams').get_data(as_text=True)
        self.assertTrue('Individual shifts' in html)
        self.assertTrue('GBP 30,000.00 entered gross' in html)

    def test_links_belong_to_stream_and_do_not_double_count_deductions(self):
        identity=str(uuid4())
        self.assertEqual(self.post_shift(identity=identity).status_code,303)
        self.assertEqual(self.expense(shift_id=identity).status_code,303)
        self.assertEqual(self.payment().expense.shift_id,identity)
        self.assertEqual(self.mileage(shift_id=identity).status_code,303)
        from services.tax.records import tax_records
        from services.tax.rules import reviewed_rules
        self.assertEqual(tax_records(reviewed_rules())['deductions'][self.stream],6500)
        self.assertEqual(self.post_shift('delete',identity).status_code,400)
        self.assertIn('Unlink',self.post_shift('delete',identity).get_data(as_text=True))
        self.assertEqual(self.expense(shift_id='').status_code,303)
        self.assertIsNone(self.payment().expense.shift_id)

    def test_cross_stream_shift_links_and_guessed_id_rejected(self):
        identity=str(uuid4())
        self.assertEqual(self.post_shift(identity=identity).status_code,303)
        other=str(uuid4())
        self.session.add(IncomeStream(id=other,name='Other',kind='self_employed'))
        self.session.commit()
        self.assertEqual(self.expense(stream_id=other,shift_id=identity).status_code,400)
        self.assertEqual(self.mileage(stream_id=other,shift_id=identity).status_code,400)
        self.assertEqual(self.expense(shift_id=str(uuid4())).status_code,400)
        self.assertEqual(self.mileage(shift_id=str(uuid4())).status_code,400)

    def test_shift_context_prefills_deduction_forms(self):
        identity=str(uuid4())
        self.assertEqual(self.post_shift(identity=identity).status_code,303)
        for kind in ('expense','mileage'):
            html=self.client.get('/dashboard/deductions?type='+kind+'&shift='+identity+('&expense='+self.key if kind == 'expense' else '')).get_data(as_text=True)
            self.assertTrue(f'value="{identity}"' in html)
            self.assertTrue(f'value="{self.stream}" selected' in html)
        self.assertEqual(self.client.get('/dashboard/deductions?shift=bad').status_code,400)

    def test_mileage_suggestions_use_local_shift_date_and_preserve_submitted_edits(self):
        identity = str(uuid4())
        self.assertEqual(self.post_shift(identity=identity, starts_at='2026-10-05T23:30',
            ends_at='2026-10-06T01:00', notes='Client delivery').status_code, 303)
        html = self.client.get('/dashboard/deductions?type=mileage&shift=' + identity).get_data(as_text=True)
        self.assertTrue('value="2026-10-05" required' in html)
        self.assertTrue('Business travel for Test business: Client delivery</textarea>' in html)
        response = self.mileage(shift_id=identity, journey_date='2026-04-10',
            purpose='Actual journey to customer', miles='bad')
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertTrue('value="2026-04-10" required' in html)
        self.assertTrue('Actual journey to customer</textarea>' in html)
        self.assertFalse('Business travel for Test business: Client delivery</textarea>' in html)

    def test_validation_preserves_input_and_stale_versions_rejected(self):
        response=self.post_shift(hourly_rate='oops')
        self.assertEqual(response.status_code,400)
        self.assertIn('value="oops"',response.get_data(as_text=True))
        identity=str(uuid4())
        self.assertEqual(self.post_shift(identity=identity).status_code,303)
        self.assertEqual(self.post_shift('update',identity,version='0'*64).status_code,400)
        self.assertEqual(self.post_shift('update',identity,version='é'*64).status_code,400)

    def test_archived_csrf_and_api_guards(self):
        self.assertEqual(self.client.post(self.shift_url,data={'action':'create'}).status_code,400)
        self.assertEqual(self.client.get(self.shift_url,headers=self.headers).status_code,302)
        self.session.get(IncomeStream,self.stream).archived=True
        self.session.commit()
        self.assertEqual(self.post_shift().status_code,400)
        self.bank.assert_not_called()

    def test_currency_change_is_blocked_until_shifts_removed(self):
        identity=str(uuid4())
        self.assertEqual(self.post_shift(identity=identity).status_code,303)
        html=self.client.get('/dashboard/income-streams').get_data(as_text=True)
        data=dict(action='update',stream_id=self.stream,name='Test business',kind='self_employed',income_mode='shifts',
                  expected_gross_currency='EUR',forecast_tax_year='2026-27',
                  csrf_token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1])
        self.assertEqual(self.client.post('/dashboard/income-streams',data=data).status_code,400)

    def test_existing_mileage_can_be_linked_and_unlinked_without_reclaiming(self):
        from models import MileageEntry
        from services.tax.deduction_forms import mileage_version
        identity=str(uuid4())
        self.assertEqual(self.post_shift(identity=identity).status_code,303)
        mileage_id=str(uuid4())
        self.assertEqual(self.mileage(entry_id=mileage_id).status_code,303)
        entry=self.session.get(MileageEntry,mileage_id)
        for target in (identity,''):
            html=self.html()
            token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1]
            values=dict(action='link_mileage_shift',entry_id=mileage_id,shift_id=target,version=mileage_version(entry),csrf_token=token)
            self.assertEqual(self.client.post('/dashboard/deductions', data=dict(values, version='é'*64)).status_code, 400)
            self.assertEqual(self.client.post('/dashboard/deductions',data=values).status_code,303)
            self.assertEqual(entry.shift_id,target or None)
        self.assertEqual(self.post_shift('delete',identity).status_code,303)

    def test_other_stream_edit_cannot_expose_or_change_shift(self):
        identity=str(uuid4())
        self.assertEqual(self.post_shift(identity=identity,notes='Other stream shift sentinel').status_code,303)
        other=str(uuid4())
        self.session.add(IncomeStream(id=other,name='Another',kind='employed'))
        self.session.commit()
        response=self.client.get(f'/dashboard/income-streams/{other}/shifts?edit={identity}')
        self.assertEqual(response.status_code,400)
        self.assertFalse('Other stream shift sentinel' in response.get_data(as_text=True))

    def test_switching_mode_preserves_regular_forecast(self):
        html=self.client.get('/dashboard/income-streams').get_data(as_text=True)
        values=dict(action='update',stream_id=self.stream,name='Test business',kind='self_employed',income_mode='shifts',
                    expected_gross_currency='GBP',forecast_tax_year='2026-27',
                    csrf_token=re.search(r'name="csrf_token" value="([^"]+)"',html)[1])
        self.assertEqual(self.client.post('/dashboard/income-streams',data=values).status_code,303)
        stream=self.session.get(IncomeStream,self.stream)
        self.assertEqual(stream.expected_gross_minor,3000000)
        self.assertEqual(stream.income_mode,'shifts')
        from services.transactions.shifts import attach_shift_totals
        attach_shift_totals([stream])
        self.assertEqual(annual_gross(stream),0)

    def test_database_rejects_cross_stream_deduction_link(self):
        from sqlalchemy.exc import IntegrityError
        identity=str(uuid4())
        self.assertEqual(self.post_shift(identity=identity).status_code,303)
        self.assertEqual(self.expense().status_code,303)
        other=str(uuid4())
        self.session.add(IncomeStream(id=other,name='Another',kind='self_employed'))
        self.session.commit()
        with self.assertRaises(IntegrityError):
            with self.session.begin_nested():
                self.payment().expense.income_stream_id=other
                self.payment().expense.shift_id=identity
                self.session.flush()
