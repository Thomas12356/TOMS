"""Backup safety checks, plus optional real PostgreSQL archive/restore tests."""
import json
import io
from contextlib import redirect_stderr
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from psycopg import sql

from services.database import backups


class BackupSafetyTests(unittest.TestCase):
    def test_missing_maintenance_credentials_do_not_use_runtime_password(self):
        with patch.object(backups, 'dotenv_values', side_effect=[{'PGUSER': 'app', 'PGPASSWORD': 'secret'}, {}]):
            with self.assertRaises(RuntimeError):
                backups.settings()

    def test_checksum_detects_corruption_before_connecting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'database.dump').write_bytes(b'corrupt')
            (path / 'manifest.json').write_text(json.dumps({'format': 1, 'tables': {'x': {}}, 'archive_sha256': 'wrong'}))
            with patch.object(backups, 'connect') as connect:
                with self.assertRaisesRegex(RuntimeError, 'checksum'):
                    backups.restore(path, {'PGDATABASE': 'recovery'}, 'working')
                connect.assert_not_called()

    def test_source_and_system_databases_refused_even_on_another_host(self):
        for name in ('working', 'original', 'postgres', 'template0', 'template1'):
            with self.subTest(name=name), patch.object(backups, 'load_bundle', return_value=(Path('/unused'), {'source_database': 'original'})), patch.object(backups, 'connect') as connect:
                with self.assertRaisesRegex(RuntimeError, 'Refusing'):
                    backups.restore('/unused', {'PGDATABASE': name}, 'working')
                connect.assert_not_called()

    def test_failed_dump_does_not_publish_or_leave_partial_files(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(backups, 'connect') as connect, patch.object(backups, 'fingerprint', return_value={}), patch.object(backups, 'run_tool', side_effect=RuntimeError('failed')):
            connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = ['snapshot']
            with self.assertRaises(RuntimeError):
                backups.backup(directory, {'PGDATABASE': 'source'})
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_directory_with_public_permissions_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o755)
            with self.assertRaisesRegex(RuntimeError, 'private'):
                backups.backup(directory, {})

    def test_subprocess_errors_never_expose_stderr(self):
        with patch.object(backups.shutil, 'which', return_value='/bin/pg_dump'), patch.object(backups.subprocess, 'run') as run:
            run.return_value.returncode = 1
            run.return_value.stderr = b'password and financial contents'
            with self.assertRaises(RuntimeError) as caught:
                backups.run_tool('pg_dump', [], {'PGPASSWORD': 'hidden'})
            self.assertNotIn('financial', str(caught.exception))
            self.assertNotIn('hidden', str(caught.exception))
            self.assertNotIn('shell', run.call_args.kwargs)


