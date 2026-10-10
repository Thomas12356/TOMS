"""Record successful owner actions alongside the change, never submitted values."""
from uuid import uuid4

from flask import has_request_context, request
from flask_login import current_user
from sqlalchemy import inspect, text
from psycopg import sql

from models import IncomeStream
from services.database.connection import db
from services.database.session import test_data_active

ACTIONS = {
    'reserve.save': 'Updated account tax and NI earmarks',
    'login': 'Signed in', 'logout': 'Signed out', 'setup': 'Created owner account',
    'owner.change': 'Changed owner password',
    'classification.save': 'Edited transaction classification',
    'classification.automatic': 'Restored automatic classification',
    'income.save': 'Updated transaction income details',
    'stream.create': 'Created income stream', 'stream.update': 'Updated income stream',
    'stream.archive': 'Archived income stream', 'stream.restore': 'Restored income stream',
    'shift.create': 'Added work record', 'shift.update': 'Updated work record',
    'shift.delete': 'Removed work record',
    'deduction.save_expense': 'Saved linked expense',
    'deduction.delete_expense': 'Unlinked expense',
    'deduction.save_mileage': 'Added mileage deduction',
    'deduction.delete_mileage': 'Removed mileage deduction',
    'deduction.link_mileage_shift': 'Changed mileage shift link',
    'confirm': 'Confirmed transaction details',
    'test-data.on': 'Enabled test data', 'test-data.off': 'Disabled test data',
    'rules': 'Checked official tax rules',
    'invoice.create': 'Created invoice draft', 'invoice.update': 'Updated invoice draft',
    'invoice.issue': 'Issued invoice', 'invoice.cancel': 'Cancelled invoice',
    'invoice.allocate': 'Allocated invoice payment', 'invoice.remove_payment': 'Removed invoice payment allocation',
    'sync': 'Requested bank sync', 'sync.sample': 'Simulated bank sync',
}


def record_action(action, record_id='', *, verified_owner=False, sample=None):
    """Caller commits or rolls back. Only login/setup may supply verified_owner."""
    if not has_request_context() or request.authorization is not None:
        return
    if not verified_owner and not current_user.is_authenticated:
        return
    if action not in ACTIONS or len(str(record_id)) > 160:
        raise ValueError('Unknown activity action or invalid record identifier.')
    # Use the business-write connection even in sample mode, but explicitly name
    # the real log table. This keeps both inserts in one PostgreSQL transaction.
    connection = db.session.connection(bind_arguments={'mapper': inspect(IncomeStream)})
    # Isolated maintenance/tests may map the connection without replacing db.engine.
    real_bind = db.engine if test_data_active() else connection
    real_schema = real_bind.get_execution_options().get('schema_translate_map', {}).get('toms', 'toms')
    statement = sql.SQL('''INSERT INTO {}.user_actions (id, action, sample_data, record_id)
        VALUES (:id, :action, :sample_data, :record_id)''').format(sql.Identifier(real_schema))
    connection.execute(text(statement.as_string()),
        dict(id=str(uuid4()), action=action, sample_data=test_data_active() if sample is None else sample,
             record_id=str(record_id)))
