"""Saved evidence comparison; invoice allocations never create income."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy.orm import lazyload

from models import IncomeStream, IncomeShift, Transaction, Invoice
from services.database.connection import db
from services.invoices.workflow import invoice_query, payment_current, payment_key, receipt_valid, settlement, shift_source_version
from services.tax.rules import reviewed_rules
from services.transactions.classification import classification_details
from services.tax.payment_evidence import compact_payments

LIMIT = 50
CANDIDATE_LIMIT = 5


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
    shift_query = db.select(IncomeShift).where(IncomeShift.starts_at >= first, IncomeShift.starts_at < end,
        IncomeShift.ends_at <= now, IncomeShift.income_stream_id.in_(selected)).order_by(IncomeShift.starts_at, IncomeShift.id)
    shift_ids = set(session.scalars(shift_query.with_only_columns(IncomeShift.id)))
    sections = {name: [] for name in ('changed_work', 'unbilled', 'receipts', 'review', 'invoices')}
    counts = dict.fromkeys(sections, 0)

    def display(name, row):
        counts[name] += 1
        if len(sections[name]) < LIMIT:
            sections[name].append(row)

    allocations = {}
    shift_links = {}
    candidates = defaultdict(list)
    candidate_counts = defaultdict(int)
    invoices = session.scalars(invoice_query().options(lazyload(Invoice.deliveries)).order_by(
        Invoice.issue_date.desc(), Invoice.id).execution_options(yield_per=200))
    # All years participate, but retain only numeric budgets and compact links.
    for invoice in invoices:
        payment_states = {payment.id: payment_current(payment, invoice) for payment in invoice.payments if payment.active}
        summary = settlement(invoice, payment_states=payment_states)
        compact = SimpleNamespace(**invoice.as_dict())
        key = (invoice.income_stream_id, invoice.currency)
        if invoice.state == 'issued' and not summary['stale'] and summary['outstanding'] > 0:
            candidate_counts[key] += 1
            if len(candidates[key]) < CANDIDATE_LIMIT:
                candidates[key].append(compact)
        for payment in invoice.payments:
            if payment.active:
                budget = allocations.setdefault(payment_key(payment.transaction), dict(cash=0, tax=0, ni=0, stale=False))
                budget['cash'] += payment.cash_minor
                budget['tax'] += payment.tax_minor
                budget['ni'] += payment.ni_minor
                budget['stale'] = budget['stale'] or not payment_states[payment.id]
        for item in invoice.items:
            if item.active:
                shift_links[item.shift_id] = (compact, item.source_version)
        linked_year = any(item.active and item.shift_id in shift_ids for item in invoice.items)
        if invoice.income_stream_id in selected and invoice.state != 'cancelled' and (first.date() <= invoice.issue_date < end.date() or linked_year):
            if invoice.state == 'draft' or summary['outstanding'] or summary['stale']:
                display('invoices', dict(invoice=compact, summary=summary))
    summaries = {}

    def group(stream, currency):
        key = (stream.id, currency)
        return summaries.setdefault(key, dict(stream=stream, currency=currency, work_minor=0, received_minor=0,
            unbilled_minor=0, unbilled_count=0, review_count=0, unallocated_minor=0, discrepancy_minor=None))

    unassigned_count = 0
    for shift in session.scalars(shift_query.execution_options(yield_per=500)):
        row = group(selected[shift.income_stream_id], shift.currency)
        row['work_minor'] += shift.gross_minor
        link = shift_links.get(shift.id)
        if link and shift_source_version(shift) != link[1]:
            display('changed_work', dict(shift=shift, invoice=link[0]))
        if not link:
            row['unbilled_minor'] += shift.gross_minor
            row['unbilled_count'] += 1
            display('unbilled', shift)
    receipts = compact_payments(session, db.and_(Transaction.direction == 'IN', Transaction.status == 'SETTLED',
        Transaction.transaction_time >= first, Transaction.transaction_time < end, Transaction.transaction_time <= now))
    for transaction in receipts:
        if classification_details(transaction)['type'] != 'income':
            continue
        income = transaction.income
        if stream_id and income and income.income_stream_id and income.income_stream_id != stream_id:
            continue
        if income and income.tax_treatment == 'non_taxable':
            if income.needs_review or income.recorded_currency != transaction.currency or transaction.confirmed_at is None:
                display('review', dict(transaction=transaction, reason='Review and confirm this non-taxable receipt.'))
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
        budget = allocations.get(payment_key(transaction), dict(cash=0, tax=0, ni=0, stale=False))
        stale = budget['stale']
        if reason:
            display('review', dict(transaction=transaction, reason=reason))
            if row:
                row['review_count'] += 1
            else:
                unassigned_count += 1
            continue
        row['received_minor'] += income.gross_minor
        remaining = dict(cash=transaction.amount_minor - budget['cash'],
            tax=(income.tax_deducted_minor or 0) - budget['tax'],
            ni=(income.ni_deducted_minor or 0) - budget['ni'])
        if stale or any(value < 0 for value in remaining.values()):
            row['review_count'] += 1
            display('review', dict(transaction=transaction, reason='Review and remove stale or excessive invoice allocations.'))
        elif sum(remaining.values()) > 0:
            available = sum(remaining.values())
            row['unallocated_minor'] += available
            display('receipts', dict(transaction=transaction, stream=stream, remaining=remaining,
                available_minor=available, invoices=candidates[(stream.id, transaction.currency)],
                candidate_count=candidate_counts[(stream.id, transaction.currency)]))
    for row in summaries.values():
        if row['stream'].income_mode == 'shifts' and not row['review_count'] and not unassigned_count:
            row['discrepancy_minor'] = row['work_minor'] - row['received_minor']
    return dict(tax_year=rules['tax_year'], streams=streams, selected_stream=stream_id,
                summaries=list(summaries.values()), counts=counts, **sections, limit=LIMIT,
                candidate_limit=CANDIDATE_LIMIT)
