"""Owner-managed accounts and explicit historical coverage; no provider calls."""
from datetime import date, datetime, time, timezone, timedelta
import re

from werkzeug.exceptions import Conflict, NotFound
from models import Account, AccountHistory, AccountReserve, CashBalance, Category, Transaction
from services.cash_planning import day, payment_version, EMPTY_PAYMENTS
from services.database.connection import db
from services.invoices.workflow import LOCAL_TIME, lock_writes
from services.validation import uid, optional_text

TAX_YEAR = '2026-27'
FIRST = date(2026,4,6)
LAST = date(2027,4,5)


def account(identity, revision=None):
    if revision is not None:
        lock_writes()
    row = db.session.get(Account,uid(str(identity)),populate_existing=True,with_for_update=revision is not None)
    if row is None:
        raise NotFound('Account not found.')
    if revision is not None and str(row.revision)!=revision:
        raise Conflict('This account changed. Reload before saving.')
    return row


def save(identity, form, revision=None):
    lock_writes()
    row = account(identity,revision) if revision is not None else None
    if row and row.provider!='statement':
        raise ValueError('Starling manages this account. Add a separate statement account.')
    if row is None and db.session.get(Account,uid(identity)):
        raise Conflict('This account was already created.')
    name = optional_text(form.get('name'),field='account name',maximum=200)
    bank = optional_text(form.get('bank_name'),field='bank name',maximum=200)
    currency = form.get('currency','')
    if not name or not bank or not re.fullmatch('[A-Z]{3}',currency):
        raise ValueError('Enter an account name, bank and three-letter currency.')
    opened = day(form['opened_on']) if form.get('opened_on') else None
    today = datetime.now(LOCAL_TIME).date()
    if opened and (opened.year<1900 or opened>today):
        raise ValueError('The account opening date must be from 1900 through today.')
    if row and (currency!=row.currency or (row.opened_at and opened!=row.opened_at.astimezone(LOCAL_TIME).date())):
        raise ValueError('Keep the original currency and known opening date. Add a separate account for a different currency.')
    if row is None:
        category = uid(form.get('category',''))
        row = Account(account_uid=uid(identity),default_category_uid=category,currency=currency,
                      provider='statement',cash_active=True,revision=1,raw_payload={})
        db.session.add(row)
        db.session.flush()
        db.session.add(Category(account_uid=row.account_uid,category_uid=category,kind='manual',name='Statement records'))
    else:
        row.revision+=1
    row.name,row.bank_name=name,bank
    row.opened_at = datetime.combine(opened,time.min,LOCAL_TIME).astimezone(timezone.utc) if opened else None
    return row


def close(row, form):
    if row.provider!='statement' or row.closed_on:
        raise ValueError('Choose an open statement account.')
    closed = day(form.get('closed_on'))
    today = datetime.now(LOCAL_TIME).date()
    opened = row.opened_at.astimezone(LOCAL_TIME).date() if row.opened_at else None
    if closed>today or closed.year<1900 or (opened and closed<opened):
        raise ValueError('Enter a closure date within the account’s lifetime, no later than today.')
    balance = db.session.get(CashBalance,row.account_uid)
    reserve = db.session.get(AccountReserve,row.account_uid)
    if reserve and (reserve.tax_minor or reserve.ni_minor):
        raise ValueError('Clear this account’s tax and NI earmarks in Account money before closing it; record them on the receiving account if needed.')
    expected=payment_version(db.session,row.currency)
    if balance and balance.payment_fingerprint != expected and not (balance.payment_fingerprint is None and expected==EMPTY_PAYMENTS):
        raise ValueError('Check this account’s zero balance again after the recorded payment or correction.')
    end = datetime.combine(closed+timedelta(days=1),time.min,LOCAL_TIME)
    if db.session.scalar(db.select(Transaction.feed_item_uid).where(
            Transaction.account_uid==row.account_uid,Transaction.transaction_time>=end).limit(1)):
        raise ValueError('The closure date precedes saved transactions. Check the statement first.')
    if (closed>today or (opened and closed<opened) or form.get('confirm')!='1' or
            balance is None or balance.amount_minor!=0 or balance.recorded_currency!=row.currency or
            not datetime.now(timezone.utc)-timedelta(hours=24)<=balance.observed_at<=datetime.now(timezone.utc)):
        raise ValueError('Record a fresh zero balance, enter a valid closure date and confirm no money remains in this account.')
    row.closed_on,row.cash_active=closed,False
    row.revision+=1


def coverage_view(row, session, as_of=None):
    today = (as_of or datetime.now(timezone.utc)).astimezone(LOCAL_TIME).date()
    first = max(FIRST,row.opened_at.astimezone(LOCAL_TIME).date()) if row.opened_at else FIRST
    through = min(today,LAST,row.closed_on or LAST)
    history = session.get(AccountHistory,(row.account_uid,TAX_YEAR))
    start_time = datetime.combine(FIRST,time.min,LOCAL_TIME)
    end_time = datetime.combine(min(today,LAST)+timedelta(days=1),time.min,LOCAL_TIME)
    unreviewed = session.scalar(db.select(db.func.count()).select_from(Transaction).where(
        Transaction.account_uid==row.account_uid,Transaction.transaction_time>=start_time,
        Transaction.transaction_time<end_time,Transaction.status=='SETTLED',Transaction.confirmed_at.is_(None)))
    complete = through<first or bool(history and history.complete and history.from_on<=first and history.through_on>=through and not unreviewed)
    return dict(history=history,first=first,through=through,complete=complete,unreviewed=unreviewed)


def confirm_history(row, form):
    view = coverage_view(row,db.session)
    saved = view['history']
    if str(saved.revision if saved else 0)!=form.get('history_version'):
        raise Conflict('History coverage changed. Reload before confirming.')
    first,through = day(form.get('from_on')),day(form.get('through_on'))
    today = datetime.now(LOCAL_TIME).date()
    if form.get('confirm')!='1' or first>through or through>today or first>view['first'] or through<view['through']:
        raise ValueError('Cover the required dates and confirm all relevant records are included, including periods with no activity.')
    if view['unreviewed']:
        raise ValueError('Review and confirm imported transactions before confirming history coverage.')
    if saved is None:
        saved = AccountHistory(account_uid=row.account_uid,tax_year=TAX_YEAR,revision=0)
        db.session.add(saved)
    saved.from_on,saved.through_on,saved.complete=first,through,True
    saved.confirmed_at=datetime.now(timezone.utc)
    saved.revision+=1


def invalidate_history(identity):
    row = db.session.get(AccountHistory,(identity,TAX_YEAR))
    if row:
        row.complete=False
        row.confirmed_at=None
        row.revision+=1


def history_issues(session, as_of=None):
    issues=[]
    for row in session.scalars(db.select(Account).where(Account.provider=='statement').order_by(Account.account_uid)):
        view = coverage_view(row,session,as_of)
        if not view['complete']:
            issues.append(f'{row.name or "Statement account"}: review transactions and confirm prior-bank history coverage in Accounts and statements.')
    return issues
