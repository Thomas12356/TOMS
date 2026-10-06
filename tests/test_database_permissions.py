"""Real PostgreSQL privilege boundaries; every probe is rolled back."""
import os
import unittest
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import app
from services.database.connection import db
from services.database.migration_connection import migration_engine


class MigrationConfigurationTests(unittest.TestCase):
    def test_missing_credentials_never_fall_back_to_runtime(self):
        with patch('services.database.migration_connection.dotenv_values', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'separate migration login'):
                migration_engine()


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS') == '1', 'Requires PostgreSQL.')
class DatabasePermissionTests(unittest.TestCase):
    def test_runtime_has_no_administrative_privileges(self):
        with app.app_context(), db.engine.connect() as connection:
            row = connection.execute(text('SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()
            self.assertEqual(tuple(row), (False,) * 5)
            self.assertEqual(connection.scalar(text('SELECT current_user')), 'toms_app')
            for statement in (
                'SELECT * FROM toms.schema_migrations',
                'CREATE SCHEMA toms_forbidden_probe',
                'ALTER TABLE toms.accounts ADD COLUMN forbidden_probe integer',
                'TRUNCATE toms.accounts CASCADE',
                'SET ROLE toms_migrator',
                'SET ROLE postgres',
            ):
                with self.subTest(statement=statement):
                    savepoint = connection.begin_nested()
                    try:
                        with self.assertRaises(DBAPIError) as error:
                            connection.execute(text(statement))
                        self.assertEqual(error.exception.orig.sqlstate, '42501')
                    finally:
                        savepoint.rollback()

    def test_migrator_is_separate_and_not_superuser(self):
        with app.app_context():
            engine = migration_engine()
            try:
                with engine.connect() as connection:
                    self.assertEqual(connection.scalar(text('SELECT current_user')), 'toms_migrator')
                    self.assertFalse(connection.scalar(text('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')))
                    self.assertGreater(connection.scalar(text('SELECT count(*) FROM toms.schema_migrations')), 0)
                    connection.execute(text('CREATE TABLE toms.permission_probe (id integer)'))
                    for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                        self.assertTrue(connection.scalar(text("SELECT has_table_privilege('toms_app', 'toms.permission_probe', :privilege)"), {'privilege': privilege}))
                    self.assertFalse(connection.scalar(text("SELECT has_table_privilege('toms_app', 'toms.schema_migrations', 'SELECT')")))
            finally:
                engine.dispose()
