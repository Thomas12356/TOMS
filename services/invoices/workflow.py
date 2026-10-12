"""Drafts, issued snapshots and actual payment allocations. Caller commits."""
from datetime import date, datetime, timezone
import re
from uuid import uuid4

from sqlalchemy.orm import load_only, selectinload
from werkzeug.exceptions import Conflict, NotFound

from models import IncomeShift, IncomeStream, Invoice, InvoiceItem, InvoicePayment, InvoiceSequence, InvoiceDelivery, Transaction, TransactionIncome
from services.database.connection import db
from services.database.session import test_data_active
from services.transactions.classification import classification_details
from services.transactions.review import REVIEW_FIELDS, record_snapshot as snapshot, record_version as version, payment_version
from services.transactions.shifts import LOCAL_TIME, shift_label, local_time
from services.validation import optional_text, uid

MAX_MINOR = 2**63 - 1
BUSINESS_FIELDS = {'business_name': 200, 'business_address': 2000,
                   'business_contact': 500, 'payment_instructions': 2000}
DELIVERY_METHODS = {'email': 'Email', 'post': 'Post', 'hand': 'Hand delivered', 'other': 'Other'}


def shift_source_version(shift):
    return version(shift.as_dict())


def today():
    return datetime.now(LOCAL_TIME).date()


def invoice_query():
    return db.select(Invoice).options(selectinload(Invoice.items),
        selectinload(Invoice.payments).selectinload(InvoicePayment.transaction).load_only(
            *(getattr(Transaction, field) for field in REVIEW_FIELDS), Transaction.confirmed_at),
        selectinload(Invoice.payments).selectinload(InvoicePayment.transaction).selectinload(Transaction.income),
        selectinload(Invoice.payments).selectinload(InvoicePayment.transaction).selectinload(Transaction.classification))


def lock_writes():
    # Same lock as shift/stream writes; independent lock for sample records.
    db.session.execute(db.select(db.func.pg_advisory_xact_lock(6075157141257144400 + int(test_data_active()))))


def get_invoice(identity, revision=None):
    query = invoice_query().where(Invoice.id == str(identity))
    if revision is not None:
        lock_writes()
        query = query.with_for_update(of=Invoice).execution_options(populate_existing=True)
    invoice = db.session.scalar(query)
    if invoice is None:
        raise NotFound('Invoice not found.')
    if revision is not None and str(invoice.revision) != revision:
        raise Conflict('This invoice changed. Reload it before saving.')
    return invoice


def available_shifts(stream_id, invoice_id=None):
    reserved = db.select(InvoiceItem.shift_id).where(InvoiceItem.active.is_(True), InvoiceItem.shift_id.is_not(None))
    if invoice_id:
        reserved = reserved.where(InvoiceItem.invoice_id != invoice_id)
    return db.session.scalars(db.select(IncomeShift).where(IncomeShift.income_stream_id == stream_id,
        IncomeShift.ends_at <= datetime.now(timezone.utc), IncomeShift.id.not_in(reserved))
        .order_by(IncomeShift.starts_at, IncomeShift.id)).all()


def draft_fields(form):
    name = optional_text(form.get('client_name'), field='client name', maximum=200)
    if not name:
        raise ValueError('Enter the client name.')
    values = dict(client_name=name,
        client_address=optional_text(form.get('client_address'), field='client address', maximum=2000) or '',
        reference=optional_text(form.get('reference'), field='invoice reference', maximum=200) or '')
    values.update({field: optional_text(form.get(field), field=field.replace('_', ' '), maximum=limit) or ''
                   for field, limit in BUSINESS_FIELDS.items()})
    for field in ('issue_date', 'due_date'):
        value = form.get(field, '')
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError('Enter valid invoice and due dates.')
        try:
            values[field] = date.fromisoformat(value)
        except ValueError:
            raise ValueError('Enter valid invoice and due dates.') from None
    if values['due_date'] < values['issue_date']:
        raise ValueError('The due date must be on or after the invoice date.')
    return values


