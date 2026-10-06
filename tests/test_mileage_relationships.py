"""Stream-level relationships, historical recalculation and write boundaries."""
import re
from uuid import uuid4

from werkzeug.datastructures import MultiDict

from models import IncomeStream
from services.tax.mileage import mileage_allowances
from services.tax.records import tax_records
from services.tax.rules import reviewed_rules
from deduction_support import DeductionOwnerCase


class MileageRelationshipTests(DeductionOwnerCase):
    def add_stream(self, kind='self_employed', name='Second source'):
        stream = IncomeStream(id=str(uuid4()), name=name, kind=kind,
            expected_gross_minor=3000000, expected_gross_period='yearly', expected_gross_currency='GBP')
        self.session.add(stream)
        self.session.commit()
        return stream

    def stream_form(self, identity=None, **changes):
        stream = self.session.get(IncomeStream, identity or self.stream)
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        fields = dict(action='update', stream_id=stream.id, name=stream.name, kind=stream.kind,
            income_mode='forecast', expected_gross='30000', expected_gross_period='yearly',
            expected_gross_currency='GBP', unpaid_holiday='0', unpaid_holiday_unit='weeks',
            forecast_tax_year='2026-27', mileage_with_stream='',
            csrf_token=re.search(r'name="csrf_token" value="([^"]+)"', html)[1])
        fields.update(changes)
        return fields

    def link(self, source, target):
        return self.client.post('/dashboard/income-streams',
            data=self.stream_form(source, mileage_with_stream=target or ''))

    def test_existing_journeys_share_threshold_after_linking(self):
        other = self.add_stream(kind='cis')
        self.assertEqual(self.mileage(miles='8000').status_code, 303)
        self.assertEqual(self.mileage(stream_id=other.id, miles='4000', journey_date='2026-04-11').status_code, 303)
        before = tax_records(reviewed_rules())['deductions']
        self.assertEqual(before[self.stream], 440000)
        self.assertEqual(before[other.id], 220000)
        self.assertEqual(self.link(other.id, self.stream).status_code, 303)
        after = tax_records(reviewed_rules())['deductions']
        self.assertEqual(after[self.stream], 440000)
        self.assertEqual(after[other.id], 160000)
        self.assertEqual(self.client.get('/dashboard/tax-estimate').status_code, 200)
        self.assertEqual(self.link(other.id, None).status_code, 303)
        self.assertEqual(tax_records(reviewed_rules())['deductions'], before)

    def test_create_can_join_an_existing_business(self):
        fields = self.stream_form(mileage_with_stream=self.stream, name='New linked source', action='create')
        fields.pop('stream_id')
        self.assertEqual(self.client.post('/dashboard/income-streams', data=fields).status_code, 303)
        from services.database.connection import db
        created = self.session.scalar(db.select(IncomeStream).where(IncomeStream.name == 'New linked source'))
        self.assertEqual(created.mileage_pool_id, self.session.get(IncomeStream, self.stream).mileage_pool_id)

    def test_same_employer_reimbursements_offset_shared_annual_amount(self):
        first = self.session.get(IncomeStream, self.stream)
        first.kind = 'employed'
        self.session.commit()
        other = self.add_stream(kind='employed')
        self.assertEqual(self.mileage(miles='100', reimbursed='100').status_code, 303)
        self.assertEqual(self.mileage(stream_id=other.id, miles='100').status_code, 303)
        self.assertEqual(sum(tax_records(reviewed_rules())['deductions'].values()), 5500)
        self.assertEqual(self.link(other.id, first.id).status_code, 303)
        self.assertEqual(sum(tax_records(reviewed_rules())['deductions'].values()), 1000)

    def test_chained_selection_and_moving_one_stream_does_not_move_others(self):
        other, third = self.add_stream(), self.add_stream(name='Third source')
        self.assertEqual(self.link(other.id, self.stream).status_code, 303)
        self.assertEqual(self.link(third.id, other.id).status_code, 303)
        shared = other.mileage_pool_id
        self.assertEqual(third.mileage_pool_id, shared)
        self.assertEqual(self.link(self.stream, None).status_code, 303)
        self.assertNotEqual(self.session.get(IncomeStream, self.stream).mileage_pool_id, shared)
        self.assertEqual(other.mileage_pool_id, third.mileage_pool_id)

    def test_archiving_keeps_relationship_and_choice(self):
        other = self.add_stream()
        self.assertEqual(self.link(other.id, self.stream).status_code, 303)
        fields = self.stream_form(action='archive')
        self.assertEqual(self.client.post('/dashboard/income-streams', data=fields).status_code, 303)
        self.assertEqual(other.mileage_pool_id, self.session.get(IncomeStream, self.stream).mileage_pool_id)
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        self.assertTrue('counted with Second source' in html)

    def test_forged_unknown_self_and_incompatible_links_are_rejected(self):
        incompatible = self.add_stream(kind='employed')
        original = self.session.get(IncomeStream, self.stream).mileage_pool_id
        for target in ('bad', str(uuid4()), self.stream, incompatible.id):
            self.assertEqual(self.link(self.stream, target).status_code, 400)
            self.assertEqual(self.session.get(IncomeStream, self.stream).mileage_pool_id, original)
        fields = self.stream_form(mileage_pool_id=str(uuid4()))
        self.assertEqual(self.client.post('/dashboard/income-streams', data=fields).status_code, 400)
        fields = MultiDict(self.stream_form())
        fields.add('mileage_with_stream', incompatible.id)
        self.assertEqual(self.client.post('/dashboard/income-streams', data=fields).status_code, 400)
        fields = self.stream_form()
        fields.pop('csrf_token')
        self.assertEqual(self.client.post('/dashboard/income-streams', data=fields).status_code, 400)
        self.assertEqual(self.client.post('/dashboard/income-streams', data=self.stream_form(), headers=self.headers).status_code, 302)
        self.bank.assert_not_called()

    def test_related_pool_does_not_mix_vehicle_types(self):
        from models import MileageEntry
        from services.database.connection import db
        other = self.add_stream()
        self.assertEqual(self.link(other.id, self.stream).status_code, 303)
        self.assertEqual(self.mileage(miles='10000').status_code, 303)
        self.assertEqual(self.mileage(stream_id=other.id, miles='10', vehicle_type='motorcycle', vehicle_key='MOTORCYCLE').status_code, 303)
        entries = self.session.scalars(db.select(MileageEntry)).all()
        totals = mileage_allowances(entries)
        self.assertEqual(totals[self.stream], 550000)
        self.assertEqual(totals[other.id], 240)
