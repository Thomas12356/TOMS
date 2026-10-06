"""Migration credentials are read only by maintenance commands, never Flask startup."""
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import URL, create_engine

from services.database.connection import db

MIGRATION_ENV = Path(__file__).resolve().parents[2] / '.env.migrations'


def migration_engine():
    settings = dotenv_values(MIGRATION_ENV)
    if not settings.get('PGUSER') or not settings.get('PGPASSWORD'):
        raise RuntimeError('Configure .env.migrations with the separate migration login first.')
    runtime = db.engine.url
    return create_engine(URL.create(
        'postgresql+psycopg', username=settings['PGUSER'], password=settings['PGPASSWORD'],
        host=runtime.host, port=runtime.port, database=runtime.database,
    ), connect_args={'connect_timeout': 5}, hide_parameters=True)
