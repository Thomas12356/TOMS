"""Read backup metadata without hashing large archives during a web request."""
from datetime import datetime, timezone
import json
from pathlib import Path

from sqlalchemy import text


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def metadata(path):
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Invalid metadata file.')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Invalid metadata object.')
    return value


def backup_inventory(directory):
    directory = Path(directory)
    rows, total_bytes, count = [], 0, 0
    try:
        if directory.is_symlink():
            raise OSError('Backup directory must not be a symlink.')
        if not directory.exists():
            return dict(rows=[], total_bytes=0, count=0, error=None)
        bundles = sorted((path for path in directory.glob('toms-*')
                          if path.is_dir() and not path.is_symlink()), reverse=True)
        for bundle in bundles:
            size = 0
            for path in bundle.iterdir():
                if path.is_file() and not path.is_symlink():
                    size += path.stat().st_size
            total_bytes += size
            count += 1
            if len(rows) >= 50:
                continue
            row = dict(name=bundle.name, size=size, created=None, verified=None, state='Incomplete')
            try:
                manifest = metadata(bundle / 'manifest.json')
                archive = bundle / 'database.dump'
                if manifest.get('format') != 1 or not manifest.get('tables') or archive.is_symlink() or not archive.is_file():
                    raise ValueError('Incomplete archive.')
                row['created'] = timestamp(manifest.get('created_utc'))
                if row['created'] is None or not isinstance(manifest.get('archive_sha256'), str):
                    raise ValueError('Incomplete manifest.')
                row['state'] = 'Not restore-tested'
                checks = []
                for marker in bundle.glob('verified-*.json'):
                    try:
                        result = metadata(marker)
                        when = timestamp(result.get('verified_utc'))
                        if when and result.get('archive_sha256') == manifest['archive_sha256']:
                            checks.append(when)
                    except (OSError, ValueError):
                        continue
                if checks:
                    row.update(verified=max(checks), state='Restore tested')
            except (OSError, ValueError):
                pass
            rows.append(row)
        return dict(rows=rows, total_bytes=total_bytes, count=count, error=None)
    except OSError:
        return dict(rows=[], total_bytes=0, count=0, error='Backup storage is unavailable. Check directory permissions.')


def database_storage(connection):
    total = connection.execute(text('SELECT pg_database_size(current_database())')).scalar_one()
    schemas = dict(connection.execute(text("""SELECT n.nspname, coalesce(sum(pg_total_relation_size(c.oid)),0)::bigint
        FROM pg_namespace n LEFT JOIN pg_class c ON c.relnamespace=n.oid AND c.relkind IN ('r','m')
        WHERE n.nspname IN ('toms','toms_demo') GROUP BY n.nspname""")).all())
    return dict(total=total, real=schemas.get('toms', 0), sample=schemas.get('toms_demo', 0))


def format_bytes(value):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:,.0f} {unit}' if unit == 'B' else f'{value:,.1f} {unit}'
        value /= 1024


def format_timestamp(value, seconds=False):
    if value is None:
        return 'Unknown date'
    return value.astimezone(timezone.utc).strftime('%d %b %Y, %H:%M:%S UTC' if seconds else '%d %b %Y, %H:%M UTC')
