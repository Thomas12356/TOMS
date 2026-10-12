"""Explicit transfer pairs and reversible statement corrections, with evidence history."""
from datetime import datetime, timedelta, time, timezone
from uuid import uuid4

from sqlalchemy.orm import aliased, joinedload, load_only
from werkzeug.exceptions import Conflict, NotFound
from models import Account, InvoicePayment, StatementChange, StatementImportRow, Transaction
from services.database.connection import db
from services.invoices.workflow import LOCAL_TIME, lock_writes
from services.statement_accounts import account, invalidate_history
from services.statement_imports import digest, normalize
from services.transactions.classification import classification_details, save_classification
from services.transactions.review import record_snapshot
from services.validation import optional_text, uid


FIELDS=('account_uid','category_uid','feed_item_uid','transaction_time','amount_minor',
        'currency','direction','status','counterparty_name','reference','confirmed_at')


def snapshot(tx, invoice_links=None):
    values={field:getattr(tx,field) for field in FIELDS}
    values['classification']=classification_details(tx)
    values['income_linked']=tx.income is not None
    values['expense_linked']=tx.expense is not None
    values['invoice_links']=invoice_links if invoice_links is not None else [str(value) for value in db.session.scalars(db.select(InvoicePayment.id).where(
        InvoicePayment.account_uid==tx.account_uid,InvoicePayment.category_uid==tx.category_uid,
        InvoicePayment.feed_item_uid==tx.feed_item_uid).order_by(InvoicePayment.id))]
    return record_snapshot(values)


def version(tx, invoice_links=None):
    return digest(snapshot(tx,invoice_links))


def linked(tx):
    return tx.income is not None or tx.expense is not None or bool(db.session.scalar(db.select(InvoicePayment.id).where(
        InvoicePayment.account_uid==tx.account_uid,InvoicePayment.category_uid==tx.category_uid,
        InvoicePayment.feed_item_uid==tx.feed_item_uid).limit(1)))


def transaction(identity, *, lock=False):
    parts=identity.split('/')
    if len(parts)!=3:
        raise ValueError('Choose a transaction from this review.')
    tx=db.session.get(Transaction,tuple(uid(part) for part in parts),populate_existing=True,with_for_update=lock)
    if tx is None:
        raise NotFound('Transaction not found.')
    return tx


def identity(tx):
    return '/'.join((tx.account_uid,tx.category_uid,tx.feed_item_uid))


def audit(kind, before, transactions, reason):
    db.session.flush()
    after=[snapshot(tx) for tx in transactions]
    db.session.add(StatementChange(id=str(uuid4()),account_uid=transactions[0].account_uid,
        kind=kind,before=before,after=after,reason=reason))
    for tx in transactions:
        invalidate_history(tx.account_uid)


def suggestions(account_uid):
    """Bounded candidates, no names/date ambiguity used to claim a proven match."""
    other=aliased(Transaction)
    def eligible(model):
        allocations=db.select(InvoicePayment.id).where(InvoicePayment.account_uid==model.account_uid,
            InvoicePayment.category_uid==model.category_uid,InvoicePayment.feed_item_uid==model.feed_item_uid).exists()
        return db.and_(model.status=='SETTLED',model.confirmed_at.is_(None),model.amount_minor>0,
            ~model.income.has(),~model.expense.has(),~allocations,
            db.or_(~model.classification.has(),model.classification.has(type='other'),model.classification.has(type='internal_transfer')))
    query=db.select(Transaction,other).join(other,db.and_(
        other.account_uid!=Transaction.account_uid,other.currency==Transaction.currency,
        other.amount_minor==Transaction.amount_minor,other.direction!=Transaction.direction,
        other.transaction_time>=Transaction.transaction_time-timedelta(days=3),
        other.transaction_time<=Transaction.transaction_time+timedelta(days=3))).where(
        Transaction.account_uid==account_uid,eligible(Transaction),eligible(other)).options(
        load_only(*(getattr(Transaction,field) for field in (*FIELDS,'source'))),
        load_only(*(getattr(other,field) for field in (*FIELDS,'source'))),
        joinedload(Transaction.classification),joinedload(Transaction.income),joinedload(Transaction.expense),
        joinedload(other.classification),joinedload(other.income),joinedload(other.expense)).order_by(
        Transaction.transaction_time.desc(),Transaction.feed_item_uid,other.transaction_time,other.feed_item_uid).limit(201)
    pairs=db.session.execute(query).all()
    names={row.account_uid:row.name for row in db.session.scalars(db.select(Account))}
    versions={}
    def cached(tx):
        key=identity(tx)
        if key not in versions:
            versions[key]=version(tx,[])
        return versions[key]
    return [dict(left=a,right=b,left_id=identity(a),right_id=identity(b),
        left_version=cached(a),right_version=cached(b),other_name=names.get(b.account_uid) or 'Other account')
        for a,b in pairs[:200]],len(pairs)>200


