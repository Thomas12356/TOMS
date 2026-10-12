"""One receipt, explained candidate invoices and read-only allocation previews."""
from sqlalchemy.orm import load_only, selectinload
from werkzeug.exceptions import NotFound

from models import Transaction, IncomeStream, Invoice
from services.database.connection import db
from services.invoices import workflow as work
from services.transactions.review import REVIEW_FIELDS


def receipt(key, *, lock=False):
    query = db.select(Transaction).options(load_only(
        *(getattr(Transaction, field) for field in REVIEW_FIELDS), Transaction.confirmed_at),
        selectinload(Transaction.income), selectinload(Transaction.classification)).where(
            Transaction.account_uid == key[0], Transaction.category_uid == key[1], Transaction.feed_item_uid == key[2])
    if lock:
        query = query.with_for_update(of=Transaction).execution_options(populate_existing=True)
    payment = db.session.scalar(query)
    if payment is None:
        raise NotFound('Payment not found.')
    return payment


def reviewed_receipt(payment, invoice):
    income = payment.income
    return bool(work.receipt_valid(payment, invoice) and
        income.gross_minor - (income.tax_deducted_minor or 0) - (income.ni_deducted_minor or 0)
        + income.adjustment_minor == payment.amount_minor)


def choices(payment, search=None):
    """Bounded, explicit choices; same source/currency are criteria, not proof."""
    income = payment.income
    source = db.session.get(IncomeStream, income.income_stream_id) if income and income.income_stream_id else None
    if source is None:
        return dict(source=None, available=None, rows=[], eligible=False)
    probe = Invoice(income_stream_id=source.id, currency=payment.currency)
    if not reviewed_receipt(payment, probe):
        return dict(source=source, available=None, rows=[], eligible=False)
    available = work.remaining_receipt(payment)
    query = work.invoice_query().where(Invoice.income_stream_id == source.id,
        Invoice.currency == payment.currency, Invoice.state == 'issued')
    if search:
        query = query.where(db.or_(*(field.icontains(search, autoescape=True)
            for field in (Invoice.number, Invoice.client_name, Invoice.reference))))
    rows = []
    for invoice in db.session.scalars(query.order_by(Invoice.issue_date.desc(), Invoice.id).execution_options(yield_per=100)):
        summary = work.settlement(invoice)
        if summary['stale'] or not summary['outstanding']:
            continue
        reasons = ['Same income source and currency']
        reference = (payment.reference or '').strip().casefold()
        ref_match = bool(reference and reference in ((invoice.number or '').casefold(), (invoice.reference or '').strip().casefold()))
        amount_match = summary['outstanding'] == sum(available.values())
        if ref_match:
            reasons.append('Payment reference matches the invoice number or reference')
        if amount_match:
            reasons.append('Outstanding amount equals the remaining cash and recorded deductions')
        if invoice.issue_date <= payment.transaction_time.astimezone(work.LOCAL_TIME).date():
            reasons.append('Invoice was issued on or before this payment')
        rows.append(dict(invoice=invoice, summary=summary, reasons=reasons, rank=(not ref_match, not amount_match,
            -invoice.issue_date.toordinal(), invoice.id)))
        rows.sort(key=lambda row: row['rank'])
        del rows[20:]
    return dict(source=source, available=available, rows=rows, eligible=True)
