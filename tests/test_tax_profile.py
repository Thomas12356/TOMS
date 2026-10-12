"""Annual jurisdiction, stale writes, safe targets and real/sample separation."""
from datetime import date
from decimal import Decimal
import io
import json
import os
import re
import unittest
from zipfile import ZipFile

from sqlalchemy import text
from sqlalchemy.orm import Session
from werkzeug.datastructures import MultiDict

from models import MileageEntry, TaxProfile
from services.web.test_data import sample_id
import test_test_data as sample_support


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS') == '1', 'Requires disposable PostgreSQL schemas.')
class TaxProfileTests(unittest.TestCase):
    def setUp(self):
        self.fixture = sample_support.TestDataTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.client = self.fixture.client
        self.assertEqual(self.fixture.toggle(True).status_code, 303)

    def form(self, **changes):
        response = self.client.get('/dashboard/tax-profile')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        fields = dict(jurisdiction='england', confirmed='1',
            csrf_token=re.search(r'name="csrf_token" value="([^"]+)"', html)[1],
            version=re.search(r'name="version" value="([^"]+)"', html)[1])
        fields.update(changes)
        return fields

    def save(self, **changes):
        return self.client.post('/dashboard/tax-profile', data=self.form(**changes))

    def plan(self):
        response = self.client.get('/dashboard/tax-export/download')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        with ZipFile(io.BytesIO(response.data)) as archive:
            return json.loads(archive.read('report.json'))['calculation']

    def test_supported_jurisdictions_keep_same_calculation_and_export_evidence(self):
        before = self.plan()
        for jurisdiction in ('england', 'wales', 'northern_ireland'):
            self.assertEqual(self.save(jurisdiction=jurisdiction).status_code, 303)
            after = self.plan()
            self.assertEqual(after['calculation'], before['calculation'])
            self.assertEqual(after['ni_minor'], before['ni_minor'])
            self.assertEqual(after['tax_profile']['jurisdiction'], jurisdiction)
            self.assertIsNotNone(after['tax_profile']['confirmed_at'])
        self.fixture.bank.assert_not_called()

    def test_unknown_scottish_and_other_profiles_never_publish_supported_tax_targets(self):
        for jurisdiction in ('scotland', 'other', 'unknown'):
            self.assertEqual(self.save(jurisdiction=jurisdiction).status_code, 303)
            plan = self.plan()
            self.assertTrue(plan['blockers'])
            for field in ('calculation', 'reserve_minor', 'received_reserve_minor', 'total_liability_minor', 'ni_minor'):
                self.assertIsNone(plan[field], (jurisdiction, field))
            html = self.client.get('/dashboard/tax-estimate').get_data(as_text=True)
            self.assertIn('Review tax profile', html)
            self.assertNotIn('class="tax-totals"', html)
            money = self.client.get('/dashboard/account-money').get_data(as_text=True)
            self.assertIn('Suggested tax / NI reserve needs review', money)
            notices = self.client.get('/dashboard/income-notifications').get_json()
            self.assertTrue(all(row['tax_minor'] is None for row in notices['notices']))

    def test_scottish_journey_does_not_change_confirmed_english_tax_status(self):
        engine = self.fixture.admin.execution_options(schema_translate_map={'toms': self.fixture.demo_schema})
        with Session(engine) as saved, saved.begin():
            saved.add(MileageEntry(id=sample_id(880), income_stream_id=sample_id(22),
                journey_date=date(2026, 4, 10), location='scotland', vehicle_type='car_van',
                vehicle_key='SAMPLE', miles=Decimal(100), purpose='Sample Scottish business journey',
                start_postcode='EH1 1AA', end_postcode='EH2 1AA', reimbursed_minor=0))
        plan = self.plan()
        self.assertIsNotNone(plan['calculation'])
        self.assertEqual(plan['deductions_minor'], 5500)
        self.assertEqual(plan['tax_profile']['jurisdiction'], 'england')
        self.assertFalse(plan['blockers'])

    def test_missing_real_profile_is_unknown_and_sample_edits_stay_isolated(self):
        self.assertEqual(self.save(jurisdiction='scotland').status_code, 303)
        self.assertEqual(self.fixture.toggle(False).status_code, 303)
        self.assertIn('Saved profile: <strong>Not confirmed', self.client.get('/dashboard/tax-profile').get_data(as_text=True))
        self.assertEqual(self.fixture.live_value('SELECT count(*) FROM SCHEMA.tax_profiles'), 0)
        self.assertEqual(self.save(jurisdiction='wales').status_code, 303)
        self.assertEqual(self.fixture.live_value('SELECT jurisdiction FROM SCHEMA.tax_profiles'), 'wales')
        self.assertEqual(self.fixture.toggle(True).status_code, 303)
        self.assertEqual(self.plan()['tax_profile']['jurisdiction'], 'scotland')
        # Re-entering sample mode must preserve the owner's sample choice.
        self.assertEqual(self.save(jurisdiction='unknown').status_code, 303)
        self.fixture.toggle(False)
        self.fixture.toggle(True)
        self.assertEqual(self.plan()['tax_profile']['jurisdiction'], 'unknown')

    def test_stale_form_cannot_overwrite_new_profile_or_silently_retry(self):
        stale = self.form(jurisdiction='scotland')
        self.assertEqual(self.save(jurisdiction='wales').status_code, 303)
        response = self.client.post('/dashboard/tax-profile', data=stale)
        self.assertEqual(response.status_code, 409)
        self.assertIn('Reload current profile', response.get_data(as_text=True))
        self.assertNotIn('Save tax profile</button>', response.get_data(as_text=True))
        self.assertEqual(self.plan()['tax_profile']['jurisdiction'], 'wales')

    def test_two_first_save_forms_cannot_replace_the_new_profile(self):
        self.assertEqual(self.fixture.toggle(False).status_code, 303)
        older = self.form(jurisdiction='england')
        self.assertEqual(older['version'], '0')
        self.assertEqual(self.save(jurisdiction='wales').status_code, 303)
        self.assertEqual(self.client.post('/dashboard/tax-profile', data=older).status_code, 409)
        self.assertEqual(self.fixture.live_value('SELECT jurisdiction FROM SCHEMA.tax_profiles'), 'wales')

    def test_unknown_can_be_saved_without_attesting_and_database_failure_is_safe(self):
        from unittest.mock import patch
        from sqlalchemy.exc import OperationalError
        fields = self.form(jurisdiction='unknown')
        fields.pop('confirmed')
        self.assertEqual(self.client.post('/dashboard/tax-profile', data=fields).status_code, 303)
        self.assertIsNone(self.plan()['tax_profile']['confirmed_at'])
        with patch('routes.tax_profile.current_profile', side_effect=OperationalError('private SQL', {}, Exception('private details'))):
            response = self.client.get('/dashboard/tax-profile')
        self.assertEqual(response.status_code, 503)
        html = response.get_data(as_text=True)
        self.assertIn('Tax profile is unavailable', html)
        self.assertNotIn('private SQL', html)
        self.assertNotIn('private details', html)

    def test_validation_confirmation_csrf_and_owner_boundaries(self):
        for change in ({'jurisdiction': 'invalid'}, {'confirmed': ''}, {'tax_year': '2027-28'}):
            self.assertEqual(self.save(**change).status_code, 400)
        fields = self.form(jurisdiction='wales')
        fields.pop('confirmed')
        response = self.client.post('/dashboard/tax-profile', data=fields)
        self.assertEqual(response.status_code, 400)
        self.assertIn('value="wales" selected', response.get_data(as_text=True))
        duplicate = MultiDict(self.form())
        duplicate.add('jurisdiction', 'scotland')
        self.assertEqual(self.client.post('/dashboard/tax-profile', data=duplicate).status_code, 400)
        fields = self.form()
        fields.pop('csrf_token')
        self.assertEqual(self.client.post('/dashboard/tax-profile', data=fields).status_code, 400)
        self.assertEqual(self.client.get('/dashboard/tax-profile', headers={'Authorization': 'Bearer test-key'}).status_code, 302)
        self.assertEqual(self.client.get('/dashboard/tax-profile?jurisdiction=england').status_code, 400)
        self.assertEqual(self.plan()['tax_profile']['jurisdiction'], 'england')
        response = self.client.get('/dashboard/tax-profile')
        self.assertIn('no-store', response.headers['Cache-Control'])
        with self.client.session_transaction() as cookie:
            cookie.clear()
        self.assertEqual(self.client.get('/dashboard/tax-profile').status_code, 302)

    def test_migration_matches_model_and_leaves_profiles_unconfirmed(self):
        from pathlib import Path
        schema = self.fixture.real_schema
        engine = self.fixture.admin.execution_options(schema_translate_map={'toms': schema})
        with engine.begin() as connection:
            TaxProfile.__table__.drop(connection)
            sql = (Path(__file__).resolve().parents[1] / 'migrations/026_tax_profile.sql').read_text()
            connection.exec_driver_sql(sql.replace('toms.', schema + '.'))
            self.assertEqual(connection.scalar(text(f'SELECT count(*) FROM {schema}.tax_profiles')), 0)
        with Session(engine) as saved, saved.begin():
            saved.add(TaxProfile(tax_year='2026-27'))
            saved.flush()
            row = saved.get(TaxProfile, '2026-27')
            self.assertEqual(row.jurisdiction, 'unknown')
            self.assertEqual(row.revision, 1)
            self.assertIsNone(row.confirmed_at)