def match(account_uid, form):
    lock_writes()
    keys=(form.get('left',''),form.get('right',''))
    locked={key:transaction(key,lock=True) for key in sorted(set(keys))}
    left,right=(locked[key] for key in keys)
    if left.account_uid!=account_uid or left.account_uid==right.account_uid:
        raise ValueError('Choose two different accounts, including this account.')
    if form.get('left_version')!=version(left) or form.get('right_version')!=version(right):
        raise Conflict('A payment changed. Reload and check the suggested pair again.')
    if (form.get('confirm')!='1' or left.currency!=right.currency or left.amount_minor!=right.amount_minor
            or left.amount_minor<=0 or left.direction==right.direction
            or abs(left.transaction_time-right.transaction_time)>timedelta(days=3)):
        raise ValueError('Confirm opposite payments of the same amount and currency within three days.')
    for tx in (left,right):
        if (tx.status!='SETTLED' or tx.confirmed_at or linked(tx)
                or classification_details(tx)['type'] not in ('other','internal_transfer') and tx.classification is not None):
            raise ValueError('This payment already has reviewed or linked evidence. Use its existing correction workflow first.')
    before=[snapshot(left),snapshot(right)]
    for tx in (left,right):
        save_classification(tx,'internal_transfer',tx.classification.notes if tx.classification else None)
        tx.confirmed_at=datetime.now(timezone.utc)
    audit('transfer',before,[left,right],'Owner confirmed these are two sides of a transfer between their own accounts.')


def imported_rows(batch_id, page=1):
    query=db.select(StatementImportRow,Transaction).outerjoin(Transaction,db.and_(
        StatementImportRow.account_uid==Transaction.account_uid,StatementImportRow.category_uid==Transaction.category_uid,
        StatementImportRow.feed_item_uid==Transaction.feed_item_uid)).where(StatementImportRow.batch_id==batch_id).options(
        load_only(*(getattr(Transaction,field) for field in (*FIELDS,'source'))),
        joinedload(Transaction.classification),joinedload(Transaction.income),joinedload(Transaction.expense)).order_by(StatementImportRow.row_number).offset((page-1)*50).limit(50)
    rows=db.session.execute(query).all()
    keys=[(tx.account_uid,tx.category_uid,tx.feed_item_uid) for _,tx in rows if tx]
    allocations={}
    if keys:
        for value in db.session.execute(db.select(InvoicePayment.account_uid,InvoicePayment.category_uid,
                InvoicePayment.feed_item_uid,InvoicePayment.id).where(db.tuple_(InvoicePayment.account_uid,
                InvoicePayment.category_uid,InvoicePayment.feed_item_uid).in_(keys)).order_by(InvoicePayment.id)):
            allocations.setdefault(tuple(value[:3]),[]).append(str(value.id))
    result=[]
    for row,tx in rows:
        links=allocations.get((tx.account_uid,tx.category_uid,tx.feed_item_uid),[]) if tx else []
        result.append(dict(row=row,tx=tx,identity=identity(tx) if tx else None,
            version=version(tx,links) if tx else None,
            linked=bool(tx and (tx.income or tx.expense or links))))
    return result