@unittest.skipUnless(os.getenv('RUN_BACKUP_POSTGRES_TESTS') == '1', 'Requires disposable CREATEDB test server.')
class BackupPostgresTests(unittest.TestCase):
    def setUp(self):
        self.admin = {key: os.environ[key] for key in ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD')}
        self.name = 'toms_backup_source_' + uuid.uuid4().hex
        with backups.connect(self.admin, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(self.name)))
        self.source = dict(self.admin, PGDATABASE=self.name)
        with backups.connect(self.source) as connection:
            connection.execute('CREATE SCHEMA toms; CREATE SCHEMA toms_demo')
            connection.execute('CREATE TABLE toms.schema_migrations (version text PRIMARY KEY)')
            connection.execute("INSERT INTO toms.schema_migrations VALUES ('001')")
            connection.execute('CREATE TABLE toms.accounts (id integer PRIMARY KEY, balance bigint NOT NULL)')
            connection.execute('CREATE TABLE toms.transactions (id integer PRIMARY KEY, account_id integer REFERENCES toms.accounts(id), amount bigint, recorded timestamptz)')
            connection.execute("INSERT INTO toms.accounts VALUES (1, 250075); INSERT INTO toms.transactions VALUES (1,1,-1234,'2026-10-08 12:00+01')")
            connection.execute('CREATE TABLE toms_demo.accounts (id integer PRIMARY KEY); INSERT INTO toms_demo.accounts VALUES (2)')
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()
        with backups.connect(self.admin, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(self.name)))

    def test_real_backup_round_trip_and_snapshot_after_source_changes(self):
        bundle = backups.backup(self.directory.name, self.source)
        with backups.connect(self.source) as connection:
            connection.execute('UPDATE toms.accounts SET balance=1')
        marker = backups.verify(bundle, self.source, self.admin)
        self.assertTrue(marker.exists())
        self.assertEqual((bundle / 'database.dump').stat().st_mode & 0o777, 0o600)
        self.assertEqual(bundle.stat().st_mode & 0o777, 0o700)
        with backups.connect(self.admin) as connection:
            self.assertFalse(connection.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname LIKE 'toms_restore_test_%')").fetchone()[0])

    def test_snapshot_stays_consistent_when_source_changes_during_dump(self):
        original = backups.run_tool
        def concurrent_write(name, arguments, config):
            with backups.connect(self.source) as connection:
                connection.execute('UPDATE toms.accounts SET balance=99')
            return original(name, arguments, config)
        with patch.object(backups, 'run_tool', side_effect=concurrent_write):
            bundle = backups.backup(self.directory.name, self.source)
        backups.verify(bundle, self.source, self.admin)

    def test_real_nonempty_restore_target_is_untouched(self):
        bundle = backups.backup(self.directory.name, self.source)
        other = dict(self.source, PGDATABASE=self.name + '_occupied')
        with backups.connect(self.admin, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(other['PGDATABASE'])))
        try:
            with backups.connect(other) as connection:
                connection.execute('CREATE TABLE public.keep_me (id integer); INSERT INTO public.keep_me VALUES (42)')
            with self.assertRaisesRegex(RuntimeError, 'empty'):
                backups.restore(bundle, other, self.source['PGDATABASE'])
            with backups.connect(other) as connection:
                self.assertEqual(connection.execute('SELECT id FROM public.keep_me').fetchone()[0], 42)
        finally:
            with backups.connect(self.admin, autocommit=True) as connection:
                connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(other['PGDATABASE'])))

    def test_modified_manifest_fails_restore_and_cleans_up(self):
        bundle = backups.backup(self.directory.name, self.source)
        manifest_path = bundle / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['tables']['toms.accounts']['rows'] = 999
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(RuntimeError, 'differ'):
            backups.verify(bundle, self.source, self.admin)
        self.assertEqual(list(bundle.glob('verified-*')), [])
        with backups.connect(self.admin) as connection:
            self.assertFalse(connection.execute("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname LIKE 'toms_restore_test_%')").fetchone()[0])


class BackupRetentionTests(unittest.TestCase):
    def bundle(self, directory, name, verified=True):
        path = Path(directory) / name
        path.mkdir()
        (path / 'database.dump').write_bytes(b'archive')
        digest = backups.file_hash(path / 'database.dump')
        (path / 'manifest.json').write_text(json.dumps({'format': 1, 'tables': {'toms.accounts': {}},
            'created_utc': '2000-01-01T00:00:00+00:00', 'archive_sha256': digest}))
        if verified:
            (path / 'verified-test.json').write_text(json.dumps({'archive_sha256': digest, 'verified_utc': '2000-01-02T00:00:00+00:00'}))
        return path

    def test_prune_only_intact_verified_bundles_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            old = self.bundle(directory, 'toms-20000101T000000Z-11111111')
            unverified = self.bundle(directory, 'toms-20000101T000000Z-22222222', verified=False)
            corrupt = self.bundle(directory, 'toms-20000101T000000Z-33333333')
            (corrupt / 'database.dump').write_bytes(b'corrupt')
            other = self.bundle(directory, 'other-folder')
            self.assertEqual(backups.status(directory)['last_verified_utc'], '2000-01-02T00:00:00+00:00')
            self.assertEqual(backups.prune(directory, 30), 1)
            self.assertFalse(old.exists())
            for path in (unverified, corrupt, other):
                self.assertTrue(path.exists())

    def test_symlink_bundles_are_never_pruned(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            original = self.bundle(outside, 'toms-20000101T000000Z-11111111')
            (Path(directory) / original.name).symlink_to(original, target_is_directory=True)
            self.assertEqual(backups.prune(directory, 30), 0)
            self.assertTrue(original.exists())

    def test_cli_retention_requires_successful_verification_option(self):
        with patch('sys.argv', ['backups', 'backup', '--keep-days', '30']), patch.object(backups, 'backup') as create, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                backups.main()
            self.assertEqual(caught.exception.code, 1)
            create.assert_not_called()
