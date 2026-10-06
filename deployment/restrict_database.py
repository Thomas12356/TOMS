"""One-time owner-run provisioning using the existing admin login in .env.

Run before changing PGUSER yourself. No bank calls or application imports.
"""
from pathlib import Path
import os
import secrets

from dotenv import dotenv_values, set_key
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]


def main():
    settings = dotenv_values(ROOT / '.env')
    with psycopg.connect(host=settings.get('PGHOST', 'localhost'),
                         port=settings.get('PGPORT', '5432'),
                         dbname=settings.get('PGDATABASE', 'TOMS'),
                         user=settings.get('PGUSER', 'postgres'),
                         password=settings.get('PGPASSWORD', '')) as connection:
        if not connection.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0]:
            raise RuntimeError('Provisioning requires the existing administrator login in .env.')
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname IN ('toms_app','toms_migrator')").fetchone():
            raise RuntimeError('Roles already exist; refusing to replace credentials or ownership.')
        passwords = {role: secrets.token_urlsafe(48) for role in ('toms_app', 'toms_migrator')}
        for role, password in passwords.items():
            connection.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
        connection.execute('CREATE SCHEMA IF NOT EXISTS toms')
        # Transfer only this application schema, never unrelated postgres-owned objects.
        tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='toms'").fetchall()
        for (name,) in tables:
            connection.execute(sql.SQL('ALTER TABLE toms.{} OWNER TO toms_migrator').format(sql.Identifier(name)))
        sequences = connection.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='toms'").fetchall()
        for (name,) in sequences:
            connection.execute(sql.SQL('ALTER SEQUENCE toms.{} OWNER TO toms_migrator').format(sql.Identifier(name)))
        connection.execute('ALTER SCHEMA toms OWNER TO toms_migrator')
        connection.execute('REVOKE ALL ON SCHEMA toms FROM PUBLIC')
        connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        connection.execute('GRANT USAGE ON SCHEMA toms TO toms_app')
        for (name,) in tables:
            if name != 'schema_migrations':
                connection.execute(sql.SQL('GRANT SELECT, INSERT, UPDATE, DELETE ON toms.{} TO toms_app').format(sql.Identifier(name)))
        connection.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA toms TO toms_app')
        connection.execute('ALTER DEFAULT PRIVILEGES FOR ROLE toms_migrator IN SCHEMA toms GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO toms_app')
        connection.execute('ALTER DEFAULT PRIVILEGES FOR ROLE toms_migrator IN SCHEMA toms GRANT USAGE, SELECT ON SEQUENCES TO toms_app')
        if any(name == 'schema_migrations' for (name,) in tables):
            connection.execute('REVOKE ALL ON TABLE toms.schema_migrations FROM PUBLIC, toms_app')
        database = connection.execute('SELECT current_database()').fetchone()[0]
        connection.execute(sql.SQL('GRANT CONNECT, CREATE ON DATABASE {} TO toms_migrator').format(sql.Identifier(database)))
    # Database transaction succeeded before switching runtime credentials.
    migration_file = ROOT / '.env.migrations'
    descriptor = os.open(migration_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(f"PGUSER=toms_migrator\nPGPASSWORD={passwords['toms_migrator']}\n")
    set_key(ROOT / '.env', 'PGUSER', 'toms_app')
    set_key(ROOT / '.env', 'PGPASSWORD', passwords['toms_app'])
    os.chmod(ROOT / '.env', 0o600)
    print('Provisioned restricted toms_app and separate toms_migrator; credentials saved privately.')


if __name__ == '__main__':
    if (ROOT / '.env.migrations').exists():
        raise SystemExit('.env.migrations already exists; refusing to overwrite it.')
    try:
        main()
    except (psycopg.Error, OSError, RuntimeError) as error:
        raise SystemExit(f"Provisioning failed ({type(error).__name__}); check database access and private file permissions.") from None
