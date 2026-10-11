"""Opt-in synthetic route benchmark; never benchmarks working financial rows.

Run RUN_POSTGRES_TESTS=1 .venv/bin/python tests/performance_review.py.
Uses the existing disposable-schema fixture and its blocked bank/rule traffic.
Timings are observations, not CI thresholds. Temporary schemas are always removed.
"""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
import tracemalloc
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from sqlalchemy import event, insert, text
from sqlalchemy.orm import Session, load_only, selectinload

from models import IncomeShift, Invoice, InvoiceDelivery, InvoiceItem, InvoicePayment, TaxProfile, Transaction, TransactionIncome
from services.database.connection import db
from services.transactions.review import REVIEW_FIELDS, payment_version
from services.web.test_data import sample_id
from test_test_data import TestDataTests


def identity(number):
    return str(UUID(int=number + 1000000))


def seed(fixture, count):
    account, category, stream = sample_id(1), sample_id(11), sample_id(21)
    start = datetime(2026, 4, 6, 12, tzinfo=timezone.utc)
    engine = fixture.admin.execution_options(schema_translate_map={'toms': fixture.real_schema})
    with Session(engine) as session, session.begin():
        session.add(TaxProfile(tax_year='2026-27', jurisdiction='england', confirmed_at=start))
        # Remove the tiny fixture receipt so benchmark counts are exact.
        session.execute(db.delete(Transaction))
        for offset in range(0, count, 1000):
            transactions, incomes = [], []
            for index in range(offset, min(count, offset + 1000)):
                stamp = start + timedelta(minutes=index % 200000) if index % 3 == 0 else start - timedelta(days=1 + index % 1460)
                incoming = index % 3 == 0
                values = dict(account_uid=account, category_uid=category, feed_item_uid=identity(index),
                    amount_minor=10000, currency='GBP', direction='IN' if incoming else 'OUT',
                    status='SETTLED', transaction_time=stamp, source_updated_at=stamp,
                    confirmed_at=None if index % 5 == 0 else stamp,
                    source='FASTER_PAYMENTS_IN' if incoming else 'FASTER_PAYMENTS_OUT',
                    counterparty_name='Synthetic payer ' + str(index % 100), reference='Synthetic reference ' + str(index % 100),
                    raw_payload={'synthetic_only': 'x' * 2048})
                transactions.append(values)
                if incoming:
                    incomes.append(dict(account_uid=account, category_uid=category, feed_item_uid=identity(index),
                        income_stream_id=stream, income_type='employment', tax_treatment='no_tax_deducted',
                        gross_minor=10000, tax_deducted_minor=0, ni_deducted_minor=0,
                        adjustment_minor=0, recorded_currency='GBP', needs_review=False))
            session.execute(insert(Transaction), transactions)
            if incomes:
                session.execute(insert(TransactionIncome), incomes)
        shifts, invoices, items, deliveries = [], [], [], []
        for index in range(count // 10):
            stamp = start + timedelta(minutes=index * 10)
            shift_id = identity(count + index)
            shifts.append(dict(id=shift_id, income_stream_id=stream, starts_at=stamp,
                ends_at=stamp + timedelta(hours=8), payment_mode='total', gross_minor=10000,
                currency='GBP', notes='Synthetic work'))
            if index % 4:
                continue
            invoice_id = identity(count * 2 + index)
            issue = date(2026, 4, 12) if index % 8 == 0 else date(2025, 4, 12)
            invoices.append(dict(id=invoice_id, income_stream_id=stream, stream_name='Synthetic source',
                number='BENCH-' + str(index), state='issued', client_name='Synthetic client',
                issue_date=issue, due_date=issue, currency='GBP', total_minor=10000, issued_at=start))
            items.append(dict(id=identity(count * 3 + index), invoice_id=invoice_id, shift_id=shift_id,
                active=True, description='Synthetic invoice item', amount_minor=10000,
                source_version='synthetic-previous-version', shift_snapshot={'synthetic': True}))
            deliveries.append(dict(id=identity(count * 4 + index), invoice_id=invoice_id,
                sent_at=start, method='email', recipient='synthetic@example.invalid', notes='Synthetic history'))
        for model, rows in ((IncomeShift, shifts), (Invoice, invoices), (InvoiceItem, items), (InvoiceDelivery, deliveries)):
            session.execute(insert(model), rows)
        receipt_ids = [identity(3 * (index * 5 + 1)) for index in range(len(invoices))]
        receipts = {row.feed_item_uid: row for row in session.scalars(db.select(Transaction).where(
            Transaction.feed_item_uid.in_(receipt_ids)).options(
                load_only(*(getattr(Transaction, field) for field in REVIEW_FIELDS), Transaction.confirmed_at),
                selectinload(Transaction.income), selectinload(Transaction.classification)))}
        allocations = [dict(id=identity(count * 5 + index), invoice_id=invoice['id'],
            account_uid=account, category_uid=category, feed_item_uid=receipt_id, cash_minor=5000,
            tax_minor=0, ni_minor=0, active=True, payment_label='Synthetic payment',
            payment_version=payment_version(receipts[receipt_id]))
            for index, (invoice, receipt_id) in enumerate(zip(invoices, receipt_ids))]
        session.execute(insert(InvoicePayment), allocations)
    # Metadata fixtures omit several migration-only indexes. Copy those index
    # definitions from the working schema's catalog, without reading its rows.
    with fixture.admin.begin() as connection:
        existing = set(connection.scalars(text('SELECT indexname FROM pg_indexes WHERE schemaname=:schema'),
                                          dict(schema=fixture.real_schema)))
        tables = set(connection.scalars(text('SELECT tablename FROM pg_tables WHERE schemaname=:schema'),
                                        dict(schema=fixture.real_schema)))
        for name, table, definition in connection.execute(text("SELECT indexname,tablename,indexdef FROM pg_indexes WHERE schemaname='toms'")):
            if name not in existing and table in tables:
                if ' ON toms.' not in definition:
                    raise RuntimeError('Unexpected index schema; refusing catalog copy.')
                connection.exec_driver_sql(definition.replace(' ON toms.', ' ON ' + fixture.real_schema + '.', 1))
        for table in ('transactions', 'transaction_income', 'income_shifts', 'invoices', 'invoice_items', 'invoice_deliveries', 'invoice_payments'):
            connection.exec_driver_sql(f'ANALYZE {fixture.real_schema}.{table}')
    return dict(transactions=count, current_year_receipts=len(range(0, count, 3)),
                work=len(shifts), invoices=len(invoices), allocations=len(allocations),
                current_year_invoices=sum(row['issue_date'].year == 2026 for row in invoices))


def measure(fixture, path, repeats):
    durations, queries, sql_times = [], [], []
    families = Counter()
    starts = []
    slowest = [0, None, None]

    def before(connection, cursor, statement, parameters, context, executemany):
        starts.append(perf_counter())

    def after(connection, cursor, statement, parameters, context, executemany):
        elapsed = perf_counter() - starts.pop()
        sql_times.append(elapsed)
        families[statement.split()[0].upper()] += 1
        if (elapsed > slowest[0] and statement.startswith('SELECT')
                and fixture.real_schema in statement):
            slowest[:] = [elapsed, statement, parameters]

    event.listen(fixture.runtime, 'before_cursor_execute', before)
    event.listen(fixture.runtime, 'after_cursor_execute', after)
    try:
        # Warm compilation/templates/cache first, then measure without tracing.
        response = fixture.client.get(path)
        fixture.assertEqual(response.status_code, 200, path)
        for _ in range(repeats):
            families.clear()
            sql_times.clear()
            started = perf_counter()
            response = fixture.client.get(path)
            fixture.assertEqual(response.status_code, 200, path)
            response.get_data()
            durations.append(perf_counter() - started)
            queries.append(sum(families.values()))
        last_sql = sum(sql_times)
    finally:
        event.remove(fixture.runtime, 'before_cursor_execute', before)
        event.remove(fixture.runtime, 'after_cursor_execute', after)
    plan_summary = None
    if slowest[1]:
        with fixture.runtime.connect() as connection, connection.begin():
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            plan = connection.exec_driver_sql('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + slowest[1], slowest[2]).scalar()[0]
        nodes = []

        def visit(node):
            nodes.append(dict(node=node['Node Type'], rows=node.get('Actual Rows'),
                              relation=node.get('Relation Name'), sort=node.get('Sort Method')))
            for child in node.get('Plans', []):
                visit(child)

        visit(plan['Plan'])
        plan_summary = dict(execution_ms=plan['Execution Time'], nodes=nodes,
                            shared_hit_blocks=plan['Plan'].get('Shared Hit Blocks', 0),
                            temp_written_blocks=plan['Plan'].get('Temp Written Blocks', 0))
    # Measure Python allocation separately so tracing does not distort timings.
    tracemalloc.start()
    try:
        response = fixture.client.get(path)
        fixture.assertEqual(response.status_code, 200, path)
        size = len(response.get_data())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    fixture.bank.assert_not_called()
    print(f'Measured {path}', file=sys.stderr, flush=True)
    return dict(path=path, median_ms=round(median(durations) * 1000, 1),
                min_ms=round(min(durations) * 1000, 1), max_ms=round(max(durations) * 1000, 1),
                query_counts=queries, last_sql_ms=round(last_sql * 1000, 1),
                python_peak_mib=round(peak / 1024**2, 1), response_bytes=size,
                slowest_query_plan=plan_summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transactions', nargs='+', type=int, default=[10000, 50000])
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if os.getenv('RUN_POSTGRES_TESTS') != '1' or any(not 100 <= count <= 100000 for count in args.transactions) or not 1 <= args.repeats <= 5:
        parser.error('Opt in with RUN_POSTGRES_TESTS=1; use 100..100000 rows and 1..5 repeats.')
    results = []
    for count in args.transactions:
        fixture = TestDataTests()
        try:
            fixture.setUp()
            shape = seed(fixture, count)
            paths = ['/dashboard', f'/dashboard?page={max(1, count // 50)}',
                     '/dashboard?q=Synthetic+reference+42', '/dashboard?attention=needs_review',
                     '/dashboard/overview', '/dashboard/reconciliation', '/dashboard/tax-export/download']
            result = dict(dataset=shape, routes=[measure(fixture, path, args.repeats) for path in paths])
            results.append(result)
            print(json.dumps(result), flush=True)
        finally:
            if not fixture.doCleanups():
                raise RuntimeError('Disposable fixture cleanup failed; inspect the fixture errors.')
        try:
            with fixture.admin.connect() as connection:
                remaining = connection.scalar(text('SELECT count(*) FROM pg_namespace WHERE nspname IN (:real,:demo)'),
                    dict(real=fixture.real_schema, demo=fixture.demo_schema))
                fixture.assertEqual(remaining, 0, 'Disposable schemas must be removed.')
        finally:
            fixture.admin.dispose()
    return results


if __name__ == '__main__':
    main()