def save_draft(identity, stream_id, form, shift_ids, revision=None):
    lock_writes()
    stream = db.session.get(IncomeStream, uid(stream_id))
    if stream is None or stream.archived:
        raise ValueError('Choose an active income stream.')
    identity = uid(identity)
    if revision is None:
        if db.session.get(Invoice, identity):
            raise Conflict('This draft was already created. Open it from Invoices.')
        invoice = Invoice(id=identity, income_stream_id=stream.id, state='draft', revision=1)
    else:
        invoice = get_invoice(identity, revision)
        if invoice.state != 'draft' or invoice.income_stream_id != stream.id:
            raise ValueError('Only draft invoices can be edited; their income stream stays fixed.')
    values = draft_fields(form)
    if not 1 <= len(shift_ids) <= 100 or len(set(shift_ids)) != len(shift_ids):
        raise ValueError('Choose between 1 and 100 different completed shifts.')
    identities = [uid(value) for value in shift_ids]
    choices = {shift.id: shift for shift in available_shifts(stream.id, identity if revision else None)}
    if any(value not in choices for value in identities):
        raise Conflict('A selected shift is unavailable, unfinished or already invoiced. Reload the list.')
    selected = [choices[value] for value in identities]
    currencies = {shift.currency for shift in selected}
    if stream.expected_gross_currency and currencies != {stream.expected_gross_currency}:
        raise ValueError('Selected shifts must use the income stream’s currency.')
    if len(currencies) != 1:
        raise ValueError('All invoice shifts must use the same currency.')
    total = sum(shift.gross_minor for shift in selected)
    if not 0 < total <= MAX_MINOR:
        raise ValueError('The invoice total exceeds the supported amount.')
    for field, value in values.items():
        setattr(invoice, field, value)
    invoice.currency, invoice.total_minor, invoice.stream_name = selected[0].currency, total, stream.name
    if revision is None:
        db.session.add(invoice)
        db.session.flush()
    else:
        # A draft has no payments; replacing its editable items releases old reservations.
        for item in invoice.items:
            db.session.delete(item)
        db.session.flush()
        invoice.revision += 1
    for shift in selected:
        saved_snapshot = snapshot(shift.as_dict())
        db.session.add(InvoiceItem(id=str(uuid4()), invoice_id=identity, shift_id=shift.id, active=True,
            description=shift_label(shift) + (f' · {shift.notes}' if shift.notes else ''),
            amount_minor=shift.gross_minor, source_version=shift_source_version(shift), shift_snapshot=saved_snapshot))
    db.session.flush()
    db.session.expire(invoice, ['items'])
    return invoice


def issue(invoice):
    if invoice.state != 'draft':
        raise ValueError('Only a draft can be issued.')
    if not all(getattr(invoice, field).strip() for field in ('business_name', 'business_address', 'payment_instructions')):
        raise ValueError('Edit the draft and add your business name, address and payment instructions before issuing.')
    stream = db.session.get(IncomeStream, invoice.income_stream_id)
    if stream.archived:
        raise ValueError('Restore this income stream before issuing its draft.')
    if invoice.issue_date > today():
        raise ValueError('An issued invoice cannot have a future issue date.')
    if not invoice.items or sum(item.amount_minor for item in invoice.items) != invoice.total_minor:
        raise ValueError('Review and save this draft before issuing it.')
    for item in invoice.items:
        shift = db.session.get(IncomeShift, item.shift_id)
        if not shift or shift.income_stream_id != invoice.income_stream_id or shift.ends_at > datetime.now(timezone.utc) or shift_source_version(shift) != item.source_version:
            raise Conflict('A shift changed after this draft was saved. Edit and save the draft before issuing.')
    counter = db.session.get(InvoiceSequence, 1, with_for_update=True)
    if counter is None:
        counter = InvoiceSequence(id=1, last_number=0)
        db.session.add(counter)
    if counter.last_number >= MAX_MINOR:
        raise ValueError('Invoice numbering has reached its limit.')
    counter.last_number += 1
    invoice.number = ('TEST-INV-' if test_data_active() else 'INV-') + f'{counter.last_number:06d}'
    invoice.state, invoice.issued_at = 'issued', datetime.now(timezone.utc)
    invoice.revision += 1


