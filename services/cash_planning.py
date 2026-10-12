"""Recorded cash commitments; no bank writes or changes to tax calculations."""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re

from werkzeug.exceptions import Conflict, NotFound

from models import Account, AccountReserve, CashBalance, CashCommitment, CashCoverage, CashPayment
from services.database.connection import db
from services.invoices.workflow import lock_writes, LOCAL_TIME, MAX_MINOR
from services.transactions.income_form import parse_amount
from services.validation import optional_text, uid

COMPONENTS = dict(balancing='Balancing payment', first_on_account='First payment on account',
                  second_on_account='Second payment on account', other='Other statement charge')
RECURRENCES = dict(once='One-off', weekly='Weekly', monthly='Monthly', yearly='Yearly')


def day(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value or ''):
        raise ValueError('Enter a date in YYYY-MM-DD format.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError('Enter a valid date.') from None


def amount(value, currency, *, unknown=False, signed=False):
    result = parse_amount(value, currency, signed=signed)
    if result is None and not unknown:
        raise ValueError('Enter the amount. Unknown amounts must stay blank.')
    if result is not None and abs(result) > MAX_MINOR:
        raise ValueError('Amount is too large.')
    return result


def occurrences(item, end):
    """Keep the original day anchor through short months and leap years."""
    if not item.active:
        return
    last = min(end, item.end_date) if item.end_date else end
    for index in range(2000):
        if item.recurrence == 'once':
            due = item.due_date
        elif item.recurrence == 'weekly':
            due = item.due_date + timedelta(weeks=index)
        else:
            months = index * (12 if item.recurrence == 'yearly' else 1)
            year, month = divmod(item.due_date.year * 12 + item.due_date.month - 1 + months, 12)
            month += 1
            due = date(year, month, min(item.due_date.day, calendar.monthrange(year, month)[1]))
        if due > last:
            return
        yield due
        if item.recurrence == 'once' or due == last:
            return
    raise ValueError('This schedule has too much unresolved history. Use a later starting date or record a current opening balance.')


def commitment(identity, revision=None):
    if revision is not None:
        lock_writes()
    row = db.session.scalar(db.select(CashCommitment).where(CashCommitment.id == uid(str(identity)))
        .with_for_update().execution_options(populate_existing=True)) if revision is not None else db.session.get(CashCommitment, uid(str(identity)))
    if row is None:
        raise NotFound('Commitment not found.')
    if revision is not None and str(row.revision) != revision:
        raise Conflict('This commitment changed. Reload it before saving.')
    return row


def save_commitment(identity, form, revision=None):
    lock_writes()
    identity = uid(identity)
    existing = commitment(identity, revision) if revision is not None else None
    if revision is None and db.session.get(CashCommitment, identity):
        raise Conflict('This commitment was already saved. Open it from the list.')
    kind = form.get('kind')
    if kind not in ('bill', 'hmrc'):
        raise ValueError('Choose a bill or an HMRC statement charge.')
    name = optional_text(form.get('name'), field='name', maximum=200)
    if not name:
        raise ValueError('Enter a name.')
    currency = form.get('currency', '')
    if not re.fullmatch('[A-Z]{3}', currency) or (kind == 'hmrc' and currency != 'GBP'):
        raise ValueError('Use a three-letter currency; HMRC charges use GBP.')
    recurrence = form.get('recurrence')
    if recurrence not in RECURRENCES or (kind == 'hmrc' and recurrence != 'once'):
        raise ValueError('HMRC statement charges are one-off entries; choose a valid recurrence.')
    tax_year, component = None, None
    if kind == 'hmrc':
        tax_year, component = form.get('tax_year', ''), form.get('tax_component')
        if not re.fullmatch(r'20\d{2}-\d{2}', tax_year) or int(tax_year[-2:]) != (int(tax_year[:4]) + 1) % 100 or component not in COMPONENTS:
            raise ValueError('Enter the tax year and charge type shown on your HMRC statement.')
        if form.get('statement_confirmed') != '1':
            raise ValueError('Confirm that this is a remaining charge from your statement, after existing credits.')
        if component != 'other' and form.get('supported_charge_confirmed') != '1':
            raise ValueError('Confirm that this charge is income tax/Class 4 NI only, or choose Other for mixed or unknown charges.')
    values = dict(kind=kind, name=name, currency=currency, amount_minor=amount(form.get('amount', ''), currency, unknown=True),
        due_date=day(form.get('due_date')), recurrence=recurrence,
        end_date=day(form['end_date']) if form.get('end_date') else None, tax_year=tax_year, tax_component=component,
        notes=optional_text(form.get('notes'), field='notes', maximum=1000) or '')
    if values['end_date'] and (values['end_date'] < values['due_date'] or recurrence == 'once'):
        raise ValueError('Use an end date on or after the first due date for recurring bills only.')
    if existing:
        if not existing.active:
            raise ValueError('Cancelled schedules cannot be edited. Add a replacement.')
        payments = db.session.scalars(db.select(CashPayment).where(CashPayment.commitment_id == identity)).all()
        if payments and any(getattr(existing, key) != values[key] for key in ('kind','currency','due_date','recurrence','tax_year','tax_component')):
            raise ValueError('Keep the original schedule and currency for recorded payment history. Cancel remaining charges and add a replacement if needed.')
        paid = defaultdict(int)
        for row in payments:
            if row.active:
                paid[row.occurrence_date] += row.amount_minor
        if paid and (values['amount_minor'] is None or values['amount_minor'] < max(paid.values())):
            raise ValueError('The charge cannot be less than its recorded payments.')
        existing.revision += 1
    else:
        existing = CashCommitment(id=identity, active=True, revision=1)
        db.session.add(existing)
    for key, value in values.items():
        setattr(existing, key, value)
    return existing


def record_payment(item, form):
    identity = uid(form.get('payment_id', ''))
    if db.session.get(CashPayment, identity):
        raise Conflict('This payment was already recorded.')
    due, paid_on = day(form.get('occurrence_date')), day(form.get('paid_on'))
    if not item.active or due not in set(occurrences(item, due)):
        raise ValueError('Choose a due date in this active schedule.')
    if paid_on > datetime.now(LOCAL_TIME).date():
        raise ValueError('Record payments already made, not future payments.')
    if item.amount_minor is None:
        raise ValueError('Enter the actual charge before recording a payment.')
    value = amount(form.get('amount', ''), item.currency)
    credit = amount(form.get('tax_credit',''), item.currency, unknown=True) or 0
    if credit > value or (credit and (item.kind!='hmrc' or item.tax_year!='2026-27' or item.tax_component=='other')):
        raise ValueError('Claim only the supported 2026–27 income tax/Class 4 NI portion of this payment, no larger than the payment itself.')
    paid = db.session.scalar(db.select(db.func.coalesce(db.func.sum(CashPayment.amount_minor), 0)).where(
        CashPayment.commitment_id == item.id, CashPayment.occurrence_date == due, CashPayment.active.is_(True)))
    if value <= 0 or value > item.amount_minor - paid:
        raise ValueError('Record a positive payment no larger than this occurrence’s outstanding charge.')
    now = datetime.now(timezone.utc)
    db.session.add(CashPayment(id=identity, commitment_id=item.id, occurrence_date=due, paid_on=paid_on,
        amount_minor=value, tax_credit_minor=credit, reference=optional_text(form.get('reference'), field='payment reference', maximum=200) or '',
        active=True, recorded_at=now, changed_at=now))
    item.revision += 1


def save_balance(form):
    lock_writes()
    identity = uid(form.get('account', ''))
    account = db.session.get(Account, identity)
    if account is None:
        raise ValueError('Choose a saved account.')
    balance = db.session.get(CashBalance, identity)
    if str(balance.revision if balance else 0) != form.get('version'):
        raise Conflict('This balance changed. Reload before saving.')
    try:
        observed = datetime.fromisoformat(form.get('observed_at', ''))
        if observed.tzinfo is not None:
            raise ValueError()
        if observed.replace(tzinfo=LOCAL_TIME,fold=0).utcoffset() != observed.replace(tzinfo=LOCAL_TIME,fold=1).utcoffset():
            raise ValueError()
        observed = observed.replace(tzinfo=LOCAL_TIME).astimezone(timezone.utc)
    except ValueError:
        raise ValueError('Enter the balance date and time in Europe/London.') from None
    now = datetime.now(timezone.utc)
    if observed > now:
        raise ValueError('A balance observation cannot be in the future.')
    if balance is None:
        balance = CashBalance(account_uid=identity, revision=0)
        db.session.add(balance)
    balance.amount_minor = amount(form.get('amount', ''), account.currency, signed=True)
    balance.recorded_currency = account.currency
    balance.observed_at, balance.recorded_at = observed, now
    balance.revision += 1


def save_review(form):
    """Save reviewed observations and optional coverage in one atomic write."""
    lock_writes()
    before = snapshot(db.session)
    if before['fingerprint'] != form.get('fingerprint'):
        raise Conflict('Planning records changed. Reload and check them before saving this review.')
    allowed = {'action', 'fingerprint', 'confirm', 'csrf_token'}
    for account in before['accounts']:
        allowed.update(f'{field}_{account.account_uid}' for field in ('amount', 'version', 'observed'))
    if set(form) - allowed or any(len(form.getlist(key)) != 1 for key in form):
        raise ValueError('Supply each review field once, for the accounts shown.')
    if form.get('confirm') not in (None, '1'):
        raise ValueError('Confirm coverage using the review checkbox.')
    changed = []
    for account in before['accounts']:
        identity = account.account_uid
        value = form.get(f'amount_{identity}', '').strip()
        if not value:
            continue  # Retain the observation and its age, never silently refresh it.
        save_balance(dict(account=identity, amount=value,
            version=form.get(f'version_{identity}'), observed_at=form.get(f'observed_{identity}')))
        changed.append(identity)
    if not changed and form.get('confirm') != '1':
        raise ValueError('Enter an updated balance or confirm that you reviewed all commitments.')
    db.session.flush()
    if form.get('confirm') == '1':
        fingerprint = snapshot(db.session)['fingerprint']
        coverage = db.session.get(CashCoverage, 1)
        if coverage is None:
            coverage = CashCoverage(id=1)
            db.session.add(coverage)
        coverage.fingerprint = fingerprint
        coverage.confirmed_at = datetime.now(timezone.utc)
    return changed


def snapshot(session, *, as_of=None, tax_target=None, tax_year='2026-27'):
    now = as_of or datetime.now(timezone.utc)
    today = now.astimezone(LOCAL_TIME).date()
    end = today + timedelta(days=30)
    accounts = session.scalars(db.select(Account).order_by(Account.account_uid)).all()
    balances = {row.account_uid: row for row in session.scalars(db.select(CashBalance))}
    reserves = {row.account_uid: row for row in session.scalars(db.select(AccountReserve))}
    items = session.scalars(db.select(CashCommitment).order_by(CashCommitment.due_date, CashCommitment.id)).all()
    payments = session.scalars(db.select(CashPayment).order_by(CashPayment.id)).all()
    coverage = session.get(CashCoverage, 1)
    fingerprint = hashlib.sha256(json.dumps(dict(day=str(today), accounts=[(a.account_uid,a.currency) for a in accounts],
        balances=[b.as_dict() for b in sorted(balances.values(), key=lambda b:b.account_uid)],
        reserves=[r.as_dict() for r in sorted(reserves.values(),key=lambda r:r.account_uid)],
        commitments=[item.as_dict() for item in items], payments=[p.as_dict() for p in payments]),
        default=lambda value:value.astimezone(timezone.utc).isoformat() if isinstance(value,datetime) else str(value),
        sort_keys=True).encode()).hexdigest()
    complete = bool(coverage and coverage.fingerprint == fingerprint)
    paid, latest_change = defaultdict(int), {}
    currencies = {item.id:item.currency for item in items}
    item_by_id = {item.id:item for item in items}
    direct_tax_paid = 0
    for payment in payments:
        if payment.active:
            paid[(payment.commitment_id,payment.occurrence_date)] += payment.amount_minor
            item = item_by_id[payment.commitment_id]
            if item.kind == 'hmrc' and item.tax_year == tax_year and item.tax_component != 'other':
                direct_tax_paid += payment.tax_credit_minor
        currency = currencies[payment.commitment_id]
        latest_change[currency] = max(latest_change.get(currency, payment.changed_at), payment.changed_at)
    groups = {}
    def group(currency):
        return groups.setdefault(currency, dict(currency=currency, cash=0, bills=0, hmrc=0, hmrc_current=0, hmrc_other=0, earmarks=0,
            tax_paid=direct_tax_paid if currency=='GBP' else 0,
            tax_target=max(0,tax_target-direct_tax_paid) if currency=='GBP' and tax_target is not None else None,
            reasons=[], available=None))
    balance_tasks = []
    for account in accounts:
        row, balance = group(account.currency), balances.get(account.account_uid)
        account_reasons = []
        if balance is None:
            account_reasons.append('No balance recorded')
            row['reasons'].append('A saved account has no balance observation.')
        else:
            row['cash'] += balance.amount_minor
            if balance.recorded_currency != account.currency:
                account_reasons.append('Currency changed')
                row['reasons'].append('An account currency changed. Record a new balance in its current currency.')
            if not now - timedelta(hours=24) <= balance.observed_at <= now:
                account_reasons.append('Balance needs a fresh observation')
                row['reasons'].append('A balance is older than 24 hours or ahead of the clock.')
            if account.currency in latest_change and balance.recorded_at < latest_change[account.currency]:
                account_reasons.append('Check balance after payment or correction')
                row['reasons'].append('Update balances after recording or correcting a payment.')
        if account_reasons:
            balance_tasks.append(dict(account=account, reasons=account_reasons))
        reserve = reserves.get(account.account_uid)
        if reserve:
            row['earmarks'] += reserve.tax_minor + reserve.ni_minor
    upcoming, total_count = [], 0
    for item in items:
        if not item.active:
            continue
        row = group(item.currency)
        try:
            for due in occurrences(item, end):
                remaining = None if item.amount_minor is None else max(0, item.amount_minor - paid[(item.id,due)])
                if remaining == 0:
                    continue
                total_count += 1
                if len(upcoming) < 100:
                    upcoming.append(dict(item=item, due=due, remaining=remaining, overdue=due < today))
                if remaining is None:
                    row['reasons'].append('An overdue or upcoming commitment has an unknown amount.')
                else:
                    row['hmrc' if item.kind=='hmrc' else 'bills'] += remaining
                    if item.kind == 'hmrc':
                        row['hmrc_current' if item.tax_year==tax_year and item.tax_component!='other' else 'hmrc_other'] += remaining
        except ValueError as invalid:
            row['reasons'].append(str(invalid))
    for row in groups.values():
        if not complete:
            row['reasons'].append('Confirm that all balances and commitments are included for this 30-day window.')
        if not any(a.currency==row['currency'] for a in accounts):
            row['reasons'].append('No saved account balance in this currency.')
        if row['tax_target'] is None:
            row['reasons'].append('A supported received-income tax target is unavailable. Complete tax planning first.'
                if row['currency']=='GBP' else 'Spending figures in this currency are unavailable: tax planning supports GBP.')
        row['protected'] = max(row['earmarks'],max(row['hmrc_current'],row['tax_target'] or 0) + row['hmrc_other'])
        row['reasons'] = list(dict.fromkeys(row['reasons']))
        if not row['reasons']:
            row['available'] = row['cash'] - row['bills'] - row['protected']
    return dict(today=today, end=end, accounts=accounts, balances=balances, items=items, payments=payments,
        upcoming=sorted(upcoming,key=lambda row:(row['due'],row['item'].id)), count=total_count,
        groups=list(groups.values()), fingerprint=fingerprint, complete=complete, balance_tasks=balance_tasks)
