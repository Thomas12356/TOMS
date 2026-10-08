"""Owner-only system information, safe filesystem metadata and durable activity."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.exc import DBAPIError, OperationalError

from app import app
from models import UserAction
from services.database.connection import db
from services.system.overview import backup_inventory, format_bytes, format_timestamp
from services.web.activity import record_action
from support import ApiTestCase
from test_login import OwnerLoginFixture


class InventoryTests(unittest.TestCase):
    def test_empty_missing_invalid_and_symlink_bundles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(backup_inventory(root / 'absent')['count'], 0)
            bundle = root / 'toms-20261008T020000Z-11111111'
            bundle.mkdir()
            (bundle / 'manifest.json').write_text('[]')
            self.assertEqual(backup_inventory(root)['rows'][0]['state'], 'Incomplete')
            (root / 'toms-link').symlink_to(bundle, target_is_directory=True)
            self.assertEqual(backup_inventory(root)['count'], 1)

    def test_verified_markers_match_archive_and_dates_are_escaped_by_template(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / 'toms-20261008T020000Z-11111111'
            bundle.mkdir()
            (bundle / 'database.dump').write_bytes(b'archive')
            (bundle / 'manifest.json').write_text(json.dumps({'format': 1, 'tables': {'toms.accounts': {}},
                'created_utc': '2026-10-08T02:00:00+00:00', 'archive_sha256': 'expected'}))
            marker = bundle / 'verified-one.json'
            marker.write_text(json.dumps({'verified_utc': '2026-10-08T03:00:00+00:00', 'archive_sha256': 'wrong'}))
            self.assertEqual(backup_inventory(directory)['rows'][0]['state'], 'Not restore-tested')
            marker.write_text(json.dumps({'verified_utc': '2026-10-08T03:00:00+00:00', 'archive_sha256': 'expected'}))
            result = backup_inventory(directory)
            self.assertEqual(result['rows'][0]['state'], 'Restore tested')
            self.assertGreater(result['total_bytes'], 7)
            (bundle / 'database.dump').unlink()
            self.assertEqual(backup_inventory(directory)['rows'][0]['state'], 'Incomplete')

    def test_activity_display_converts_database_timezone_to_utc(self):
        local = datetime(2026, 10, 8, 13, 30, tzinfo=timezone(timedelta(hours=1)))
        self.assertEqual(format_timestamp(local, seconds=True), '08 Oct 2026, 12:30:00 UTC')

    def test_sizes_use_binary_units(self):
        self.assertEqual(format_bytes(0), '0 B')
        self.assertEqual(format_bytes(1024), '1.0 KiB')
        self.assertEqual(format_bytes(1024**3), '1.0 GiB')


class SystemAuthTests(ApiTestCase):
    def test_anonymous_and_api_credentials_cannot_read_system(self):
        with patch.dict(app.config, SECRET_KEY='a' * 64), patch('routes.system.backup_inventory') as inventory, patch('routes.system.database_storage') as storage:
            for headers in ({}, self.headers, {'Authorization': 'Basic dXNlcjpwYXNz'}):
                response = self.client.get('/dashboard/system', headers=headers)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/login', response.location)
            inventory.assert_not_called()
            storage.assert_not_called()

    def test_system_has_no_mutating_http_routes(self):
        self.assertEqual(self.client.post('/dashboard/system', headers=self.headers).status_code, 405)


class SystemOwnerTests(OwnerLoginFixture):
    def setUp(self):
        super().setUp()
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(app, 'instance_path', self.directory))
        self.enterContext(patch('routes.system.database_storage', return_value=dict(total=1048576, real=524288, sample=262144)))
        self.bank = self.enterContext(patch('services.banking.client.http_client.send', side_effect=AssertionError('No bank calls')))
        self.assertEqual(self.sign_in().status_code, 302)

    def test_page_shows_storage_navigation_and_successful_login_without_secrets(self):
        response = self.client.get('/dashboard/system')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        for text in ('System management', '1.0 MiB', '512.0 KiB', 'No backups yet', 'Signed in', 'aria-current="page">System'):
            self.assertIn(text, html)
        self.assertNotIn(self.password, html)
        self.assertNotIn('password_hash', html)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.bank.assert_not_called()

    def test_filters_pagination_and_invalid_queries(self):
        for index in range(35):
            self.session.add(UserAction(id=str(uuid4()), occurred_at=datetime.now(timezone.utc), action='confirm', sample_data=index % 2 == 0, record_id=''))
        self.session.commit()
        page = self.client.get('/dashboard/system')
        self.assertIn('Older', page.get_data(as_text=True))
        filtered = self.client.get('/dashboard/system?mode=test')
        self.assertEqual(filtered.status_code, 200)
        self.assertNotIn('Signed in', filtered.get_data(as_text=True))
        for query in ('page=0', 'page=100001', 'page=x', 'mode=wrong', 'page=1&page=2', 'path=/etc/passwd'):
            self.assertEqual(self.client.get('/dashboard/system?' + query).status_code, 400)

    def test_successful_stream_change_is_logged_but_validation_failure_is_not(self):
        import re
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
        form = dict(action='create', name='Private stream name', kind='self_employed',
                    expected_gross='30000', expected_gross_period='yearly',
                    expected_gross_currency='GBP', forecast_tax_year='2026-27', csrf_token=csrf)
        self.assertEqual(self.client.post('/dashboard/income-streams', data=form).status_code, 303)
        events = self.session.scalars(db.select(UserAction).where(UserAction.action == 'stream.create')).all()
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].record_id)
        self.assertNotIn('Private stream name', str(events[0].as_dict()))
        form['name'] = ''
        self.assertEqual(self.client.post('/dashboard/income-streams', data=form).status_code, 400)
        self.assertEqual(self.session.scalar(db.select(db.func.count()).select_from(UserAction).where(UserAction.action == 'stream.create')), 1)

    def test_activity_is_transactional_and_has_no_body_contents(self):
        with app.test_request_context('/dashboard/transactions', method='POST', data={'password': 'DO-NOT-LOG', 'notes': 'PRIVATE-NOTES'}):
            record_action('confirm', 'record-uuid', verified_owner=True)
            self.session.rollback()
        self.assertEqual(self.session.scalar(db.select(db.func.count()).select_from(UserAction).where(UserAction.record_id == 'record-uuid')), 0)
        with app.test_request_context('/dashboard/transactions', method='POST', data={'password': 'DO-NOT-LOG'}):
            record_action('confirm', 'saved-uuid', verified_owner=True)
            self.session.commit()
        action = self.session.scalar(db.select(UserAction).where(UserAction.record_id == 'saved-uuid'))
        self.assertEqual(action.action, 'confirm')
        self.assertNotIn('DO-NOT-LOG', str(action.as_dict()))

    def test_database_failures_have_safe_messages(self):
        with patch('routes.system.database_storage', side_effect=OperationalError('private sql', {}, Exception('secret password'))), self.assertLogs('toms.errors'):
            response = self.client.get('/dashboard/system')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret password', response.get_data(as_text=True))

    def test_runtime_cannot_edit_or_delete_activity_history(self):
        for statement in ('UPDATE toms.user_actions SET action=action', 'DELETE FROM toms.user_actions', 'TRUNCATE toms.user_actions'):
            with self.subTest(statement=statement):
                savepoint = self.session.begin_nested()
                try:
                    with self.assertRaises(DBAPIError) as caught:
                        self.session.execute(db.text(statement))
                    self.assertEqual(caught.exception.orig.sqlstate, '42501')
                finally:
                    savepoint.rollback()
