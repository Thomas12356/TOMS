"""First-run tokens are issued on the server, never through a public endpoint."""

import secrets
from datetime import datetime, timedelta, timezone

import click
from sqlalchemy.exc import SQLAlchemyError

from models import OwnerLogin, OwnerSetup
from services.database.connection import db
from services.error_logging import log_failure
from services.web.sessions import lock_owner_setup, token_hash


def create_setup_token():
    """Rotate the one-hour token, or return None when setup is already complete."""
    lock_owner_setup()
    if db.session.get(OwnerLogin, 1) is not None:
        db.session.rollback()
        return None
    token = secrets.token_urlsafe(32)
    db.session.merge(OwnerSetup(id=1, token_hash=token_hash(token),
                               expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
    db.session.commit()
    return token


def announce_setup(app):
    """Called once by the server launcher; keep secrets out of HTTP responses."""
    with app.app_context():
        if not app.secret_key or len(app.secret_key) < 32:
            click.echo("TOMS: set SECRET_KEY in .env before using browser setup.", err=True)
            return
        try:
            token = create_setup_token()
        except SQLAlchemyError as error:
            db.session.rollback()
            log_failure("owner.startup", error)
            click.echo("TOMS: setup unavailable. Check PostgreSQL and run flask db-upgrade.", err=True)
            return
        if token is not None:
            click.echo("TOMS first-run setup: open / and enter this token within one hour:", err=True)
            click.echo(token, err=True)
