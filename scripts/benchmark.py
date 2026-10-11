"""Measure read paths on disposable synthetic schemas; print no financial rows."""
import json
import argparse
import os
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from flask import g
from sqlalchemy import event
from app import app
from models import Transaction
from services.database.connection import db
from services.web.test_data import sample_id
from test_test_data import TestDataTests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--multi-year', action='store_true', help='Put half the added receipts in the prior year.')
    args = parser.parse_args()
    if os.environ.get('RUN_POSTGRES_TESTS') != '1':
        parser.error('Set RUN_POSTGRES_TESTS=1 to enable disposable database fixtures.')
    fixture = TestDataTests()
    try:
        fixture.setUp()
        assert fixture.toggle(True).status_code == 303
        for size in (100, 1000, 10000):
            with app.test_request_context('/dashboard'):
                g.use_test_data = True
                current = db.session.scalar(db.select(db.func.count()).select_from(Transaction))
                for index in range(max(0, size - current)):
                    year = 2025 if args.multi_year and index % 2 else 2026
                    db.session.add(Transaction(account_uid=sample_id(1), category_uid=sample_id(11),
                        feed_item_uid=str(uuid4()), amount_minor=100, currency='GBP', direction='IN',
                        status='SETTLED', transaction_time=datetime(year, 6, 1, tzinfo=timezone.utc),
                        source_updated_at=datetime(year, 6, 1, tzinfo=timezone.utc),
                        source='FASTER_PAYMENTS_IN', counterparty_name='Synthetic benchmark', raw_payload={}))
                db.session.commit()
                db.session.remove()
            for path in ('/dashboard/income-notifications', '/dashboard/reconciliation'):
                counts = []
                def count_query(*args):
                    counts.append(1)
                event.listen(db.engine, 'before_cursor_execute', count_query)
                started = perf_counter()
                try:
                    response = fixture.client.get(path)
                    elapsed = perf_counter() - started
                finally:
                    event.remove(db.engine, 'before_cursor_execute', count_query)
                assert response.status_code == 200, response.status_code
                print(json.dumps(dict(records=size, multi_year=args.multi_year, path=path, seconds=round(elapsed, 3), queries=len(counts))))
        fixture.bank.assert_not_called()
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    main()
