"""Select actual, current tax credits and allowable expenses for the selected year."""
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.orm import selectinload, load_only

from models import Transaction, MileageEntry
from services.database.connection import db
from services.transactions.classification import classification_details
from services.tax.mileage import mileage_allowances
from services.tax.mileage_rules import cached_status
from flask import current_app


def expense_current(record):
    payment = record.transaction
    return (payment.direction == 'OUT' and payment.status == 'SETTLED' and payment.currency == 'GBP'
            and classification_details(payment)['type'] == 'expense'
            and record.recorded_amount_minor == payment.amount_minor
            and record.recorded_currency == payment.currency and record.recorded_time == payment.transaction_time
            and record.amount_minor <= payment.amount_minor)


def tax_records(rules):
    first = datetime.fromisoformat(rules['starts_on']).replace(tzinfo=ZoneInfo('Europe/London'))
    end = (datetime.fromisoformat(rules['ends_on']) + timedelta(days=1)).replace(tzinfo=ZoneInfo('Europe/London'))
    payments = db.session.scalars(db.select(Transaction).where(
        Transaction.transaction_time >= first, Transaction.transaction_time < end).options(
        load_only(Transaction.amount_minor, Transaction.currency, Transaction.direction, Transaction.status,
                  Transaction.source, Transaction.transaction_time),
        selectinload(Transaction.income), selectinload(Transaction.expense), selectinload(Transaction.classification))).all()
    credits, deductions, issues = defaultdict(int), defaultdict(int), []
    for payment in payments:
        income, expense = payment.income, payment.expense
        if income and income.tax_deducted_minor:
            if (income.needs_review or income.recorded_currency != payment.currency or payment.currency != 'GBP'
                    or payment.direction != 'IN' or payment.status != 'SETTLED'
                    or classification_details(payment)['type'] != 'income'):
                issues.append('A logged tax deduction needs review before it can be credited.')
            elif income.income_stream_id is None:
                issues.append('Assign income with logged tax deductions to a stream.')
            elif income.tax_treatment in ('paye', 'cis', 'other_deduction'):
                credits[income.income_stream_id] += income.tax_deducted_minor
        if expense:
            if not expense_current(expense):
                issues.append('A linked expense changed at the bank or needs review; its deduction is excluded.')
            else:
                deductions[expense.income_stream_id] += expense.amount_minor
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
    return dict(credits=dict(credits), deductions=dict(deductions), issues=issues)
