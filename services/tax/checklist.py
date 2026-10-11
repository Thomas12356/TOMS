"""Next actions from saved records and the shared planning checks."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy.orm import load_only, selectinload

from models import Transaction, ExpenseDeduction
from services.database.connection import db
from services.tax.readiness import check_income, payment_link
from services.tax.records import expense_current
from services.tax.rules import reviewed_rules
from services.transactions.classification import classification_details

LIMIT = 20


def group(key, title, description, url, action):
    return dict(key=key, title=title, description=description, url=url, action=action, count=0, items=[])


def add(step, label, reason, url):
    step['count'] += 1
    if len(step['items']) < LIMIT:
        step['items'].append(dict(label=label, reason=reason, url=url))


def guided_checklist(estimate, *, as_of=None, session=None):
    session = session if session is not None else db.session
    rules = reviewed_rules()
    first = datetime.fromisoformat(rules['starts_on']).replace(tzinfo=ZoneInfo('Europe/London'))
    end = (datetime.fromisoformat(rules['ends_on']) + timedelta(days=1)).replace(tzinfo=ZoneInfo('Europe/London'))
    now = as_of or datetime.now(timezone.utc)
    forecasts = group('forecasts', 'Set up your income forecasts',
        'Include every job or business. Individual-shift forecasts include only work you have entered.',
        '/dashboard/income-streams', 'Manage income streams')
    income = group('income', 'Complete income and actual withholding',
        'Assign taxable receipts, enter gross pay and choose tax treatment. Record actual PAYE/CIS deductions from your evidence.',
        '/dashboard?account=all', 'Open saved payments')
    review = group('review', 'Review saved payments',
        'Confirm the details you have checked. Unconfirmed spending does not block the tax forecast; unconfirmed income can block the received-income target.',
        '/dashboard?account=all&attention=unconfirmed', 'Open transaction review')
    deductions = group('deductions', 'Review changed tax deductions',
        'Check claims whose linked bank payment changed. Tracking-only expenses do not reduce the tax estimate.',
        '/dashboard/deductions', 'Review deductions')
    planning = group('planning', 'Resolve remaining estimate warnings',
        'The shared tax estimate checks forecasts, credits, deductions and received income together. A warning may refer to a record listed above.',
        '/dashboard/tax-estimate', 'Open tax estimate')
    for row in estimate['rows']:
        stream = row['stream']
        reasons = []
        if row['gross_minor'] is None:
            reasons.append('Enter expected gross income.')
        elif stream.expected_gross_currency != rules['currency']:
            reasons.append('A reviewed GBP conversion is required.')
        elif row['deductions_minor'] > row['gross_minor']:
            reasons.append('Deductions exceed the forecast; review your forecast and claims.')
        if reasons:
            add(forecasts, stream.name, ' '.join(reasons),
                '/dashboard/income-streams?' + urlencode({'configure': stream.id}))
    if not estimate['rows']:
        add(forecasts, 'Create your first income stream', 'Add each job or business to start the estimate.',
            '/dashboard/income-streams?create=1')
    payments = session.scalars(db.select(Transaction).where(
        Transaction.transaction_time >= first, Transaction.transaction_time < end,
        Transaction.transaction_time <= now, Transaction.status == 'SETTLED').options(
        load_only(Transaction.amount_minor, Transaction.currency, Transaction.direction, Transaction.status,
                  Transaction.source, Transaction.transaction_time, Transaction.confirmed_at, Transaction.counterparty_name),
        selectinload(Transaction.income), selectinload(Transaction.classification)).order_by(
        Transaction.transaction_time.desc(), Transaction.account_uid, Transaction.category_uid, Transaction.feed_item_uid)).all()
    for payment in payments:
        label = payment.counterparty_name or 'Unnamed payment'
        label += ' · ' + payment.transaction_time.astimezone(ZoneInfo('Europe/London')).strftime('%d %b %Y')
        if payment.confirmed_at is None:
            add(review, label, 'Review the classification and details, then check and confirm this payment.',
                payment_link(payment, 'guided_review.payment'))
        if payment.direction != 'IN' or classification_details(payment)['type'] != 'income' or check_income(payment, {}):
            continue
        record = payment.income
        reasons = []
        if record is None:
            reasons.append('Enter income details, including its stream, gross pay and tax treatment.')
        else:
            if record.needs_review or record.recorded_currency != payment.currency:
                reasons.append('The payment changed; review and save its income details again.')
            if record.tax_treatment != 'non_taxable':
                if not record.income_stream_id:
                    reasons.append('Assign this payment to an income stream.')
                if record.gross_minor is None:
                    reasons.append('Enter gross pay before deductions.')
                if record.tax_treatment == 'unknown':
                    reasons.append('Confirm the tax treatment; withholding is not assumed.')
                elif record.tax_treatment not in ('no_tax_deducted', 'non_taxable') and record.tax_deducted_minor is None:
                    reasons.append('Enter the actual tax withheld from your payslip or contractor statement.')
                if payment.currency != 'GBP':
                    reasons.append('This receipt needs review because received-income planning supports GBP.')
        add(income, label, ' '.join(reasons), payment_link(payment, 'dashboard.edit_income'))
    claims = session.scalars(db.select(ExpenseDeduction).where(
        ExpenseDeduction.is_tax_deductible.is_(True),
        db.or_(db.and_(ExpenseDeduction.recorded_time >= first, ExpenseDeduction.recorded_time < end),
               ExpenseDeduction.transaction.has(db.and_(Transaction.transaction_time >= first, Transaction.transaction_time < end)))
    ).options(selectinload(ExpenseDeduction.transaction).selectinload(Transaction.classification)).order_by(
        ExpenseDeduction.recorded_time.desc(), ExpenseDeduction.account_uid,
        ExpenseDeduction.category_uid, ExpenseDeduction.feed_item_uid)).all()
    for claim in claims:
        if not expense_current(claim):
            key = ':'.join((claim.account_uid, claim.category_uid, claim.feed_item_uid))
            add(deductions, claim.purpose, 'This claim is excluded until its changed bank payment is reviewed.',
                '/dashboard/deductions?' + urlencode({'expense': key}))
    for warning in dict.fromkeys(estimate['blockers'] + estimate['received_issues']):
        add(planning, warning, 'Review the explanation and linked records in the tax estimate.', planning['url'])
    steps = [forecasts, income, review, deductions, planning]
    annual_ready = not estimate['blockers'] and estimate['calculation'] is not None
    received_ready = annual_ready and not estimate['received_issues'] and estimate['received_reserve_minor'] is not None
    return dict(steps=steps, next_step=next((step for step in steps if step['count']), None),
                clear_steps=sum(step['count'] == 0 for step in steps), annual_ready=annual_ready,
                received_ready=received_ready, tax_year=rules['tax_year'], limit=LIMIT)
