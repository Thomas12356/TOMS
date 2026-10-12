"""Portable evidence package. Forecasts and bank receipts remain separate."""
import csv
import io
import json
from contextlib import contextmanager, ExitStack
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zipfile import ZipFile, ZIP_DEFLATED
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy.orm import Session, joinedload, lazyload, load_only

from models import IncomeStream, IncomeShift, Transaction, TransactionIncome, ExpenseDeduction, MileageEntry, Invoice, InvoiceItem, InvoicePayment, InvoiceDelivery
from services.database.connection import db
from services.database.session import test_data_active
from services.invoices.workflow import payment_current
from services.tax.records import expense_current
from services.tax.reserves import current_plan
from services.tax.rules import reviewed_rules
from services.tax.national_insurance import reviewed_rules as ni_rules
from services.tax.mileage_rules import reviewed_rules as mileage_rules
from services.transactions.classification import classification_details
from services.transactions.review import REVIEW_FIELDS
from tempfile import TemporaryFile
from models import Account, AccountHistory, StatementImport, StatementImportRow


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
        safe = safe_csv_row(row)
        writer.writerow(safe)
    return output.getvalue().encode('utf-8-sig')


FIELDS = {
    'accounts': 'account_uid name bank_name provider currency opened_at cash_active closed_on',
    'account_history': 'account_uid tax_year from_on through_on complete revision confirmed_at',
    'statement_imports': 'id account_uid status row_count imported_count skipped_count created_at imported_at',
    'statement_import_rows': 'batch_id row_number account_uid category_uid feed_item_uid decision',
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


def safe_csv_row(row):
    safe = {}
    for key, value in row.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, default=scalar, ensure_ascii=False)
        if isinstance(value, str) and (value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n'))):
            value = "'" + value
        safe[key] = value
    return safe


def evidence_rows(session, name, first, end, now):
    """Stream each allowlisted table with complete cross-year receipt selection."""
    selected = db.and_(Invoice.issue_date >= first.date(), Invoice.issue_date < end.date())
    linked = db.select(InvoicePayment.id).join(Invoice).where(selected,
        InvoicePayment.account_uid == Transaction.account_uid,
        InvoicePayment.category_uid == Transaction.category_uid,
        InvoicePayment.feed_item_uid == Transaction.feed_item_uid).exists()
    payments = db.or_(db.and_(Transaction.transaction_time >= first, Transaction.transaction_time < end),
        Transaction.expense.has(db.and_(ExpenseDeduction.recorded_time >= first, ExpenseDeduction.recorded_time < end)), linked)
    quiet_invoice = (lazyload(Invoice.items), lazyload(Invoice.payments), lazyload(Invoice.deliveries))
    payment_columns = tuple(dict.fromkeys((*FIELDS['transactions'].split(), *REVIEW_FIELDS)))
    if name in ('accounts','account_history','statement_imports','statement_import_rows'):
        model=dict(accounts=Account,account_history=AccountHistory,statement_imports=StatementImport,
            statement_import_rows=StatementImportRow)[name]
        query=db.select(model).options(load_only(*(getattr(model,field) for field in FIELDS[name].split())))
        if name=='statement_imports':
            query=query.where(StatementImport.status=='imported')
        elif name=='statement_import_rows':
            query=query.join(StatementImport,db.and_(StatementImport.id==StatementImportRow.batch_id,
                StatementImport.account_uid==StatementImportRow.account_uid)).where(StatementImport.status=='imported')
        query=query.order_by(*model.__table__.primary_key.columns)
    elif name == 'streams':
        query = db.select(IncomeStream).where(IncomeStream.forecast_tax_year == reviewed_rules()['tax_year']).order_by(IncomeStream.id)
    elif name == 'work':
        query = db.select(IncomeShift).where(IncomeShift.starts_at >= first, IncomeShift.starts_at < end).order_by(IncomeShift.starts_at, IncomeShift.id)
    elif name == 'mileage':
        query = db.select(MileageEntry).where(MileageEntry.journey_date >= first.date(), MileageEntry.journey_date < end.date()).order_by(MileageEntry.journey_date, MileageEntry.id)
    elif name == 'invoices':
        query = db.select(Invoice).options(*quiet_invoice).where(selected).order_by(Invoice.issue_date, Invoice.id)
    elif name in ('invoice_items', 'sending_history', 'allocations'):
        model = dict(invoice_items=InvoiceItem, sending_history=InvoiceDelivery, allocations=InvoicePayment)[name]
        query = db.select(model).join(Invoice).where(selected).order_by(Invoice.issue_date, Invoice.id)
        if name == 'sending_history':
            query = query.order_by(model.sent_at, model.id)
        else:
            query = query.order_by(model.id)
        if name == 'allocations':
            query = query.options(joinedload(InvoicePayment.invoice).options(*quiet_invoice),
                joinedload(InvoicePayment.transaction).options(
                    load_only(*(getattr(Transaction, field) for field in payment_columns), Transaction.confirmed_at),
                    joinedload(Transaction.income), joinedload(Transaction.classification)))
    elif name == 'transactions':
        query = db.select(Transaction).where(payments).options(
            load_only(*(getattr(Transaction, field) for field in payment_columns), Transaction.confirmed_at),
            joinedload(Transaction.classification)).order_by(Transaction.transaction_time, Transaction.feed_item_uid,
                Transaction.account_uid, Transaction.category_uid)
    elif name == 'income':
        query = db.select(TransactionIncome).join(Transaction).where(payments).order_by(Transaction.transaction_time,
            Transaction.feed_item_uid, Transaction.account_uid, Transaction.category_uid)
    elif name == 'expenses':
        query = db.select(ExpenseDeduction).join(Transaction).where(payments).options(
            joinedload(ExpenseDeduction.transaction).options(
                load_only(*(getattr(Transaction, field) for field in payment_columns), Transaction.confirmed_at),
                joinedload(Transaction.classification))).order_by(Transaction.transaction_time,
                    Transaction.feed_item_uid, Transaction.account_uid, Transaction.category_uid)
    else:
        raise ValueError('Unknown evidence table.')
    for obj in session.scalars(query.execution_options(yield_per=500)):
        row = record(obj, FIELDS[name])
        if name == 'transactions':
            row.update(classification=classification_details(obj)['type'],
                       in_tax_year=first <= obj.transaction_time < end,
                       settled_and_not_future=obj.status == 'SETTLED' and obj.transaction_time <= now)
        elif name == 'expenses':
            row['current'] = expense_current(obj)
        elif name == 'allocations':
            row['current'] = payment_current(obj, obj.invoice)
        yield row


def package(session, *, include_records=True):
    """Build an anonymous private file; browser downloads retain only metadata.

    include_records supports small internal evidence inspections. Production
    downloads disable it and stream table records through temporary files.
    The caller owns/ closes the returned file; every failure closes it here.
    """
    rules = reviewed_rules()
    first = datetime.fromisoformat(rules['starts_on']).replace(tzinfo=ZoneInfo('Europe/London'))
    end = (datetime.fromisoformat(rules['ends_on']) + timedelta(days=1)).replace(tzinfo=ZoneInfo('Europe/London'))
    now = datetime.now(timezone.utc)
    estimate = current_plan(refresh_rules=False, session=session, as_of=now)
    calculation = {key: value for key, value in estimate.items() if key != 'rows'}
    calculation['rows'] = [{**{key: value for key, value in row.items() if key != 'stream'},
                             'income_stream_id': row['stream'].id} for row in estimate['rows']]
    manifest = {'format_version': 1, 'tax_year': rules['tax_year'], 'generated_at': now.isoformat(),
                'sample_data': test_data_active(), 'currency': 'GBP', 'money_unit': 'minor units (pence for GBP)',
                'counts': {}, 'calculation': calculation,
                'income_tax_rules': rules, 'national_insurance_rules': ni_rules(), 'mileage_rules': mileage_rules()}
    if include_records:
        manifest['records'] = {name: [] for name in FIELDS}
    output = io.BytesIO() if include_records else TemporaryFile(mode='w+b')
    try:
        with ExitStack() as files:
            tables = {}
            streams = []
            for name, field_names in FIELDS.items():
                json_rows = files.enter_context(TemporaryFile(mode='w+b'))
                csv_file = files.enter_context(TemporaryFile(mode='w+', encoding='utf-8-sig', newline=''))
                extras = {'transactions': ['classification', 'in_tax_year', 'settled_and_not_future'],
                          'expenses': ['current'], 'allocations': ['current']}
                writer = csv.DictWriter(csv_file, fieldnames=field_names.split() + extras.get(name, []))
                writer.writeheader()
                count = 0
                for row in evidence_rows(session, name, first, end, now):
                    json_rows.write(json.dumps(row, default=scalar, ensure_ascii=False).encode('utf-8') + b'\n')
                    writer.writerow(safe_csv_row(row))
                    count += 1
                    if include_records:
                        manifest['records'][name].append(row)
                    if name == 'streams':
                        streams.append(row)
                manifest['counts'][name] = count
                json_rows.seek(0)
                csv_file.flush()
                csv_file.seek(0)
                tables[name] = (json_rows, csv_file)
            with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
                metadata = {key: value for key, value in manifest.items() if key != 'records'}
                with archive.open('report.json', 'w') as report:
                    report.write(json.dumps(metadata, default=scalar, ensure_ascii=False).encode('utf-8')[:-1])
                    report.write(b', "records": {')
                    for index, (name, (json_rows, _)) in enumerate(tables.items()):
                        if index:
                            report.write(b',')
                        report.write(json.dumps(name).encode() + b':[')
                        for row_index, line in enumerate(json_rows):
                            if row_index:
                                report.write(b',')
                            report.write(line.rstrip(b'\n'))
                        report.write(b']')
                    report.write(b'}}')
                archive.writestr('README.txt', instructions(manifest))
                html_manifest = dict(manifest, records={'streams': streams})
                template = current_app.jinja_env.get_template('tax_export_report.html')
                with archive.open('report.html', 'w') as report:
                    for fragment in template.generate(manifest=html_manifest, money=money):
                        report.write(fragment.encode('utf-8'))
                for name, (_, csv_file) in tables.items():
                    with archive.open(name + '.csv', 'w') as destination:
                        # Copy encoded text in bounded chunks; preserve the UTF-8 BOM.
                        destination.write(b'\xef\xbb\xbf')
                        while chunk := csv_file.read(65536):
                            destination.write(chunk.encode('utf-8'))
        output.seek(0)
        return output, manifest
    except BaseException:
        output.close()
        raise


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
