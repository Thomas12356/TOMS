"""Compact, streamed business evidence using the caller's database snapshot."""
from types import SimpleNamespace

from models import Transaction, TransactionIncome, TransactionClassification, ExpenseDeduction
from services.database.connection import db
from services.transactions.review import REVIEW_FIELDS


def compact_payments(session, predicate, *, descending=False):
    """Yield allowlisted fields without constructing a complete ORM identity map."""
    fields = dict(payment=('account_uid', 'category_uid', 'feed_item_uid', 'confirmed_at', *REVIEW_FIELDS),
                  income=tuple(column.name for column in TransactionIncome.__table__.columns),
                  expense=tuple(column.name for column in ExpenseDeduction.__table__.columns),
                  classification=tuple(column.name for column in TransactionClassification.__table__.columns))
    models = dict(payment=Transaction, income=TransactionIncome, expense=ExpenseDeduction,
                  classification=TransactionClassification)
    columns = [getattr(models[kind], field).label(kind + '_' + field)
               for kind, names in fields.items() for field in names]
    query = db.select(*columns).select_from(Transaction).outerjoin(Transaction.income).outerjoin(
        Transaction.expense).outerjoin(Transaction.classification).where(predicate).order_by(
            Transaction.transaction_time.desc() if descending else Transaction.transaction_time,
            Transaction.account_uid, Transaction.category_uid, Transaction.feed_item_uid)
    positions = []
    offset = 0
    for kind, names in fields.items():
        positions.append((kind, names, offset, offset + len(names)))
        offset += len(names)
    for values in session.execute(query.execution_options(yield_per=500)):
        records = {kind: SimpleNamespace(**dict(zip(names, values[start:stop])))
                   if values[start] is not None else None for kind, names, start, stop in positions}
        payment = records['payment']
        payment.income, payment.expense, payment.classification = (records[kind] for kind in ('income', 'expense', 'classification'))
        if payment.expense is not None:
            payment.expense.transaction = payment
        yield payment
