"""Authenticated browser-only demo selection and fixed, synthetic fixtures."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from flask import g, request, session
from flask_login import current_user

from models import Account, Category, IncomeStream, IncomeShift, Transaction, TransactionIncome, SyncRun
from services.database.connection import db
from services.database.session import test_data_active


def sample_id(number):
    return str(UUID(int=number))


def activate_test_data():
    # Auth has already run. Bearer/API and scheduled commands never opt in.
    g.use_test_data = (request.authorization is None and current_user.is_authenticated
                       and session.get('use_test_data') is True)


def ensure_sample_data():
    """Seed once under a database lock; subsequent visits preserve demo edits."""
    if not test_data_active():
        raise RuntimeError('Sample data requires an authenticated test-mode request.')
    db.session.execute(db.select(db.func.pg_advisory_xact_lock(6075157141257144334)))
    if db.session.get(Account, sample_id(1)) is not None:
        db.session.commit()
        return
    now = datetime.now(timezone.utc)
    for number, name in ((1, 'Demo current account'), (2, 'Demo savings account')):
        db.session.add(Account(account_uid=sample_id(number), default_category_uid=sample_id(number + 10),
                               currency='GBP', name=name, raw_payload={}))
    db.session.flush()
    for number in (1, 2):
        db.session.add(Category(account_uid=sample_id(number), category_uid=sample_id(number + 10), kind='main'))
    db.session.add_all([
        IncomeStream(id=sample_id(21), name='Demo employment', kind='employed', expected_gross_minor=3980000,
                     expected_gross_period='yearly', expected_gross_currency='GBP'),
        IncomeStream(id=sample_id(22), name='Demo freelance work', kind='self_employed', expected_gross_minor=2000000,
                     expected_gross_period='yearly', expected_gross_currency='GBP'),
        IncomeStream(id=sample_id(23), name='Demo CIS work', kind='cis', expected_gross_minor=1000000,
                     expected_gross_period='yearly', expected_gross_currency='GBP'),
    ])
    db.session.flush()
    # The £200 overtime is additional to the £39,800 salary: £40,000 combined.
    # Use a completed date in the reviewed year, not a future planned shift.
    db.session.add(IncomeShift(id=sample_id(41), income_stream_id=sample_id(21),
        starts_at=datetime(2026, 4, 10, 8, tzinfo=timezone.utc),
        ends_at=datetime(2026, 4, 10, 16, tzinfo=timezone.utc),
        payment_mode='hourly', hourly_rate_minor=2500, gross_minor=20000,
        currency='GBP', is_overtime=True, notes='Sample overtime — test data only'))
    # A mix of income, spending, a pending payment and a savings transfer.
    examples = [(31, 1, 'IN', 280000, 'Demo employer', 'FASTER_PAYMENTS_IN', 'SETTLED', 0),
                (32, 1, 'IN', 85000, 'Demo freelance client', 'FASTER_PAYMENTS_IN', 'SETTLED', 1),
                (33, 1, 'IN', 80000, 'Demo CIS contractor', 'FASTER_PAYMENTS_IN', 'SETTLED', 2),
                (34, 1, 'OUT', 4250, 'Demo groceries', 'CARD', 'SETTLED', 0),
                (35, 1, 'OUT', 65000, 'Demo rent', 'FASTER_PAYMENTS_OUT', 'SETTLED', 3),
                (36, 1, 'OUT', 1299, 'Demo subscription', 'CARD', 'PENDING', 1),
                (37, 2, 'IN', 50000, 'Demo savings transfer', 'INTERNAL_TRANSFER', 'SETTLED', 4)]
    for item, account, direction, amount, name, source, status, days in examples:
        stamp = now - timedelta(days=days)
        db.session.add(Transaction(account_uid=sample_id(account), category_uid=sample_id(account + 10),
                                   feed_item_uid=sample_id(item), direction=direction, amount_minor=amount,
                                   currency='GBP', status=status, transaction_time=stamp, source_updated_at=stamp,
                                   source=source, counterparty_name=name, reference='Sample payment — test data only', raw_payload={}))
    db.session.flush()
    for item, stream, gross, tax, treatment in ((31, 21, 333333, 45717, 'paye'),
                                              (32, 22, 85000, 0, 'no_tax_deducted'),
                                              (33, 23, 100000, 20000, 'cis')):
        # The payroll adjustment represents NI, excluded from income-tax credits.
        amount = next(example[3] for example in examples if example[0] == item)
        adjustment = amount - (gross - tax)
        db.session.add(TransactionIncome(account_uid=sample_id(1), category_uid=sample_id(11), feed_item_uid=sample_id(item),
                                         income_stream_id=sample_id(stream), income_type='employment' if item == 31 else 'other',
                                         tax_treatment=treatment, source_name='Sample income', gross_minor=gross, tax_deducted_minor=tax,
                                         adjustment_minor=adjustment, adjustment_notes='Demo payroll deductions' if adjustment else None,
                                         recorded_currency='GBP'))
    record_sample_sync()


def record_sample_sync():
    """Simulate the sync status; never start an importer or bank request."""
    if not test_data_active():
        raise RuntimeError('Sample sync requires test mode.')
    now = datetime.now(timezone.utc)
    db.session.add(SyncRun(run_uid=str(uuid4()), status='completed', requested_options={},
                           snapshot_at=now, started_at=now, finished_at=now, rows_changed=0))
    db.session.commit()


def sample_balances(accounts, format_amount, *, combined):
    # Fixed sample balances, independent of imported real balances or receipts.
    amounts = {sample_id(1): 245075, sample_id(2): 510000}
    cards = [dict(name=account.name, amount=format_amount(amounts.get(account.account_uid, 0), account.currency), error=None) for account in accounts]
    payload = dict(balances=cards)
    if combined:
        payload['totals'] = [dict(name='Total balance · All demo accounts',
                                 amount=format_amount(sum(amounts.get(account.account_uid, 0) for account in accounts), 'GBP'), error=None)] if accounts else []
    return payload
