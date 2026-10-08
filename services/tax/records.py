"""Select actual, current tax credits and allowable expenses for the selected year."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import selectinload, load_only

from models import Transaction, MileageEntry, ExpenseDeduction
from services.database.connection import db
from services.transactions.classification import classification_details
from services.tax.mileage import mileage_allowances
from services.tax.mileage_rules import cached_status
from flask import current_app
from services.tax.readiness import add_attention, check_income


def expense_current(record):
    payment = record.transaction
    return (payment.direction == 'OUT' and payment.status == 'SETTLED' and payment.currency == 'GBP'
            and classification_details(payment)['type'] == 'expense'
            and record.recorded_amount_minor == payment.amount_minor
            and record.recorded_currency == payment.currency and record.recorded_time == payment.transaction_time
            and record.amount_minor <= payment.amount_minor)


def tax_records(rules, *, as_of=None):
    first = datetime.fromisoformat(rules['starts_on']).replace(tzinfo=ZoneInfo('Europe/London'))
    end = (datetime.fromisoformat(rules['ends_on']) + timedelta(days=1)).replace(tzinfo=ZoneInfo('Europe/London'))
    payments = db.session.scalars(db.select(Transaction).outerjoin(Transaction.expense).where(db.or_(
        db.and_(Transaction.transaction_time >= first, Transaction.transaction_time < end),
        db.and_(ExpenseDeduction.recorded_time >= first, ExpenseDeduction.recorded_time < end))).options(
        load_only(Transaction.amount_minor, Transaction.currency, Transaction.direction, Transaction.status,
                  Transaction.source, Transaction.transaction_time, Transaction.confirmed_at, Transaction.counterparty_name),
        selectinload(Transaction.income), selectinload(Transaction.expense), selectinload(Transaction.classification))).all()
    credits, deductions, issues = defaultdict(int), defaultdict(int), []
    received, ni_paid, attention = defaultdict(int), defaultdict(int), {}
    received_issues = []
    now = as_of or datetime.now(timezone.utc)
    for payment in payments:
        income, expense = payment.income, payment.expense
        # Include the saved claim date in the query so moving a bank payment into
        # another year cannot silently hide a claim that now needs review.
        if expense:
            if not expense_current(expense):
                add_attention(attention, 'deductions', 'Deductions need review', '/dashboard/deductions')
                issues.append('A linked expense changed at the bank or needs review; its deduction is excluded.')
            elif first <= payment.transaction_time < end and payment.transaction_time <= now:
                deductions[expense.income_stream_id] += expense.amount_minor
        if not first <= payment.transaction_time < end or payment.transaction_time > now:
            continue
        if payment.status == 'SETTLED' and payment.confirmed_at is None:
            add_attention(attention, 'confirmation', 'Transactions awaiting your confirmation',
                          '/dashboard?account=all')
        if payment.status == 'SETTLED' and payment.direction == 'IN' and classification_details(payment)['type'] == 'income':
            complete = check_income(payment, attention)
            if not complete or payment.confirmed_at is None:
                received_issues.append('Complete and confirm income receipts before using the received-income target.')
            elif payment.income.tax_treatment != 'non_taxable':
                received[payment.income.income_stream_id] += payment.income.gross_minor

        if income and income.tax_deducted_minor:
            if (income.needs_review or income.recorded_currency != payment.currency or payment.currency != 'GBP'
                    or payment.direction != 'IN' or payment.status != 'SETTLED'
                    or classification_details(payment)['type'] != 'income'):
                issues.append('A logged tax deduction needs review before it can be credited.')
            elif income.income_stream_id is None:
                issues.append('Assign income with logged tax deductions to a stream.')
            elif income.tax_treatment in ('paye', 'cis', 'other_deduction'):
                credits[income.income_stream_id] += income.tax_deducted_minor
        if income and payment.status == 'SETTLED' and not income.needs_review and income.recorded_currency == payment.currency == 'GBP' and payment.direction == 'IN' and classification_details(payment)['type'] == 'income':
            if income.income_stream_id:
                ni_paid[income.income_stream_id] += income.ni_deducted_minor or 0
    journeys = db.session.scalars(db.select(MileageEntry).options(selectinload(MileageEntry.income_stream))).all()
    if journeys:
        status = cached_status(current_app.instance_path)
        if status.get('status') == 'needs_review':
            issues.append('Mileage rules need review; mileage is excluded until verified.')
        else:
            try:
                for key, amount in mileage_allowances(journeys).items():
                    deductions[key] += amount
            except ValueError as invalid:
                issues.append(str(invalid))
        if any(entry.location == 'scotland' for entry in journeys):
            issues.append('Scottish income-tax rates are not supported. UK mileage is recorded, but this England/Wales/NI estimate cannot confirm Scottish tax.')
    return dict(credits=dict(credits), deductions=dict(deductions), issues=list(dict.fromkeys(issues)),
                received=dict(received), ni_paid=dict(ni_paid), attention=list(attention.values()),
                received_issues=list(dict.fromkeys(received_issues)))
