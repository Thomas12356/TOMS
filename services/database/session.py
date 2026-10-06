"""Route browser test-mode business records to their own PostgreSQL schema.

Authentication models always use the normal schema. The request flag is set
only after browser authentication, never by an API key or background worker.
"""
from flask import current_app, g, has_request_context
from flask_sqlalchemy.session import Session
from sqlalchemy.sql import visitors
from sqlalchemy.sql.schema import Table
from sqlalchemy.sql.elements import TextClause

DATA_TABLES = frozenset({'accounts', 'categories', 'transactions', 'transaction_classifications',
                         'transaction_income', 'income_streams', 'income_shifts', 'expense_deductions', 'mileage_entries', 'sync_runs', 'sync_targets'})


def test_data_active():
    return has_request_context() and getattr(g, 'use_test_data', False) is True


class DataSession(Session):
    def get_bind(self, mapper=None, clause=None, bind=None, **kwargs):
        engine = super().get_bind(mapper=mapper, clause=clause, bind=bind, **kwargs)
        if not test_data_active():
            return engine
        elements = tuple(visitors.iterate(clause)) if clause is not None else ()
        if any(isinstance(node, TextClause) for node in elements):
            raise RuntimeError('Raw SQL is not supported for browser test data.')
        tables = set()
        if mapper is not None:
            tables.add(mapper.local_table)
        tables.update(node for node in elements if isinstance(node, Table))
        if not tables or any(table.schema == 'toms' and table.name in DATA_TABLES for table in tables):
            if any(table.schema == 'toms' and table.name not in DATA_TABLES for table in tables):
                raise RuntimeError('Test records and authentication must not share a query.')
            cache = g.setdefault('_test_data_engines', {})
            if engine not in cache:
                cache[engine] = engine.execution_options(schema_translate_map={'toms': current_app.config.get('TEST_DATA_SCHEMA', 'toms_demo')})
            return cache[engine]
        return engine
