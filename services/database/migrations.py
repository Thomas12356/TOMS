"""Apply the existing versioned SQL migrations through SQLAlchemy."""

import hashlib
from pathlib import Path

from sqlalchemy import text


# SQL migration files stay in the repository's top-level migrations/ directory.
MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def upgrade_database(connection):
    """Caller owns the transaction; preserve existing migration checksums."""
    applied = []
    connection.execute(text("SELECT pg_advisory_xact_lock(6075157141257144321)"))
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS toms"))
    connection.execute(text("""CREATE TABLE IF NOT EXISTS toms.schema_migrations (
        version TEXT PRIMARY KEY, checksum TEXT NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""))
    for path in sorted(MIGRATIONS.glob("[0-9]*.sql")):
        sql = path.read_text()
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        row = connection.execute(text(
            "SELECT checksum FROM toms.schema_migrations WHERE version = :version"),
            {"version": path.name}).first()
        if row:
            if row[0] != checksum:
                raise RuntimeError("An applied migration was modified: " + path.name)
            continue
        connection.exec_driver_sql(sql)
        connection.execute(text(
            "INSERT INTO toms.schema_migrations (version, checksum) VALUES (:version, :checksum)"),
            {"version": path.name, "checksum": checksum})
        applied.append(path.name)
    return applied
