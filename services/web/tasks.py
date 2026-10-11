"""Bounded owner actions derived from saved evidence, with no external traffic."""
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy.orm import load_only, selectinload

from models import Transaction, TransactionIncome
from services.database.connection import db
from services.tax.readiness import check_income, payment_link
from services.tax.records import expense_current
from services.transactions.classification import classification_details, effective_type
from services.transactions.ledger import filtered_transactions, needs_attention_expression
from services.transactions.review import REVIEW_FIELDS
from services.web.presentation import local_date

LIMIT = 20
TASK_FILTERS = dict(all='All actions', imports='Imports', payments='Payments',
                    invoices='Invoice evidence', forecasts='Income setup', planning='Planning warnings')
IMPORT_AGE = timedelta(days=1)


def payment_task(payment, now):
    reasons = []
    changed = False
    if payment.expense and not expense_current(payment.expense):
        changed = True
        reasons.append('The linked expense no longer matches this payment. Check its amount, currency and eligibility.')
    settled_income = (payment.direction == 'IN' and payment.status == 'SETTLED'
                      and payment.transaction_time <= now and classification_details(payment)['type'] == 'income')
    if settled_income and not check_income(payment, {}):
        changed = changed or bool(payment.income and (payment.income.needs_review
                                  or payment.income.recorded_currency != payment.currency))
        reasons.append('Complete or correct the income source, gross pay and actual withholding. Unknown amounts stay unknown.')
    if payment.confirmed_at is None:
        reasons.append('Check the classification and saved details before confirming.')
    if payment.status != 'SETTLED' or payment.transaction_time > now:
        reasons.append('Settled income details are not required yet for this pending or future record.')
    key = ':'.join((payment.account_uid, payment.category_uid, payment.feed_item_uid))
    url = payment_link(payment, 'guided_review.payment')
    if payment.confirmed_at is not None:
        url += '?step=details'
    name = payment.counterparty_name or 'Unnamed payment'
    if len(name) > 200:
        name = name[:199] + '…'
    return dict(key='payment:' + key, title='Correct changed payment evidence' if changed else
                'Complete income details' if settled_income and not check_income(payment, {}) else 'Review payment',
                label=name + ' · ' + local_date(payment.transaction_time).strftime('%d %b %Y'),
                reason=' '.join(reasons), url=url, action='Review this payment')


def task_inbox(session, checks, invoice_rows, *, last_success, latest, latest_full, now, selected='all'):
    groups = {key: dict(key=key, title=title, count=0, items=[], url=url)
              for key, title, url in (
                  ('imports', 'Imports', '/dashboard/system'),
                  ('payments', 'Payments', '/dashboard?account=all&attention=needs_review'),
                  ('invoices', 'Invoice evidence', '/dashboard/invoices'),
                  ('forecasts', 'Income setup', '/dashboard/income-streams'),
                  ('planning', 'Planning warnings', '/dashboard/tax-estimate'))}
    for key, label in dict(imports='System and import status', payments='All payments needing attention',
                           invoices='All invoices', forecasts='All income sources', planning='Full tax estimate').items():
        groups[key]['action'] = label

    def add(group, **item):
        group['count'] += 1
        if len(group['items']) < LIMIT:
            group['items'].append(item)

    imports = groups['imports']
    failed = latest if latest and latest.status == 'failed' else latest_full if latest_full and latest_full.status == 'failed' else None
    running = latest if latest and latest.status == 'running' else latest_full if latest_full and latest_full.status == 'running' else None
    if failed:
        add(imports, key='import:failed', title='Check the failed import',
            label='Latest saved import failed' if failed is latest else 'Most recent full import failed',
            reason='Saved transactions are retained. Check system status before retrying; opening Home does not start an import.',
            url=imports['url'], action='Check import status')
    elif running:
        if running.started_at > now or now - running.started_at > timedelta(hours=1):
            add(imports, key='import:running', title='Check import progress',
                label='Import timestamp is ahead of this device’s clock' if running.started_at > now else 'Still marked running after an hour',
                reason='This is a saved status, not proof that a worker is still active. Check system status.',
                url=imports['url'], action='Check import status')
    elif last_success is None or last_success > now or now - last_success >= IMPORT_AGE:
        add(imports, key='import:freshness', title='Check saved import freshness',
            label='No successful full import recorded' if last_success is None else
                  'Full-import timestamp is ahead of this device’s clock' if last_success > now else
                  'Last successful full import was at least 24 hours ago',
            reason='Partial imports do not establish full account history. Check system status; refreshing Home makes no bank request.',
            url=imports['url'], action='Check import status')

    payments = groups['payments']
    query = filtered_transactions(db.select(Transaction), {}).where(needs_attention_expression(now=now))
    payments['count'] = session.scalar(db.select(db.func.count()).select_from(query.subquery()))
    priority = db.case((db.or_(TransactionIncome.needs_review.is_(True), Transaction.confirmed_at.is_not(None)), 0),
        (db.and_(Transaction.direction == 'IN', Transaction.status == 'SETTLED',
                 Transaction.transaction_time <= now, effective_type() == 'income'), 1), else_=2)
    for payment in session.scalars(query.options(
            load_only(*(getattr(Transaction, field) for field in REVIEW_FIELDS), Transaction.confirmed_at),
            selectinload(Transaction.income), selectinload(Transaction.classification), selectinload(Transaction.expense)
        ).order_by(priority, Transaction.transaction_time, Transaction.account_uid,
                   Transaction.category_uid, Transaction.feed_item_uid).limit(LIMIT)):
        item = payment_task(payment, now)
        item['url'] += ('&' if '?' in item['url'] else '?') + urlencode({'return_to': 'home', 'tasks': selected})
        payments['items'].append(item)

    for row in invoice_rows:
        invoice, summary = row['invoice'], row['summary']
        reasons = []
        if summary['stale']:
            reasons.append('Review stale payment allocations; they do not reduce the outstanding amount.')
        if row['changed_work']:
            reasons.append('Current work differs from the saved invoice lines. Check both before changing the invoice.')
        if summary['overdue']:
            reasons.append('Its due date has passed with an outstanding amount. Recording sending does not send a message.')
        if reasons:
            add(groups['invoices'], key='invoice:' + invoice.id, title='Review invoice evidence',
                label=(invoice.number or 'Draft invoice') + ' · ' + invoice.client_name,
                reason=' '.join(reasons), url='/dashboard/invoices/' + invoice.id, action='Review this invoice')
    steps = {step['key']: step for step in checks['steps']}
    forecasts = groups['forecasts']
    forecasts['count'] = steps['forecasts']['count']
    forecasts['items'] = [dict(key='forecast:' + item['url'], title='Complete income setup', action='Open income setup', **item)
                          for item in steps['forecasts']['items']]
    if steps['planning']['count']:
        count = steps['planning']['count']
        add(groups['planning'], key='planning:estimate', title='Review the estimate explanations',
            label=f'{count} planning explanation' + ('' if count == 1 else 's'),
            reason='Some explanations relate to the record actions above. Review the full estimate for supported-scope and calculation warnings.',
            url=groups['planning']['url'], action='Review planning warnings')
    selected_groups = list(groups.values()) if selected == 'all' else [groups[selected]]
    limit = 6 if selected == 'all' else LIMIT
    return dict(categories=list(groups.values()), total=sum(group['count'] for group in groups.values()),
                selected=selected, selected_count=sum(group['count'] for group in selected_groups), limit=limit,
                items=[dict(category=group['title'], **item) for group in selected_groups for item in group['items']][:limit])
