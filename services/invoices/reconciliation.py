"""Saved evidence comparison; invoice allocations never create income."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy.orm import load_only, selectinload

from models import IncomeStream, IncomeShift, Transaction
from services.database.connection import db
from services.invoices.workflow import invoice_query, payment_current, payment_key, receipt_valid, settlement, shift_source_version
from services.tax.rules import reviewed_rules
from services.transactions.classification import classification_details
from services.transactions.review import REVIEW_FIELDS

LIMIT = 50


def invoice_work_changes(invoices, shifts):
    """Compare current work with active invoice snapshots without changing either."""
    links = {item.shift_id: (invoice, item) for invoice in invoices
             for item in invoice.items if item.active and item.shift_id}
    return [dict(shift=shift, invoice=links[shift.id][0]) for shift in shifts
            if shift.id in links and shift_source_version(shift) != links[shift.id][1].source_version]


def reconcile(session, *, stream_id=None, as_of=None):
    rules = reviewed_rules()
    now = as_of or datetime.now(timezone.utc)
    first = datetime.fromisoformat(rules['starts_on']).replace(tzinfo=ZoneInfo('Europe/London'))
    end = (datetime.fromisoformat(rules['ends_on']) + timedelta(days=1)).replace(tzinfo=ZoneInfo('Europe/London'))
    streams = session.scalars(db.select(IncomeStream).where(IncomeStream.forecast_tax_year == rules['tax_year']).order_by(IncomeStream.name, IncomeStream.id)).all()
    selected = {obj.id: obj for obj in streams if stream_id is None or obj.id == stream_id}
    shifts = session.scalars(db.select(IncomeShift).where(IncomeShift.starts_at >= first, IncomeShift.starts_at < end,
        IncomeShift.ends_at <= now, IncomeShift.income_stream_id.in_(selected)).order_by(IncomeShift.starts_at, IncomeShift.id)).all()
    receipts = session.scalars(db.select(Transaction).where(Transaction.direction == 'IN', Transaction.status == 'SETTLED',
        Transaction.transaction_time >= first, Transaction.transaction_time < end, Transaction.transaction_time <= now).options(
        load_only(*(getattr(Transaction, key) for key in REVIEW_FIELDS), Transaction.confirmed_at),
        selectinload(Transaction.income), selectinload(Transaction.classification)).order_by(Transaction.transaction_time, Transaction.feed_item_uid)).all()
    invoices = session.scalars(invoice_query().order_by(db.desc('issue_date'))).all()
    allocations = defaultdict(list)
    shift_links = {}
    invoice_rows = []
    shift_ids = {shift.id for shift in shifts}
    invoice_summaries = {invoice.id: settlement(invoice) for invoice in invoices}
    candidates = defaultdict(list)
    for invoice in invoices:
        summary = invoice_summaries[invoice.id]
        if invoice.state == 'issued' and not summary['stale'] and summary['outstanding'] > 0:
            candidates[(invoice.income_stream_id, invoice.currency)].append(invoice)
    # All years participate in receipt budgets and work reservations. A receipt
    # already used on an older invoice must never be offered as wholly unused.
    for invoice in invoices:
        for payment in invoice.payments:
            if payment.active:
                allocations[payment_key(payment.transaction)].append((invoice, payment))
        for item in invoice.items:
            if item.active:
                shift_links[item.shift_id] = (invoice, item)
        linked_year = any(item.active and item.shift_id in shift_ids for item in invoice.items)
        if invoice.income_stream_id in selected and invoice.state != 'cancelled' and (first.date() <= invoice.issue_date < end.date() or linked_year):
            summary = invoice_summaries[invoice.id]
            if invoice.state == 'draft' or summary['outstanding'] or summary['stale']:
                invoice_rows.append(dict(invoice=invoice, summary=summary))
    summaries = {}

    def group(stream, currency):
        key = (stream.id, currency)
        return summaries.setdefault(key, dict(stream=stream, currency=currency, work_minor=0, received_minor=0,
            unbilled_minor=0, unbilled_count=0, review_count=0, unallocated_minor=0, discrepancy_minor=None))

    unbilled, receipt_rows, review_rows = [], [], []
    changed_work = invoice_work_changes(invoices, shifts)
    unassigned_count = 0
    for shift in shifts:
        row = group(selected[shift.income_stream_id], shift.currency)
        row['work_minor'] += shift.gross_minor
        if shift.id not in shift_links:
            row['unbilled_minor'] += shift.gross_minor
            row['unbilled_count'] += 1
            unbilled.append(shift)
    for transaction in receipts:
        if classification_details(transaction)['type'] != 'income':
            continue
        income = transaction.income
        if stream_id and income and income.income_stream_id and income.income_stream_id != stream_id:
            continue
        if income and income.tax_treatment == 'non_taxable':
            if income.needs_review or income.recorded_currency != transaction.currency or transaction.confirmed_at is None:
                review_rows.append(dict(transaction=transaction, reason='Review and confirm this non-taxable receipt.'))
                unassigned_count += 1
            continue
        stream = selected.get(income.income_stream_id) if income else None
        row = group(stream, transaction.currency) if stream else None
        probe = SimpleNamespace(currency=transaction.currency, income_stream_id=stream.id if stream else None)
        reason = None
        if stream is None:
            reason = 'Assign this income to a stream in the supported tax year.'
        elif not receipt_valid(transaction, probe):
            reason = 'Complete income details and confirm the saved payment.'
        elif income.gross_minor - (income.tax_deducted_minor or 0) - (income.ni_deducted_minor or 0) + income.adjustment_minor != transaction.amount_minor:
            reason = 'Gross pay, withholding and adjustments do not match the bank receipt.'
        entries = allocations[payment_key(transaction)]
        stale = any(not payment_current(payment, invoice) for invoice, payment in entries)
        if reason:
            review_rows.append(dict(transaction=transaction, reason=reason))
            if row:
                row['review_count'] += 1
            else:
                unassigned_count += 1
            continue
        row['received_minor'] += income.gross_minor
        remaining = dict(cash=transaction.amount_minor - sum(payment.cash_minor for _, payment in entries),
            tax=(income.tax_deducted_minor or 0) - sum(payment.tax_minor for _, payment in entries),
            ni=(income.ni_deducted_minor or 0) - sum(payment.ni_minor for _, payment in entries))
        if stale or any(value < 0 for value in remaining.values()):
            row['review_count'] += 1
            review_rows.append(dict(transaction=transaction, reason='Review and remove stale or excessive invoice allocations.'))
        elif sum(remaining.values()) > 0:
            available = sum(remaining.values())
            row['unallocated_minor'] += available
            receipt_rows.append(dict(transaction=transaction, stream=stream, remaining=remaining,
                available_minor=available, invoices=candidates[(stream.id, transaction.currency)][:LIMIT]))
    for row in summaries.values():
        if row['stream'].income_mode == 'shifts' and not row['review_count'] and not unassigned_count:
            row['discrepancy_minor'] = row['work_minor'] - row['received_minor']
    sections = dict(changed_work=changed_work, unbilled=unbilled, receipts=receipt_rows, review=review_rows, invoices=invoice_rows)
    return dict(tax_year=rules['tax_year'], streams=streams, selected_stream=stream_id,
                summaries=list(summaries.values()), counts={key: len(value) for key, value in sections.items()},
                **{key: value[:LIMIT] for key, value in sections.items()}, limit=LIMIT)
