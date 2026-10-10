"""Incoming income notifies until acknowledged; bank corrections reopen notices."""
from datetime import datetime, timezone

from sqlalchemy.orm import load_only, selectinload

from models import IncomeNoticeRead, Transaction
from services.database.connection import db
from services.transactions.review import payment_version, record_version
from services.tax.readiness import check_income, payment_link
from services.tax.reserves import earned_reserve
from services.tax.rules import reviewed_rules
from zoneinfo import ZoneInfo
from services.transactions.classification import classification_details


def notice_version(payment):
    # Confirmation changes the available advice even when the bank data is unchanged.
    return record_version(dict(payment=payment_version(payment), confirmed_at=payment.confirmed_at))


def income_receipts():
    return db.session.scalars(db.select(Transaction).where(
        Transaction.direction == 'IN', Transaction.status == 'SETTLED',
        Transaction.transaction_time <= datetime.now(timezone.utc)).options(
        load_only(Transaction.amount_minor, Transaction.currency, Transaction.direction, Transaction.status,
                  Transaction.transaction_time, Transaction.confirmed_at, Transaction.counterparty_name,
                  Transaction.reference, Transaction.source_updated_at, Transaction.settlement_time,
                  Transaction.source, Transaction.spending_category, Transaction.source_amount_minor,
                  Transaction.source_currency),
        selectinload(Transaction.classification), selectinload(Transaction.income)).order_by(
        Transaction.transaction_time, Transaction.account_uid, Transaction.category_uid, Transaction.feed_item_uid)).all()


def notices(estimate, format_amount):
    reads = {(row.account_uid, row.category_uid, row.feed_item_uid): row.payment_version
             for row in db.session.scalars(db.select(IncomeNoticeRead))}
    streams = {row['stream'].id: row['stream'] for row in estimate['rows']}
    ready = not estimate['blockers'] and not estimate['received_issues'] and estimate['received_reserve_minor'] is not None
    rules = reviewed_rules()
    first, last = rules['starts_on'], rules['ends_on']
    gross, credits, previous = 0, 0, (0, 0)
    result = []
    for payment in income_receipts():
        income = payment.income
        if classification_details(payment)['type'] != 'income':
            continue
        stream = streams.get(income.income_stream_id) if income else None
        supported_date = first <= payment.transaction_time.astimezone(ZoneInfo('Europe/London')).date().isoformat() <= last
        complete = check_income(payment, {}) and payment.confirmed_at is not None and supported_date
        amounts = None
        if complete and income.tax_treatment == 'non_taxable':
            amounts = (0, 0)
        elif ready and stream and complete:
            if stream.kind == 'employed':
                amounts = (0, 0)
            else:
                gross += income.gross_minor
                credits += income.tax_deducted_minor or 0
                current = earned_reserve(estimate, gross, credits)
                amounts = (current[0] - previous[0], current[1] - previous[1])
                previous = current
        key = (payment.account_uid, payment.category_uid, payment.feed_item_uid)
        current_version = notice_version(payment)
        if reads.get(key) == current_version:
            continue
        if income is None or income.income_stream_id is None:
            message = 'Tax / NI set-aside needs review. Assign this incoming payment to a stream and confirm its income details.'
        elif amounts is None:
            message = 'Tax / NI set-aside needs review. Confirm income details and resolve tax estimate warnings.'
        elif stream and stream.kind == 'employed':
            message = 'No business tax / NI reserve for this employment payment. Only logged payroll deductions are used.'
        else:
            message = (f'Tax reserve change {format_amount(amounts[0], "GBP")} · '
                       f'NI reserve change {format_amount(amounts[1], "GBP")}. '
                       f'Set aside {format_amount(max(0, sum(amounts)), "GBP")} for this payment.')
        result.append(dict(payment=':'.join(key), version=current_version,
            source=stream.name if stream else (income.source_name if income else None) or payment.counterparty_name or 'Unassigned income',
            amount=format_amount(payment.amount_minor, payment.currency), message=message,
            date=payment.transaction_time.astimezone(ZoneInfo('Europe/London')).strftime('%d %b %Y, %H:%M'),
            payer=payment.counterparty_name or 'Unnamed payer',
            edit_url=payment_link(payment, 'dashboard.edit_income'),
            tax_minor=amounts[0] if amounts else None, ni_minor=amounts[1] if amounts else None))
    return result[::-1]