def record_delivery(invoice, form):
    """Record an actual send separately from issue and payment settlement."""
    if invoice.state != 'issued':
        raise ValueError('Only an issued invoice can be recorded as sent.')
    method = form.get('method', '')
    if method not in DELIVERY_METHODS:
        raise ValueError('Choose how you sent the invoice.')
    try:
        sent_at = local_time(form.get('sent_at', ''))
    except (ValueError, OverflowError):
        raise ValueError('Enter an unambiguous, valid sending date and time in Europe/London.') from None
    if sent_at > datetime.now(timezone.utc) or sent_at.astimezone(LOCAL_TIME).date() < invoice.issue_date:
        raise ValueError('The sending date must be on or after the invoice date and cannot be in the future.')
    recipient = optional_text(form.get('recipient'), field='recipient', maximum=500)
    if not recipient:
        raise ValueError('Enter the recipient or delivery destination.')
    notes = optional_text(form.get('notes'), field='sending notes', maximum=1000) or ''
    row = InvoiceDelivery(id=str(uuid4()), invoice_id=invoice.id, sent_at=sent_at,
                          method=method, recipient=recipient, notes=notes)
    db.session.add(row)
    invoice.revision += 1
    return row


def cancel(invoice):
    if invoice.state == 'cancelled':
        raise ValueError('This invoice is already cancelled.')
    if any(payment.active for payment in invoice.payments):
        raise ValueError('Remove payment allocations before cancelling this invoice.')
    invoice.state, invoice.cancelled_at = 'cancelled', datetime.now(timezone.utc)
    invoice.revision += 1
    for item in invoice.items:
        item.active = False


def receipt_valid(transaction, invoice):
    if (transaction is None or transaction.direction != 'IN' or transaction.status != 'SETTLED'
            or transaction.confirmed_at is None or transaction.amount_minor <= 0
            or transaction.transaction_time > datetime.now(timezone.utc) or transaction.currency != invoice.currency
            or classification_details(transaction)['type'] != 'income'):
        return False
    income = transaction.income
    return bool(income and not income.needs_review and income.income_stream_id == invoice.income_stream_id
                and income.recorded_currency == invoice.currency and income.gross_minor is not None
                and income.tax_treatment in ('no_tax_deducted', 'cis', 'paye', 'other_deduction')
                and (income.tax_treatment == 'no_tax_deducted' or income.tax_deducted_minor is not None))


def payment_current(payment, invoice):
    return (receipt_valid(payment.transaction, invoice)
            and payment.payment_version == payment_version(payment.transaction))


def settlement(invoice, *, payment_states=None):
    active = [payment for payment in invoice.payments if payment.active]
    valid = [payment for payment in active if (payment_current(payment, invoice)
             if payment_states is None else payment_states[payment.id])]
    credited = sum(payment.cash_minor + payment.tax_minor + payment.ni_minor for payment in valid)
    outstanding = max(0, invoice.total_minor - credited)
    stale = len(valid) != len(active)
    if invoice.state == 'draft':
        label = 'Draft'
    elif invoice.state == 'cancelled':
        label = 'Cancelled'
    elif stale:
        label = 'Payment needs review'
    elif outstanding == 0:
        label = 'Paid'
    else:
        label = 'Part-paid' if credited else 'Unpaid'
    return dict(label=label, credited=credited, outstanding=outstanding, stale=stale,
                overdue=invoice.state == 'issued' and outstanding > 0 and invoice.due_date < today())


def payment_key(transaction):
    return (transaction.account_uid, transaction.category_uid, transaction.feed_item_uid)


def remaining_receipt(transaction):
    existing = db.session.scalars(db.select(InvoicePayment).where(InvoicePayment.active.is_(True),
        InvoicePayment.account_uid == transaction.account_uid, InvoicePayment.category_uid == transaction.category_uid,
        InvoicePayment.feed_item_uid == transaction.feed_item_uid)).all()
    current_version = payment_version(transaction)
    if any(payment.payment_version != current_version for payment in existing):
        raise Conflict('Review and remove stale allocations for this payment before allocating it again.')
    income = transaction.income
    return dict(cash=transaction.amount_minor - sum(payment.cash_minor for payment in existing),
                tax=(income.tax_deducted_minor or 0) - sum(payment.tax_minor for payment in existing),
                ni=(income.ni_deducted_minor or 0) - sum(payment.ni_minor for payment in existing))