def correct(saved, form):
    lock_writes()
    tx=transaction(form.get('transaction',''),lock=True)
    provenance=db.session.scalar(db.select(StatementImportRow).where(StatementImportRow.batch_id==saved.id,
        StatementImportRow.account_uid==tx.account_uid,StatementImportRow.category_uid==tx.category_uid,
        StatementImportRow.feed_item_uid==tx.feed_item_uid))
    if saved.status!='imported' or provenance is None or tx.source!='STATEMENT_IMPORT':
        raise ValueError('Choose a saved statement transaction from this import.')
    if form.get('version')!=version(tx):
        raise Conflict('This transaction changed. Reload its current details before correcting it.')
    if linked(tx):
        raise ValueError('This receipt has income, expense or invoice evidence. Open its transaction evidence to review the links; direct statement corrections are blocked, including historical invoice allocations.')
    reason=optional_text(form.get('reason'),field='correction reason',maximum=1000)
    if not reason or form.get('confirm')!='1':
        raise ValueError('Explain the correction and confirm you checked the original statement.')
    before=[snapshot(tx)]
    action=form.get('action')
    if action=='edit':
        row=account(tx.account_uid)
        amount=form.get('amount','')
        if amount.startswith('-') or form.get('direction') not in ('IN','OUT'):
            raise ValueError('Enter a non-negative amount and money in or out.')
        cells=[form.get('date',''),amount if form['direction']=='IN' else '',amount if form['direction']=='OUT' else '',form.get('payer',''),form.get('reference','')]
        values=normalize(row,cells,dict(date='0',credit='1',debit='2',payer='3',reference='4',date_format='iso',amount_mode='separate',decimal='point'),provenance.row_number)
        tx.transaction_time=datetime.combine(datetime.fromisoformat(values['date']).date(),time.min,LOCAL_TIME).astimezone(timezone.utc)
        tx.amount_minor,tx.direction=values['amount_minor'],values['direction']
        tx.counterparty_name,tx.reference=values['payer'] or None,values['reference'] or None
    elif action=='exclude' and tx.status=='SETTLED':
        # Preserve provenance and amounts; ordinary settled-ledger calculations exclude this row.
        tx.status='REVERSED'
    elif action=='restore' and tx.status=='REVERSED':
        tx.status='SETTLED'
    else:
        raise ValueError('Choose an available correction.')
    save_classification(tx,'other',tx.classification.notes if tx.classification else None)
    if tx.status=='REVERSED':
        # The owner has explicitly reviewed the exclusion; do not requeue it on Home.
        tx.confirmed_at=datetime.now(timezone.utc)
    audit(action,before,[tx],reason)


def selection_version(records):
    return digest([(item['identity'],item['version']) for item in records])


def bulk_exclude(saved, form, selected, page):
    """Validate the complete selection first, then preserve one atomic audit."""
    lock_writes()
    records=imported_rows(saved.id,page)
    for identity in sorted(item['identity'] for item in records if item['tx']):
        transaction(identity,lock=True)
    records=imported_rows(saved.id,page)
    if form.get('selection_version')!=selection_version(records):
        raise Conflict('These import records changed. Reload before selecting corrections again.')
    reason=optional_text(form.get('reason'),field='correction reason',maximum=1000)
    if not reason or form.get('confirm')!='1' or not 1<=len(selected)<=50 or len(set(selected))!=len(selected):
        raise ValueError('Select 1–50 payments, explain the exclusion and confirm the statement check.')
    known={item['identity']:item for item in records if item['tx']}
    transactions=[]
    for identity in selected:
        item=known.get(identity)
        if item is None or item['linked'] or item['tx'].status!='SETTLED' or item['tx'].source!='STATEMENT_IMPORT':
            raise ValueError('Select only settled statement payments without linked evidence on this page.')
        transactions.append(item['tx'])
    before=[snapshot(tx) for tx in transactions]
    for tx in transactions:
        tx.status='REVERSED'
        save_classification(tx,'other',tx.classification.notes if tx.classification else None)
        tx.confirmed_at=datetime.now(timezone.utc)
    audit('bulk-exclude',before,transactions,reason)
