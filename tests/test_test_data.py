"""Real request/session routing against two disposable PostgreSQL schemas.

Live and demo intentionally share UUIDs: isolation must depend on the schema,
not on hoping that synthetic identifiers never collide with real records.
"""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from flask import g
from sqlalchemy import text
from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash

from app import app
from models import Account, BrowserSession, Category, IncomeStream, MileageEntry, OwnerLogin, Transaction
from services.banking.client import StarlingError, starling_request
from services.database.connection import db
from services.database.migration_connection import migration_engine
from services.database.session import DATA_TABLES
from services.transactions.automatic_sync import start_dashboard_sync
from services.web.test_data import sample_id, ensure_sample_data
from services.web.sessions import token_hash


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS') == '1', 'Requires PostgreSQL.')
class TestDataTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(app.app_context())
        self.enterContext(patch.dict(app.config, SECRET_KEY='a' * 64, SESSION_COOKIE_SECURE=False, APP_API_KEY='test-key'))
        self.runtime = db.engine
        self.admin = migration_engine()
        self.addCleanup(self.admin.dispose)
        self.real_schema, self.demo_schema = ('test_real_' + uuid4().hex, 'test_demo_' + uuid4().hex)
        with self.admin.begin() as connection:
            for schema in (self.real_schema, self.demo_schema):
                connection.execute(text(f'CREATE SCHEMA {schema}'))
        self.addCleanup(self.drop_schemas)
        db.metadata.create_all(self.admin.execution_options(schema_translate_map={'toms': self.real_schema}))
        db.metadata.create_all(self.admin.execution_options(schema_translate_map={'toms': self.demo_schema}),
                               tables=[table for table in db.metadata.sorted_tables if table.name in DATA_TABLES])
        with self.admin.begin() as connection:
            for schema in (self.real_schema, self.demo_schema):
                connection.execute(text(f'GRANT USAGE ON SCHEMA {schema} TO toms_app'))
                connection.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO toms_app'))
        self.enterContext(patch.dict(db.engines, {None: self.runtime.execution_options(schema_translate_map={'toms': self.real_schema})}))
        self.enterContext(patch.dict(app.config, TEST_DATA_SCHEMA=self.demo_schema))
        self.addCleanup(db.session.remove)
        def clear_caches():
            for key in ('_login_user', 'csrf_token', 'csrf_valid', 'use_test_data', '_test_data_engines'):
                g.pop(key, None)
        app.before_request_funcs.setdefault(None, []).insert(0, clear_caches)
        self.addCleanup(app.before_request_funcs[None].remove, clear_caches)
        def close_request_session(response):
            db.session.remove()
            return response
        app.after_request_funcs.setdefault(None, []).append(close_request_session)
        self.addCleanup(app.after_request_funcs[None].remove, close_request_session)
        self.bank = self.enterContext(patch('services.banking.client.http_client.send', side_effect=AssertionError('Test mode must never contact the bank')))
        self.start_sync = self.enterContext(patch('routes.dashboard.start_dashboard_sync'))
        self.enterContext(patch('services.tax.rules.start_rule_check'))
        now = datetime.now(timezone.utc)
        self.token = 'f' * 64
        db.session.add(OwnerLogin(id=1, username='demo-test-owner', password_hash=generate_password_hash('test-pass-123')))
        db.session.add(BrowserSession(token_hash=token_hash(self.token), created_at=now, last_seen_at=now, expires_at=now + timedelta(hours=8)))
        db.session.add(Account(account_uid=sample_id(1), default_category_uid=sample_id(11), name='REAL-ISOLATION-SENTINEL', currency='GBP', raw_payload={}))
        db.session.commit()
        db.session.add(Category(account_uid=sample_id(1), category_uid=sample_id(11), kind='main'))
        db.session.add(IncomeStream(id=sample_id(21), name='REAL-INCOME-SENTINEL', kind='employed'))
        db.session.commit()
        db.session.add(Transaction(account_uid=sample_id(1), category_uid=sample_id(11), feed_item_uid=sample_id(31), amount_minor=999,
                                   currency='GBP', direction='IN', status='SETTLED', transaction_time=now, source_updated_at=now,
                                   source='FASTER_PAYMENTS_IN', counterparty_name='REAL-ISOLATION-SENTINEL', raw_payload={}))
        db.session.commit()
        db.session.remove()
        self.client = app.test_client()
        with self.client.session_transaction() as cookie:
            cookie['_user_id'] = self.token

    def drop_schemas(self):
        with self.admin.begin() as connection:
            for schema in (self.real_schema, self.demo_schema):
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))

    def csrf(self, client=None, path='/dashboard'):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))[1]

    def toggle(self, enabled, client=None):
        browser = client or self.client
        return browser.post('/dashboard/test-data', data={'enabled': '1' if enabled else '0', 'csrf_token': self.csrf(browser)})

    def live_value(self, sql):
        with self.admin.connect() as connection:
            return connection.scalar(text(sql.replace('SCHEMA', self.real_schema)))

    def test_toggle_seeds_samples_and_restores_real_data(self):
        self.assertEqual(self.toggle(True).status_code, 303)
        response = self.client.get('/dashboard')
        html = response.get_data(as_text=True)
        self.assertIn('Test mode · sample records', html)
        self.assertIn('Demo employer', html)
        self.assertNotIn('REAL-ISOLATION-SENTINEL', html)
        self.assertIn('role="switch" aria-checked="true"', html)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(self.toggle(False).status_code, 303)
        html = self.client.get('/dashboard').get_data(as_text=True)
        self.assertIn('REAL-ISOLATION-SENTINEL', html)
        self.assertNotIn('Test mode · sample records', html)
        self.assertNotIn('Demo employer', html)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.transactions'), 1)

    def test_balances_and_sync_never_reach_provider_or_importer(self):
        self.toggle(True)
        balances = self.client.get('/dashboard/balances').get_json()
        self.assertEqual(balances['totals'][0]['amount'], 'GBP 7,550.75')
        self.assertEqual(self.client.get('/dashboard/balances?account=' + sample_id(2)).get_json()['balances'][0]['amount'], 'GBP 5,100.00')
        self.assertEqual(self.client.get('/dashboard/balances?account=' + str(uuid4())).status_code, 400)
        self.assertEqual(self.client.head('/dashboard/balances').status_code, 200)
        token = self.csrf()
        response = self.client.post('/dashboard/sync?force=1', headers={'X-CSRFToken': token})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()['test_data'])
        self.assertEqual(self.client.get('/dashboard/sync-status').get_json()['run']['status'], 'completed')
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.sync_runs'), 0)
        self.bank.assert_not_called()
        self.start_sync.assert_not_called()

    def test_classification_and_confirmation_write_only_demo(self):
        self.toggle(True)
        path = f'/dashboard/transactions/{sample_id(1)}/{sample_id(11)}/{sample_id(31)}/classification'
        token = self.csrf(path=path)
        response = self.client.post(path, data={'csrf_token': token, 'action': 'save', 'type': 'income', 'notes': 'Sample edited'})
        self.assertEqual(response.status_code, 303)
        item = self.client.get('/dashboard/review').get_json()['transaction']
        self.assertEqual(self.client.post(item['confirm_url'], json={'version': item['version']}, headers={'X-CSRFToken': self.csrf()}).status_code, 200)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.transaction_classifications'), 0)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.transactions WHERE confirmed_at IS NOT NULL'), 0)
        self.toggle(False)
        self.toggle(True)
        self.assertIn('Sample edited', self.client.get(path).get_data(as_text=True))

    def test_income_forms_streams_and_tax_are_real_workflows_on_sample_rows(self):
        self.toggle(True)
        response = self.client.get('/dashboard/tax-estimate')
        self.assertEqual(response.status_code, 200)
        self.assertIn('GBP 70,000.00', response.get_data(as_text=True))
        token = self.csrf(path='/dashboard/income-streams')
        values = dict(action='create', name='Demo extra work', kind='self_employed', expected_gross='1000',
                      expected_gross_period='monthly', expected_gross_currency='GBP', forecast_tax_year='2026-27',
                      csrf_token=token)
        self.assertEqual(self.client.post('/dashboard/income-streams', data=values).status_code, 303)
        self.assertIn('GBP 82,000.00', self.client.get('/dashboard/tax-estimate').get_data(as_text=True))
        path = f'/dashboard/transactions/{sample_id(1)}/{sample_id(11)}/{sample_id(32)}/income'
        response = self.client.get(path)
        html = response.get_data(as_text=True)
        values = dict(action='save', income_stream_id=sample_id(22), tax_treatment='no_tax_deducted', gross='850.00',
                      tax_deducted='0', source_name='Demo edited client',
                      csrf_token=re.search(r'name="csrf_token" value="([^"]+)"', html)[1],
                      version=re.search(r'name="version" value="([^"]+)"', html)[1])
        self.assertEqual(self.client.post(path, data=values).status_code, 303)
        self.assertIn('Demo edited client', self.client.get(path).get_data(as_text=True))
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.income_streams'), 1)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.transaction_income'), 0)

    def test_authentication_stays_live_and_logout_clears_selection(self):
        self.toggle(True)
        self.assertEqual(self.client.get('/settings/password').status_code, 200)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.browser_sessions'), 1)
        self.assertEqual(self.client.post('/logout', data={'csrf_token': self.csrf()}).status_code, 302)
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        with self.client.session_transaction() as cookie:
            self.assertNotIn('use_test_data', cookie)

    def test_mode_is_per_browser_and_does_not_affect_api_or_background_context(self):
        other = app.test_client()
        with other.session_transaction() as cookie:
            cookie['_user_id'] = self.token
        self.toggle(True)
        self.assertIn('REAL-ISOLATION-SENTINEL', other.get('/dashboard').get_data(as_text=True))
        api_html = self.client.get('/dashboard', headers={'Authorization': 'Bearer test-key'}).get_data(as_text=True)
        self.assertIn('REAL-ISOLATION-SENTINEL', api_html)
        self.assertNotIn('Test mode · sample records', api_html)
        db.session.remove()
        self.assertEqual(db.session.get(Account, sample_id(1)).name, 'REAL-ISOLATION-SENTINEL')

    def test_stale_form_after_switch_cannot_write_identical_uuid_in_other_dataset(self):
        path = f'/dashboard/transactions/{sample_id(1)}/{sample_id(11)}/{sample_id(31)}/classification'
        old_token = self.csrf(path=path)
        self.toggle(True)
        self.assertEqual(self.client.post(path, data={'csrf_token': old_token, 'action': 'save', 'type': 'income'}).status_code, 400)
        demo_token = self.csrf(path=path)
        self.toggle(False)
        self.assertEqual(self.client.post(path, data={'csrf_token': demo_token, 'action': 'save', 'type': 'income'}).status_code, 400)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.transaction_classifications'), 0)

    def test_toggle_requires_login_csrf_and_strict_values(self):
        self.assertEqual(self.client.get('/dashboard/test-data').status_code, 405)
        self.assertEqual(self.client.post('/dashboard/test-data', data={'enabled': '1'}).status_code, 400)
        for data in (MultiDict([('enabled', '1'), ('enabled', '0')]), {'enabled': 'yes'}, {'enabled': '1', 'next': 'https://example.com'}):
            data['csrf_token'] = self.csrf()
            self.assertEqual(self.client.post('/dashboard/test-data', data=data).status_code, 400)
        self.assertEqual(self.client.post('/dashboard/test-data', headers={'Authorization': 'Bearer test-key'}, data={'enabled': '1'}).status_code, 302)
        with self.client.session_transaction() as cookie:
            cookie.clear()
        self.assertEqual(self.client.post('/dashboard/test-data', data={'enabled': '1'}).status_code, 302)

    def test_bank_and_background_guards_fail_closed_inside_test_mode(self):
        with app.test_request_context('/dashboard'):
            g.use_test_data = True
            with self.assertRaises(StarlingError) as error:
                starling_request('/api/v2/accounts', scope='account:read')
            self.assertEqual(error.exception.status_code, 403)
            with self.assertRaises(RuntimeError):
                start_dashboard_sync(app)
            with self.assertRaises(RuntimeError):
                db.session.execute(text('SELECT * FROM toms.accounts'))
            with self.assertRaises(RuntimeError):
                db.session.execute(db.select(text('amount_minor FROM toms.transactions')))
            self.bank.assert_not_called()

    def test_simultaneous_first_switches_seed_once(self):
        browsers = [app.test_client(), app.test_client()]
        forms = []
        for browser in browsers:
            with browser.session_transaction() as cookie:
                cookie['_user_id'] = self.token
            forms.append(dict(enabled='1', csrf_token=self.csrf(browser)))
        barrier = Barrier(2)
        def seed_together():
            barrier.wait(timeout=10)
            ensure_sample_data()
        def submit(index):
            return browsers[index].post('/dashboard/test-data', data=forms[index]).status_code
        with patch('routes.dashboard.ensure_sample_data', side_effect=seed_together), ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(submit, range(2))), [303, 303])
        with self.admin.connect() as connection:
            self.assertEqual(connection.scalar(text(f'SELECT count(*) FROM {self.demo_schema}.transactions')), 7)

    def test_session_refuses_mixed_authentication_and_business_queries(self):
        with app.test_request_context('/dashboard'):
            g.use_test_data = True
            with self.assertRaises(RuntimeError):
                db.session.execute(db.select(Account, OwnerLogin))
            first = db.session().get_bind(clause=db.select(Account))
            self.assertIs(first, db.session().get_bind(clause=db.select(Account)))

    def test_seed_is_idempotent_and_has_no_demo_login_tables(self):
        self.toggle(True)
        self.toggle(True)
        with self.admin.connect() as connection:
            self.assertEqual(connection.scalar(text(f'SELECT count(*) FROM {self.demo_schema}.transactions')), 7)
            self.assertEqual(connection.scalar(text(f'SELECT count(*) FROM {self.demo_schema}.income_streams')), 3)
            self.assertIsNone(connection.scalar(text('SELECT to_regclass(:table)'), {'table': self.demo_schema + '.owner_login'}))


    def test_sample_payroll_reconciles_and_overtime_is_counted_once(self):
        self.toggle(True)
        with self.admin.connect() as connection:
            rows = connection.execute(text(f"""SELECT t.amount_minor, i.gross_minor,
                i.tax_deducted_minor, i.ni_deducted_minor, i.adjustment_minor FROM {self.demo_schema}.transaction_income i
                JOIN {self.demo_schema}.transactions t USING (account_uid, category_uid, feed_item_uid)""")).all()
            self.assertEqual(len(rows), 3)
            for net, gross, tax, ni, adjustment in rows:
                self.assertEqual(net, gross - tax - (ni or 0) + adjustment)
            self.assertEqual(connection.scalar(text(f'SELECT count(*) FROM {self.demo_schema}.income_shifts WHERE is_overtime')), 1)
        self.assertTrue('GBP 70,000.00' in self.client.get('/dashboard/tax-estimate').get_data(as_text=True))
        path = f'/dashboard/income-streams/{sample_id(21)}/shifts'
        self.assertTrue('Sample overtime' in self.client.get(path).get_data(as_text=True))
        self.toggle(False)
        self.toggle(True)
        with self.admin.connect() as connection:
            self.assertEqual(connection.scalar(text(f'SELECT count(*) FROM {self.demo_schema}.income_shifts')), 1)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.income_shifts'), 0)
        self.bank.assert_not_called()

    def test_demo_mileage_relationship_preserves_real_pool_and_rejects_stale_form(self):
        original = self.live_value('SELECT mileage_pool_id FROM SCHEMA.income_streams LIMIT 1')
        self.toggle(True)
        fields = dict(action='update', stream_id=sample_id(22), name='Demo freelance work', kind='self_employed',
            income_mode='forecast', expected_gross='20000', expected_gross_period='yearly',
            expected_gross_currency='GBP', forecast_tax_year='2026-27', mileage_with_stream=sample_id(23),
            csrf_token=self.csrf(path='/dashboard/income-streams'))
        self.assertEqual(self.client.post('/dashboard/income-streams', data=fields).status_code, 303)
        with self.admin.connect() as connection:
            pools = connection.execute(text(f'SELECT mileage_pool_id FROM {self.demo_schema}.income_streams WHERE kind != :kind'), {'kind': 'employed'}).scalars().all()
            self.assertEqual(len(set(pools)), 1)
        self.toggle(False)
        self.assertEqual(self.client.post('/dashboard/income-streams', data=fields).status_code, 400)
        self.assertEqual(self.live_value('SELECT mileage_pool_id FROM SCHEMA.income_streams LIMIT 1'), original)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.mileage_entries'), 0)
        self.bank.assert_not_called()

    def test_sample_overtime_write_isolated_and_old_form_rejected_after_switch(self):
        self.toggle(True)
        path = f'/dashboard/income-streams/{sample_id(21)}/shifts'
        fields = dict(action='create', shift_id=str(uuid4()), income_mode='forecast',
            starts_at='2026-04-11T09:00', ends_at='2026-04-11T11:00', unpaid_break_minutes='0',
            payment_mode='total', total_payment='100', hourly_rate='', notes='Extra sample overtime',
            csrf_token=self.csrf(path=path))
        self.assertEqual(self.client.post(path, data=fields).status_code, 303)
        self.assertTrue('GBP 70,100.00' in self.client.get('/dashboard/tax-estimate').get_data(as_text=True))
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.income_shifts'), 0)
        self.toggle(False)
        self.assertEqual(self.client.post(path, data=fields).status_code, 400)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.income_shifts'), 0)
        self.bank.assert_not_called()


    def test_demo_mileage_shares_band_across_streams_without_real_writes(self):
        from services.tax.mileage import mileage_allowances
        self.toggle(True)
        values = dict(action='update', stream_id=sample_id(23), name='Demo CIS work', kind='cis',
            income_mode='forecast', expected_gross='10000', expected_gross_period='yearly',
            expected_gross_currency='GBP', forecast_tax_year='2026-27', mileage_with_stream=sample_id(22),
            csrf_token=self.csrf(path='/dashboard/income-streams'))
        self.assertEqual(self.client.post('/dashboard/income-streams', data=values).status_code, 303)
        for stream, miles, day in ((22, '8000', '2026-04-10'), (23, '4000', '2026-04-11')):
            values = dict(action='save_mileage', entry_id=str(uuid4()), stream_id=sample_id(stream),
                journey_date=day, location='england', vehicle_type='car_van', vehicle_key='DEMO CAR',
                miles=miles, purpose='Synthetic accumulated mileage for testing the annual band',
                start_postcode='SW1A 1AA', end_postcode='SW1A 2AA', reimbursed='0', eligible='1',
                csrf_token=self.csrf(path='/dashboard/deductions'))
            self.assertEqual(self.client.post('/dashboard/deductions', data=values).status_code, 303)
        with app.test_request_context('/dashboard'):
            g.use_test_data = True
            entries = db.session.scalars(db.select(MileageEntry)).all()
            self.assertEqual(mileage_allowances(entries), {sample_id(22): 440000, sample_id(23): 160000})
        db.session.remove()
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.mileage_entries'), 0)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.expense_deductions'), 0)
        self.bank.assert_not_called()

    def test_tax_readiness_and_logged_payroll_ni_are_visible_in_demo_only(self):
        self.toggle(True)
        response = self.client.get('/dashboard/tax-estimate')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for expected in ('Needs attention', 'Transactions awaiting your confirmation',
                         'Received-income target needs review', 'GBP 76.16', 'Class 4'):
            self.assertTrue(expected in html, expected)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.transaction_income'), 0)
        self.bank.assert_not_called()

    def test_actual_payroll_ni_edit_reconciles_and_cannot_cross_datasets(self):
        self.toggle(True)
        path = f'/dashboard/transactions/{sample_id(1)}/{sample_id(11)}/{sample_id(31)}/income'
        html = self.client.get(path).get_data(as_text=True)
        values = dict(action='save', income_stream_id=sample_id(21), tax_treatment='paye', gross='3333.33',
            tax_deducted='457.17', ni_deducted='76.16', adjustment='0', adjustment_notes='',
            csrf_token=re.search(r'name="csrf_token" value="([^"]+)"', html)[1],
            version=re.search(r'name="version" value="([^"]+)"', html)[1])
        self.assertEqual(self.client.post(path, data=values).status_code, 303)
        with self.admin.connect() as connection:
            self.assertEqual(connection.scalar(text(f'SELECT ni_deducted_minor FROM {self.demo_schema}.transaction_income WHERE income_type = :kind'), {'kind': 'employment'}), 7616)
        self.toggle(False)
        self.assertEqual(self.client.post(path, data=values).status_code, 400)
        self.assertEqual(self.live_value('SELECT count(*) FROM SCHEMA.transaction_income'), 0)
        self.bank.assert_not_called()

    def test_activity_stays_in_isolated_real_log_and_marks_sample_changes(self):
        from models import UserAction
        from sqlalchemy import inspect
        from services.web.activity import record_action
        self.assertEqual(self.toggle(True).status_code, 303)
        with app.test_request_context('/dashboard/income-streams', method='POST'):
            g.use_test_data = True
            record_action('shift.create', sample_id(41), verified_owner=True)
            db.session.commit()
        events = db.session.scalars(db.select(UserAction)).all()
        self.assertTrue(any(row.action == 'test-data.on' and row.sample_data for row in events))
        self.assertTrue(any(row.action == 'shift.create' and row.sample_data for row in events))
        with self.runtime.connect() as connection:
            self.assertFalse(inspect(connection).has_table('user_actions', schema=self.demo_schema))
        before = len(events)
        with app.test_request_context('/dashboard/income-streams', method='POST'):
            g.use_test_data = True
            record_action('shift.delete', sample_id(41), verified_owner=True)
            db.session.rollback()
        self.assertEqual(len(db.session.scalars(db.select(UserAction)).all()), before)
