"""Maintenance-only creation of sample tables, using the same model definitions."""
from services.database.connection import db
from services.database.session import DATA_TABLES


def prepare_demo_schema(connection, *, rebuild=False):
    tables = [table for table in db.metadata.sorted_tables if table.name in DATA_TABLES]
    previous = connection.get_execution_options().get('schema_translate_map')
    try:
        connection = connection.execution_options(schema_translate_map={'toms': 'toms_demo'})
        if rebuild:
            db.metadata.drop_all(connection, tables=tables)
        db.metadata.create_all(connection, tables=tables)
        connection.exec_driver_sql('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA toms_demo TO toms_app')
    finally:
        connection.execution_options(schema_translate_map=previous)
