"""Conservative suggestions from consistent confirmed evidence, never automatic writes."""
from datetime import datetime, timezone

from models import Transaction, TransactionClassification, TransactionIncome, IncomeStream
from services.database.connection import db
from services.transactions.classification import CLASSIFICATION_TYPES, classification_details, effective_type
from services.transactions.review import record_version


def payment_suggestions(payment):
    name = (payment.counterparty_name or '').strip()
    reference = (payment.reference or '').strip()
    if (not name or not reference or len(name) > 200 or len(reference) > 200
            or payment.status != 'SETTLED' or payment.confirmed_at is not None
            or payment.transaction_time > datetime.now(timezone.utc)):
        return {}
    matching = db.select(Transaction).where(
        Transaction.account_uid == payment.account_uid, Transaction.currency == payment.currency,
        Transaction.direction == payment.direction, Transaction.source == payment.source,
        db.func.lower(db.func.trim(Transaction.counterparty_name)) == name.lower(),
        db.func.lower(db.func.trim(Transaction.reference)) == reference.lower(),
        Transaction.status == 'SETTLED', Transaction.confirmed_at.is_not(None),
        Transaction.transaction_time < payment.transaction_time)
    base = matching.with_only_columns(Transaction.account_uid, Transaction.category_uid,
                                      Transaction.feed_item_uid).subquery()
    peer = db.and_(Transaction.account_uid == base.c.account_uid,
                   Transaction.category_uid == base.c.category_uid, Transaction.feed_item_uid == base.c.feed_item_uid)
    explanation = 'confirmed earlier payments with the same account, currency, direction, payer and reference'
    result = {}
    if payment.classification is None and payment.income is None and payment.expense is None:
        effective = effective_type()
        kinds = db.session.execute(db.select(effective, db.func.count(), db.func.count(TransactionClassification.type))
            .select_from(Transaction).join(base, peer).outerjoin(Transaction.classification)
            .group_by(effective)).all()
        if len(kinds) == 1 and kinds[0][2] >= 2:
            kind, count, _ = kinds[0]
            if payment.direction in CLASSIFICATION_TYPES[kind]['directions']:
                result['classification'] = dict(value=kind, label=CLASSIFICATION_TYPES[kind]['label'],
                    reason=f'{count} {explanation} use this classification.',
                    version=record_version(dict(kind=kind, count=count)))
    if (payment.direction == 'IN' and classification_details(payment)['type'] == 'income'
            and (payment.income is None or (payment.income.income_stream_id is None
                                           and payment.income.tax_treatment != 'non_taxable'))):
        sources = db.session.execute(db.select(TransactionIncome.income_stream_id, db.func.count())
            .select_from(Transaction).join(base, peer).join(Transaction.income)
            .outerjoin(Transaction.classification).where(
                effective_type() == 'income',
                TransactionIncome.income_stream_id.is_not(None), TransactionIncome.needs_review.is_(False),
                TransactionIncome.recorded_currency == payment.currency)
            .group_by(TransactionIncome.income_stream_id)).all()
        if len(sources) == 1 and sources[0][1] >= 2:
            identity, count = sources[0]
            stream = db.session.get(IncomeStream, identity)
            if stream is not None and not stream.archived:
                result['source'] = dict(value=identity, label=stream.name,
                    reason=f'{count} {explanation} were assigned to this income source. Amounts and tax treatment are not copied.',
                    version=record_version(dict(source=identity, count=count)))
    return result
