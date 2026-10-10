"""Portable evidence package. Forecasts and bank receipts remain separate."""
import csv
import io
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zipfile import ZipFile, ZIP_DEFLATED
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy.orm import Session, selectinload, load_only

from models import IncomeStream, IncomeShift, Transaction, ExpenseDeduction, MileageEntry, Invoice
from services.database.connection import db
from services.database.session import test_data_active
from services.invoices.workflow import invoice_query, payment_current
from services.tax.records import expense_current
from services.tax.reserves import current_plan
from services.tax.rules import reviewed_rules
from services.tax.national_insurance import reviewed_rules as ni_rules
from services.tax.mileage_rules import reviewed_rules as mileage_rules
from services.transactions.classification import classification_details


@contextmanager
def export_session():
    # Authentication uses its normal session. Business evidence gets one isolated
    # snapshot, routed through DataSession so sample mode keeps its own schema.
    engine = db.session.get_bind(mapper=IncomeStream.__mapper__)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin(), Session(bind=connection) as session:
            connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            yield session


def scalar(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def record(obj, fields):
    return {field: scalar(getattr(obj, field)) for field in fields.split()}


def csv_bytes(rows, fields):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        safe = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, default=scalar, ensure_ascii=False)
            # Quote spreadsheet formulas, including leading whitespace/control
            # characters. JSON retains the original evidence exactly.
            if isinstance(value, str) and (value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n'))):
                value = "'" + value
            safe[key] = value
        writer.writerow(safe)
    return output.getvalue().encode('utf-8-sig')


FIELDS = {
    'streams': 'id name kind income_mode archived expected_gross_minor expected_gross_period expected_gross_currency unpaid_holiday_weeks unpaid_holiday_unit forecast_tax_year forecast_starts_on forecast_ends_on mileage_pool_id',
    'work': 'id income_stream_id starts_at ends_at unpaid_break_minutes payment_mode is_overtime hourly_rate_minor gross_minor currency notes',
    'transactions': 'account_uid category_uid feed_item_uid amount_minor currency direction status transaction_time confirmed_at source counterparty_name reference',
    'income': 'account_uid category_uid feed_item_uid income_stream_id income_type tax_treatment source_name gross_minor tax_deducted_minor ni_deducted_minor adjustment_minor adjustment_notes recorded_currency needs_review',
    'expenses': 'account_uid category_uid feed_item_uid income_stream_id shift_id is_tax_deductible amount_minor purpose category vehicle_key recorded_amount_minor recorded_currency recorded_time',
    'mileage': 'id income_stream_id shift_id journey_date location vehicle_type vehicle_key miles purpose start_postcode end_postcode reimbursed_minor',
    'invoices': 'id income_stream_id stream_name number state revision client_name client_address reference business_name business_address business_contact payment_instructions issue_date due_date currency total_minor created_at issued_at cancelled_at',
    'invoice_items': 'id invoice_id shift_id active description amount_minor source_version shift_snapshot',
    'allocations': 'id invoice_id account_uid category_uid feed_item_uid cash_minor tax_minor ni_minor payment_label active created_at',
    'sending_history': 'id invoice_id sent_at method recipient notes recorded_at',
}


def package(session):
    rules = reviewed_rules()
    first = datetime.fromisoformat(rules['starts_on']).replace(tzinfo=ZoneInfo('Europe/London'))
    end = (datetime.fromisoformat(rules['ends_on']) + timedelta(days=1)).replace(tzinfo=ZoneInfo('Europe/London'))
    now = datetime.now(timezone.utc)
    tables = {name: [] for name in FIELDS}
    estimate = current_plan(refresh_rules=False, session=session)
    calculation = {key: value for key, value in estimate.items() if key != 'rows'}
    calculation['rows'] = [{**{key: value for key, value in row.items() if key != 'stream'},
                             'income_stream_id': row['stream'].id} for row in estimate['rows']]
    streams = session.scalars(db.select(IncomeStream).where(IncomeStream.forecast_tax_year == rules['tax_year']).order_by(IncomeStream.id)).all()
    tables['streams'] = [record(obj, FIELDS['streams']) for obj in streams]
    work = session.scalars(db.select(IncomeShift).where(IncomeShift.starts_at >= first, IncomeShift.starts_at < end).order_by(IncomeShift.starts_at, IncomeShift.id)).all()
    tables['work'] = [record(obj, FIELDS['work']) for obj in work]
    payments = session.scalars(db.select(Transaction).outerjoin(Transaction.expense).where(db.or_(
        db.and_(Transaction.transaction_time >= first, Transaction.transaction_time < end),
        db.and_(ExpenseDeduction.recorded_time >= first, ExpenseDeduction.recorded_time < end))).options(
        load_only(*(getattr(Transaction, field) for field in FIELDS['transactions'].split())),
        selectinload(Transaction.income), selectinload(Transaction.expense), selectinload(Transaction.classification)).order_by(Transaction.transaction_time, Transaction.feed_item_uid)).all()
    invoices = session.scalars(invoice_query().where(Invoice.issue_date >= first.date(), Invoice.issue_date < end.date()).order_by(Invoice.issue_date, Invoice.id)).all()
    # Complete allocation evidence can cross the tax-year boundary.
    indexed = {(p.account_uid, p.category_uid, p.feed_item_uid): p for p in payments}
    for invoice in invoices:
        tables['invoices'].append(record(invoice, FIELDS['invoices']))
        for item in sorted(invoice.items, key=lambda obj: obj.id):
            tables['invoice_items'].append(record(item, FIELDS['invoice_items']))
        for payment in sorted(invoice.payments, key=lambda obj: obj.id):
            tables['allocations'].append({**record(payment, FIELDS['allocations']), 'current': payment_current(payment, invoice)})
            indexed[(payment.account_uid, payment.category_uid, payment.feed_item_uid)] = payment.transaction
        tables['sending_history'].extend(record(obj, FIELDS['sending_history']) for obj in sorted(invoice.deliveries, key=lambda obj: (obj.sent_at, obj.id)))
    for payment in sorted(indexed.values(), key=lambda obj: (obj.transaction_time, obj.feed_item_uid)):
        in_year = first <= payment.transaction_time < end
        tables['transactions'].append({**record(payment, FIELDS['transactions']),
            'classification': classification_details(payment)['type'], 'in_tax_year': in_year,
            'settled_and_not_future': payment.status == 'SETTLED' and payment.transaction_time <= now})
        if payment.income:
            tables['income'].append(record(payment.income, FIELDS['income']))
        if payment.expense:
            tables['expenses'].append({**record(payment.expense, FIELDS['expenses']), 'current': expense_current(payment.expense)})
    journeys = session.scalars(db.select(MileageEntry).where(MileageEntry.journey_date >= first.date(), MileageEntry.journey_date < end.date()).order_by(MileageEntry.journey_date, MileageEntry.id)).all()
    tables['mileage'] = [record(obj, FIELDS['mileage']) for obj in journeys]
    manifest = {'format_version': 1, 'tax_year': rules['tax_year'], 'generated_at': now.isoformat(),
                'sample_data': test_data_active(), 'currency': 'GBP', 'money_unit': 'minor units (pence for GBP)',
                'counts': {name: len(rows) for name, rows in tables.items()}, 'calculation': calculation,
                'income_tax_rules': rules, 'national_insurance_rules': ni_rules(), 'mileage_rules': mileage_rules(), 'records': tables}
    output = io.BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('report.json', json.dumps(manifest, default=scalar, ensure_ascii=False, indent=2))
        archive.writestr('README.txt', instructions(manifest))
        archive.writestr('report.html', current_app.jinja_env.get_template('tax_export_report.html').render(manifest=manifest, money=money))
        for name, rows in tables.items():
            extras = {'transactions': ['classification', 'in_tax_year', 'settled_and_not_future'],
                      'expenses': ['current'], 'allocations': ['current']}
            fields = FIELDS[name].split() + extras.get(name, [])
            archive.writestr(name + '.csv', csv_bytes(rows, fields))
    output.seek(0)
    return output, manifest


def money(amount, currency='GBP'):
    return 'Unavailable' if amount is None else f"{currency} {Decimal(amount) / 100:,.2f}"


def instructions(manifest):
    return f"""TOMS tax-year evidence: {manifest['tax_year']}
Generated: {manifest['generated_at']}
Data: {'SAMPLE DATA ONLY' if manifest['sample_data'] else 'Saved owner records'}

This is a planning estimate and supporting evidence, not a completed tax return.
Open report.html for a readable summary. Read report.json for exact calculations,
blockers, received-income issues, attention
items and reviewed rules. Unknown amounts are null in JSON and blank in CSV.
Money columns ending _minor are integer minor units; do not sum currencies.
Decimal calculation values are strings to preserve precision.

Forecasts, work, invoices and bank receipts are separate evidence. Do not add
their totals together: that would count the same income more than once.
Invoice allocations and sending history are records, not additional receipts.
Sending history is manually logged and does not prove delivery.

Transactions include all statuses in the UK tax-year date window, saved expenses
whose recorded date is in that window, and cross-year invoice payment evidence.
Use in_tax_year, status, confirmation, review and current flags. Presence in a
CSV does not mean inclusion in a tax calculation. Tracking-only expenses and
stale allocations remain visible. Work may include future planned shifts.
Invoices are selected by issue date, with their complete line/payment/history
records, including cancelled invoices and removed allocations.

CSV text that could run as a spreadsheet formula has an apostrophe prefix.
Original text is preserved in report.json. Bank raw payloads and authentication
records are excluded. This package contains private financial and client data.
"""
