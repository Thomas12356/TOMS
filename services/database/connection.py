"""Flask-SQLAlchemy setup using the existing PostgreSQL environment settings."""

import os

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import URL, text


db = SQLAlchemy()


def init_database(app):
    # URL.create handles special characters in passwords without manual escaping.
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", URL.create(
        "postgresql+psycopg",
        username=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", ""),
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        database=os.getenv("PGDATABASE", "TOMS"),
    ))
    app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {
        "pool_pre_ping": True,
        "connect_args": {
            "connect_timeout": 5,
            "options": "-c statement_timeout=5000",
        },
    })
    db.init_app(app)


def check_database():
    """Probe the database through the request's automatically managed session."""
    return db.session.scalar(text("SELECT current_database()"))
