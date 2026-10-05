"""Gunicorn loads this file from the project directory before starting workers."""


def on_starting(server):
    # Issue a single token in the master, rather than rotating it in every worker.
    from app import app
    from services.database.connection import db
    from services.web.setup import announce_setup

    announce_setup(app)
    # Workers must create their own database connections after the fork.
    with app.app_context():
        db.engine.dispose()
