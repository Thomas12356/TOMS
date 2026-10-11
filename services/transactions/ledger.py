"""Bounded browser filters and shared saved-record attention semantics."""
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from werkzeug.exceptions import BadRequest

from models import Transaction, TransactionIncome, ExpenseDeduction
from services.database.connection import db
from services.transactions.classification import CLASSIFICATION_TYPES, effective_type
from services.validation import uid

FILTERS = {'account', 'page', 'q', 'from', 'to', 'classification', 'attention'}
ATTENTION = {'all': 'All records', 'needs_review': 'Needs attention',
             'unconfirmed': 'Awaiting confirmation', 'confirmed': 'Confirmed'}
LOCAL_TIME = ZoneInfo('Europe/London')


def ledger_options(options):
    result = {key: value for key, value in options.items() if key in FILTERS}
    try:
        if result.get('account', 'all') != 'all':
            result['account'] = uid(result['account'])
    except ValueError:
        raise BadRequest('account must be a valid account ID.') from None
    page = result.get('page', '1')
    if not re.fullmatch(r'[0-9]{1,10}', page) or not 1 <= int(page) <= 2**31 - 1:
        raise BadRequest('page must be a positive integer up to 2147483647.')
    if len(result.get('q', '')) > 200:
        raise BadRequest('Search must be at most 200 characters.')
    if result.get('classification', 'all') not in {'all', *CLASSIFICATION_TYPES}:
        raise BadRequest('Choose a valid classification filter.')
    if result.get('attention', 'all') not in ATTENTION:
        raise BadRequest('Choose a valid attention filter.')
    for key in ('from', 'to'):
        if key not in result:
            continue
        try:
            value = date.fromisoformat(result[key])
            if value.isoformat() != result[key] or value.year == 9999:
                raise ValueError()
        except ValueError:
            raise BadRequest('Enter filter dates as YYYY-MM-DD.') from None
    if result.get('from') and result.get('to') and result['from'] > result['to']:
        raise BadRequest('The end date must be on or after the start date.')
    return result


def needs_attention_expression(*, now=None):
    """Matches planning completeness plus unconfirmed and changed expense evidence.

    Callers outer-join the three one-to-one evidence relationships first.
    Pending/future receipts do not require settled-income details yet.
    """
    income = TransactionIncome
    complete = db.and_(income.account_uid.is_not(None), income.needs_review.is_(False),
        income.recorded_currency == Transaction.currency,
        db.or_(income.tax_treatment == 'non_taxable', db.and_(
            Transaction.currency == 'GBP', income.income_stream_id.is_not(None), income.gross_minor.is_not(None),
            income.tax_treatment != 'unknown', db.or_(income.tax_deducted_minor.is_not(None),
                income.tax_treatment.in_(('no_tax_deducted', 'non_taxable'))))))
    expense = ExpenseDeduction
    current_expense = db.and_(Transaction.direction == 'OUT', Transaction.status == 'SETTLED',
        Transaction.currency == expense.recorded_currency,
        db.or_(expense.is_tax_deductible.is_(False), Transaction.currency == 'GBP'),
        effective_type() == 'expense', expense.recorded_amount_minor == Transaction.amount_minor,
        expense.recorded_time == Transaction.transaction_time, expense.amount_minor <= Transaction.amount_minor)
    return db.or_(Transaction.confirmed_at.is_(None), db.and_(
        Transaction.direction == 'IN', Transaction.status == 'SETTLED',
        Transaction.transaction_time <= (now or datetime.now(timezone.utc)), effective_type() == 'income',
        db.not_(db.func.coalesce(complete, False))), db.and_(expense.account_uid.is_not(None),
        db.not_(db.func.coalesce(current_expense, False))))


def filtered_transactions(query, options):
    query = query.outerjoin(Transaction.classification).outerjoin(Transaction.income).outerjoin(Transaction.expense)
    if options.get('account', 'all') != 'all':
        query = query.where(Transaction.account_uid == options['account'])
    if options.get('q'):
        text = options['q'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        query = query.where(db.or_(Transaction.counterparty_name.ilike('%' + text + '%', escape='\\'),
                                   Transaction.reference.ilike('%' + text + '%', escape='\\')))
    if options.get('classification', 'all') != 'all':
        query = query.where(effective_type() == options['classification'])
    if options.get('from'):
        query = query.where(Transaction.transaction_time >= datetime.combine(
            date.fromisoformat(options['from']), datetime.min.time(), LOCAL_TIME))
    if options.get('to'):
        query = query.where(Transaction.transaction_time < datetime.combine(
            date.fromisoformat(options['to']) + timedelta(days=1), datetime.min.time(), LOCAL_TIME))
    attention = options.get('attention', 'all')
    if attention == 'needs_review':
        query = query.where(needs_attention_expression())
    elif attention == 'unconfirmed':
        query = query.where(Transaction.confirmed_at.is_(None))
    elif attention == 'confirmed':
        query = query.where(Transaction.confirmed_at.is_not(None))
    return query
