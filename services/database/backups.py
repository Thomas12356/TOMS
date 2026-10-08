"""Offline PostgreSQL backups. Never imports Flask or calls the bank.

A bundle is published only after pg_dump and a snapshot-consistent manifest succeed.
Restore tests use a randomly named database; recovery refuses nonempty databases.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess  # nosec B404 # fixed PostgreSQL tools, argument lists, never a shell.
import sys
import tempfile
import uuid

from dotenv import dotenv_values
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ('toms', 'toms_demo')


def settings(maintenance=True):
    values = dict(dotenv_values(ROOT / '.env'))
    if maintenance:
        credentials = dotenv_values(ROOT / '.env.migrations')
        if not credentials.get('PGUSER') or not credentials.get('PGPASSWORD'):
            raise RuntimeError('Configure separate .env.migrations credentials.')
        values.update(credentials)
    result = {key: str(values.get(key) or '') for key in
              ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD')}
    if not all(result.values()):
        raise RuntimeError('Configure PG settings and separate .env.migrations credentials.')
    return result


def connect(config, **kwargs):
    return psycopg.connect(host=config['PGHOST'], port=config['PGPORT'],
                           dbname=config['PGDATABASE'], user=config['PGUSER'],
                           password=config['PGPASSWORD'], connect_timeout=10, **kwargs)


def run_tool(name, arguments, config):
    # Pass no inherited database options, startup hooks or credentials to child tools.
    local = Path(sys.executable).parent / name
    executable = str(local) if local.is_file() and os.access(local, os.X_OK) else shutil.which(name)
    if not executable:
        raise RuntimeError(f'Install PostgreSQL client tools: {name} is missing.')
    environment = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    environment.update(config, PGCONNECT_TIMEOUT='10')
    result = subprocess.run([executable, *arguments], env=environment,  # nosec B603 # tool name fixed by callers; no shell.
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=1800)
    if result.returncode:
        # PostgreSQL errors may contain record contents; never print stderr.
        raise RuntimeError(f'{name} failed; check server access, permissions and client version.')


def fingerprint(connection):
    """Hash every row, in a stable order, without writing financial data to JSON."""
    tables = connection.execute("""SELECT schemaname, tablename FROM pg_tables
        WHERE schemaname IN ('toms', 'toms_demo') ORDER BY schemaname, tablename""").fetchall()
    if not any(schema == 'toms' and table == 'schema_migrations' for schema, table in tables):
        raise RuntimeError('The database is not an initialized TOMS database.')
    result = {}
    for schema, table in tables:
        digest = hashlib.sha256()
        count = 0
        with connection.cursor(name='backup_' + uuid.uuid4().hex) as cursor:
            cursor.execute(sql.SQL('SELECT to_jsonb(t)::text FROM {}.{} t ORDER BY to_jsonb(t)::text COLLATE "C"')
                           .format(sql.Identifier(schema), sql.Identifier(table)))
            for (row,) in cursor:
                digest.update(row.encode('utf-8') + b'\n')
                count += 1
        result[f'{schema}.{table}'] = {'rows': count, 'sha256': digest.hexdigest()}
    return result


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def private_json(path, value):
    with path.open('x', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        json.dump(value, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def backup(directory, config):
    directory = Path(directory).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise RuntimeError('Backup directory must be private (chmod 700).')
    work = Path(tempfile.mkdtemp(prefix='.partial-', dir=directory))
    try:
        dump = work / 'database.dump'
        with connect(config) as connection:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            connection.execute("SET LOCAL timezone = 'UTC'")
            snapshot = connection.execute('SELECT pg_export_snapshot()').fetchone()[0]
            manifest = {'format': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
                        'source_database': config['PGDATABASE'], 'tables': fingerprint(connection)}
            run_tool('pg_dump', ['--format=custom', '--no-owner', '--no-acl',
                                '--schema=toms', '--schema=toms_demo', '--snapshot=' + snapshot,
                                '--file=' + str(dump)], config)
        os.chmod(dump, 0o600)
        with dump.open('rb') as handle:
            os.fsync(handle.fileno())
        manifest['archive_sha256'] = file_hash(dump)
        private_json(work / 'manifest.json', manifest)
        destination = directory / ('toms-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
        work.rename(destination)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return destination
    finally:
        if work.exists():
            shutil.rmtree(work)


def load_bundle(bundle):
    bundle = Path(bundle).resolve()
    manifest_path = bundle / 'manifest.json'
    if manifest_path.stat().st_size > 1024 * 1024:
        raise RuntimeError('Backup manifest is too large.')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('format') != 1 or not manifest.get('tables'):
        raise RuntimeError('Unsupported or incomplete backup manifest.')
    if file_hash(bundle / 'database.dump') != manifest.get('archive_sha256'):
        raise RuntimeError('Backup archive checksum does not match; refusing restore.')
    return bundle, manifest


def restore(bundle, target, source_database):
    bundle, manifest = load_bundle(bundle)
    if target['PGDATABASE'] in (source_database, manifest['source_database'], 'postgres', 'template0', 'template1'):
        raise RuntimeError('Refusing to restore into the source or a system database.')
    with connect(target, autocommit=True) as connection:
        # Serialize this tool's restores, and refuse any existing user schema/object.
        connection.execute('SELECT pg_advisory_lock(20261008, 1)')
        occupied = connection.execute("""SELECT EXISTS (
            SELECT 1 FROM pg_namespace WHERE nspname NOT IN ('public','information_schema')
            AND nspname NOT LIKE 'pg_%') OR EXISTS (
            SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public') OR EXISTS (
            SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public') OR EXISTS (
            SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public')""").fetchone()[0]
        if occupied:
            raise RuntimeError('Restore target must be a new, empty database.')
        run_tool('pg_restore', ['--single-transaction', '--exit-on-error', '--no-owner', '--no-acl',
                                '--dbname=' + target['PGDATABASE'], str(bundle / 'database.dump')], target)
        with connection.transaction():
            connection.execute("SET LOCAL timezone = 'UTC'")
            if fingerprint(connection) != manifest['tables']:
                raise RuntimeError('Restored records differ from the backup snapshot.')
        # pg_restore recreates constraints; check no unvalidated constraints slipped in.
        invalid = connection.execute("""SELECT count(*) FROM pg_constraint c JOIN pg_namespace n
            ON n.oid=c.connamespace WHERE n.nspname IN ('toms','toms_demo') AND NOT c.convalidated""").fetchone()[0]
        if invalid:
            raise RuntimeError('Restored database contains unvalidated constraints.')
    return manifest


def verify(bundle, source, admin):
    """CREATEDB login is separate from the app/migrator and only for recovery tests."""
    name = 'toms_restore_test_' + uuid.uuid4().hex
    target = dict(admin, PGDATABASE=name)
    with connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
        try:
            manifest = restore(bundle, target, source['PGDATABASE'])
        finally:
            connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
    # Only successful restore + cleanup produces a success marker.
    marker = Path(bundle) / ('verified-' + uuid.uuid4().hex + '.json')
    private_json(marker, {'verified_utc': datetime.now(timezone.utc).isoformat(),
                          'archive_sha256': manifest['archive_sha256'], 'tables': len(manifest['tables'])})
    return marker


def verification_settings(source):
    values = dotenv_values(ROOT / '.env.backup-test')
    if not values.get('PGUSER') or not values.get('PGPASSWORD'):
        raise RuntimeError('Configure separate CREATEDB credentials in .env.backup-test (see DATA_STORAGE.md).')
    return dict(source, **{key: str(value) for key, value in values.items() if key in source and value is not None})


def prune(directory, keep_days):
    """Remove only old, intact, verified bundles; never follow symlinks."""
    if keep_days < 1:
        raise RuntimeError('Retention must be at least one day.')
    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
    removed = 0
    for bundle in Path(directory).iterdir():
        if bundle.is_symlink() or not bundle.is_dir() or not re.fullmatch(r'toms-\d{8}T\d{6}Z-[0-9a-f]{8}', bundle.name):
            continue
        if any(path.is_symlink() for path in bundle.rglob('*')):
            continue
        try:
            _, manifest = load_bundle(bundle)
            created = datetime.fromisoformat(manifest['created_utc']).timestamp()
            verified = any(json.loads(path.read_text()).get('archive_sha256') == manifest['archive_sha256']
                           for path in bundle.glob('verified-*.json') if path.stat().st_size < 4096)
            if verified and created < cutoff:
                shutil.rmtree(bundle)
                removed += 1
        except (OSError, ValueError, KeyError, RuntimeError):
            continue  # Damaged/unrecognized backups need investigation, never auto-deletion.
    return removed


def status(directory):
    bundles = []
    verified = []
    if Path(directory).exists():
        for bundle in Path(directory).glob('toms-*'):
            if bundle.is_symlink() or not bundle.is_dir():
                continue
            try:
                _, manifest = load_bundle(bundle)
                bundles.append(manifest['created_utc'])
                for marker in bundle.glob('verified-*.json'):
                    if marker.stat().st_size < 4096:
                        value = json.loads(marker.read_text())
                        if value.get('archive_sha256') == manifest['archive_sha256']:
                            verified.append(value['verified_utc'])
            except (OSError, ValueError, KeyError, RuntimeError):
                continue
    return {'last_backup_utc': max(bundles, default=None),
            'last_verified_utc': max(verified, default=None)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('backup')
    create.add_argument('--directory', default=str(ROOT / 'instance/backups'))
    create.add_argument('--verify', action='store_true')
    create.add_argument('--keep-days', type=int, help='Prune older verified bundles only after this backup passes verification.')
    show = commands.add_parser('status')
    show.add_argument('--directory', default=str(ROOT / 'instance/backups'))
    check = commands.add_parser('verify')
    check.add_argument('bundle', type=Path)
    recovery = commands.add_parser('restore')
    recovery.add_argument('bundle', type=Path)
    recovery.add_argument('--database', required=True, help='New empty database; never the working database.')
    arguments = parser.parse_args()
    try:
        if arguments.command == 'status':
            print(json.dumps(status(arguments.directory), indent=2))
            return
        if arguments.command == 'backup' and arguments.keep_days is not None and (not arguments.verify or arguments.keep_days < 1):
            raise RuntimeError('Retention requires --verify and a positive number of days.')
        source = settings()
        if arguments.command == 'backup':
            bundle = backup(arguments.directory, source)
            print(f'Backup saved: {bundle}')
            if arguments.verify:
                verify(bundle, source, verification_settings(source))
                print('Restore verification passed; temporary database removed.')
                if arguments.keep_days is not None:
                    print(f'Older verified bundles removed: {prune(arguments.directory, arguments.keep_days)}')
        elif arguments.command == 'verify':
            verify(arguments.bundle, source, verification_settings(source))
            print('Restore verification passed; temporary database removed.')
        else:
            restore(arguments.bundle, dict(source, PGDATABASE=arguments.database), source['PGDATABASE'])
            print('Restored records verified. Apply runtime grants before switching the app; see DATA_STORAGE.md.')
    except RuntimeError as error:
        parser.exit(1, str(error) + '\n')
    except (OSError, ValueError, psycopg.Error, subprocess.TimeoutExpired, KeyError):
        # No server errors, configuration values or financial rows in terminal logs.
        parser.exit(1, 'Backup operation failed. Check tools, private configuration, disk space and database permissions. See DATA_STORAGE.md.\n')


if __name__ == '__main__':
    main()