def allocate(invoice, key, cash, tax, ni):
    if invoice.state != 'issued':
        raise ValueError('Issue the invoice before allocating a payment.')
    transaction = db.session.scalar(db.select(Transaction).options(load_only(
        *(getattr(Transaction, field) for field in REVIEW_FIELDS), Transaction.confirmed_at)).where(Transaction.account_uid == key[0],
        Transaction.category_uid == key[1], Transaction.feed_item_uid == key[2])
        .with_for_update(of=Transaction).execution_options(populate_existing=True))
    if not receipt_valid(transaction, invoice):
        raise ValueError('Choose confirmed, settled income in this currency assigned to this stream. Complete its income details first.')
    validate_allocation(invoice, transaction, cash, tax, ni)
    payment = InvoicePayment(id=str(uuid4()), invoice_id=invoice.id, account_uid=key[0], category_uid=key[1],
        feed_item_uid=key[2], cash_minor=cash, tax_minor=tax, ni_minor=ni, payment_version=payment_version(transaction),
        payment_label=((transaction.counterparty_name or 'Unnamed payment') + ' · ' + (transaction.reference or 'No reference'))[:300], active=True)
    db.session.add(payment)
    invoice.revision += 1
    return payment


def validate_allocation(invoice, transaction, cash, tax, ni):
    """Shared read-only preview validation; allocate additionally locks records."""
    if invoice.state != 'issued' or not receipt_valid(transaction, invoice):
        raise ValueError('Choose an issued invoice for this confirmed income source and currency.')
    amounts = (cash, tax, ni)
    if any(type(value) is not int or not 0 <= value <= MAX_MINOR for value in amounts) or not 0 < sum(amounts) <= MAX_MINOR:
        raise ValueError('Allocate a positive amount using actual cash and logged deductions.')
    available = remaining_receipt(transaction)
    if any(amount > available[field] for amount, field in zip(amounts, ('cash', 'tax', 'ni'))):
        raise ValueError('This allocation exceeds the payment’s remaining cash or logged deductions.')
    summary = settlement(invoice)
    if summary['stale']:
        raise Conflict('Remove this invoice’s stale payment allocations before adding another.')
    if sum(amounts) > summary['outstanding']:
        raise ValueError('This allocation exceeds the invoice’s outstanding balance.')
    return summary


def remove_payment(invoice, identity):
    payment = next((payment for payment in invoice.payments if payment.id == identity), None)
    if payment is None or not payment.active:
        raise ValueError('Active payment allocation not found on this invoice.')
    payment.active = False
    invoice.revision += 1


def receipt_choices(invoice, search=None):
    query = db.select(Transaction).options(load_only(*(getattr(Transaction, field) for field in REVIEW_FIELDS),
        Transaction.confirmed_at), selectinload(Transaction.income), selectinload(Transaction.classification))
    # Limit the picker; a transaction edit/search workflow can expose older records later.
    query = query.join(TransactionIncome).where(TransactionIncome.income_stream_id == invoice.income_stream_id)
    if search:
        query = query.where(db.or_(Transaction.counterparty_name.icontains(search, autoescape=True),
                                   Transaction.reference.icontains(search, autoescape=True)))
    query = query.where(Transaction.direction == 'IN', Transaction.status == 'SETTLED',
        Transaction.currency == invoice.currency, Transaction.confirmed_at.is_not(None))
    choices = []
    for transaction in db.session.scalars(query.order_by(Transaction.transaction_time.desc()).limit(200)):
        if receipt_valid(transaction, invoice):
            try:
                remaining = remaining_receipt(transaction)
            except Conflict:
                continue
            if sum(remaining.values()) > 0:
                choices.append(dict(transaction=transaction, key='|'.join(payment_key(transaction)), available=remaining))
    return choices
