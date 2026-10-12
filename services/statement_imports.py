"""Bounded, private CSV staging and atomic imports; no bank communication."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime, time, timedelta, timezone
from uuid import UUID, uuid4, uuid5

from werkzeug.exceptions import Conflict, Gone, NotFound, BadRequest
from sqlalchemy.orm import load_only
from models import StatementImport, StatementImportRow, Transaction, TransactionClassification
from services.database.connection import db
from services.invoices.workflow import LOCAL_TIME, lock_writes
from services.statement_accounts import account, invalidate_history
from services.transactions.income_form import parse_amount

MAX_BYTES = 512 * 1024
DATE_FORMATS = {'iso':'%Y-%m-%d','dmy':'%d/%m/%Y','mdy':'%m/%d/%Y', 'dashes':'%d-%m-%Y','dots':'%d.%m.%Y'}
MAP_FIELDS = {'date','amount','credit','debit','payer','reference','currency','date_format','amount_mode','decimal'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def upload(row, data, encoding='utf-8-sig', delimiter=','):
    lock_writes()
    if row.provider != 'statement':
        raise ValueError('Choose a statement account.')
    if not data or len(data)>MAX_BYTES:
        raise ValueError('Upload a CSV file up to 512 KiB.')
    if encoding not in ('utf-8-sig','cp1252') or delimiter not in (',',';','\t'):
        raise ValueError('Choose a supported encoding and separator.')
    try:
        text = data.decode(encoding)
        if '\x00' in text:
            raise ValueError()
        grid = list(csv.reader(io.StringIO(text,newline=''),delimiter=delimiter,strict=True))
    except (UnicodeError,csv.Error,ValueError):
        raise ValueError('The file is not valid CSV in the selected encoding. Try UTF-8 or Windows-1252.') from None
    if len(grid)<2 or len(grid)>1001 or not 1<=len(grid[0])<=30:
        raise ValueError('Include a header and 1–1,000 rows, with at most 30 columns.')
    if any(len(cell)>120 or not cell.strip() for cell in grid[0]):
        raise ValueError('Column headers must be present and at most 120 characters.')
    if any(len(cells)!=len(grid[0]) or any(len(cell)>2000 for cell in cells) for cells in grid[1:]):
        raise ValueError('Each row must match the header width; cells are limited to 2,000 characters.')
    now = datetime.now(timezone.utc)
    # Expired raw uploads have no business evidence to retain. Never purge imports.
    db.session.execute(db.delete(StatementImport).where(StatementImport.status.in_(('mapping','preview','discarded')),
        StatementImport.expires_at<now))
    active=db.session.scalar(db.select(db.func.count()).select_from(StatementImport).where(
        StatementImport.status.in_(('mapping','preview'))))
    if active>=20:
        raise ValueError('There are 20 active previews. Finish or discard one before uploading again.')
    batch = StatementImport(id=str(uuid4()),account_uid=row.account_uid,account_revision=row.revision,
        file_hash=hashlib.sha256(data).hexdigest(),headers=grid[0],rows=grid[1:],row_count=len(grid)-1,
        status='mapping',expires_at=now+timedelta(minutes=30))
    db.session.add(batch)
    return batch


def batch(identity):
    saved = db.session.get(StatementImport,str(identity),populate_existing=True)
    if saved is None:
        raise NotFound('Statement preview not found.')
    if saved.status!='imported' and saved.expires_at<datetime.now(timezone.utc):
        raise Gone('This preview expired. Upload the statement again.')
    return saved


def money(value, currency, decimal):
    value=value.strip()
    if not value:
        return None
    separator, grouping = ('.',',') if decimal=='point' else (',','.')
    if not re.fullmatch(r'-?(?:[0-9]+|[0-9]{1,3}(?:'+re.escape(grouping)+r'[0-9]{3})+)(?:'+re.escape(separator)+r'[0-9]{1,2})?',value):
        raise ValueError('Use numeric amounts with the selected decimal separator; no currency symbols.')
    try:
        result=parse_amount(value.replace(grouping,'').replace(separator,'.'),currency,signed=True)
    except BadRequest as error:
        raise ValueError(error.description) from None
    if abs(result)>2**63-1:
        raise ValueError('Amount is too large.')
    return result


def normalize(row, cells, mapping, number):
    def cell(key):
        index=mapping.get(key,'')
        return cells[int(index)].strip() if index!='' else ''
    try:
        date=datetime.strptime(cell('date'),DATE_FORMATS[mapping['date_format']]).date()
    except ValueError:
        raise ValueError('Date does not match the selected date format.') from None
    today=datetime.now(LOCAL_TIME).date()
    if date.year<1900 or date>today or (row.closed_on and date>row.closed_on) or (row.opened_at and date<row.opened_at.astimezone(LOCAL_TIME).date()):
        raise ValueError('Date is in the future or outside the account’s lifetime.')
    if cell('currency') and cell('currency').upper()!=row.currency:
        raise ValueError('Currency differs from this account. Import each currency into a separate account.')
    if mapping['amount_mode']=='signed':
        value=money(cell('amount'),row.currency,mapping['decimal'])
        if value is None:
            raise ValueError('Amount is missing.')
    else:
        credit=money(cell('credit'),row.currency,mapping['decimal'])
        debit=money(cell('debit'),row.currency,mapping['decimal'])
        if (credit is None and debit is None) or (credit or 0)<0 or (debit or 0)<0 or (credit and debit):
            raise ValueError('Enter one non-negative credit or debit, leaving the other blank or zero.')
        value=(credit or 0)-(debit or 0)
    if len(cell('payer'))>200 or len(cell('reference'))>1000:
        raise ValueError('Payment name is limited to 200 characters and reference to 1,000.')
    return dict(row_number=number,date=str(date),amount_minor=abs(value),currency=row.currency,
        direction='OUT' if value<0 or (value==0 and mapping['amount_mode']=='separate' and debit is not None and credit is None) else 'IN',payer=cell('payer'),reference=cell('reference'))


def map_rows(saved, form):
    lock_writes()
    saved=batch(saved.id)
    row=account(saved.account_uid,str(saved.account_revision))
    if saved.status!='mapping':
        raise Conflict('This file has already been mapped. Open its preview or upload again.')
    mapping={key:form.get(key,'') for key in MAP_FIELDS}
    if mapping['date_format'] not in DATE_FORMATS or mapping['decimal'] not in ('point','comma') or mapping['amount_mode'] not in ('signed','separate'):
        raise ValueError('Choose a date format, amount layout and decimal separator.')
    required={'date','amount'} if mapping['amount_mode']=='signed' else {'date','credit','debit'}
    columns=[]
    for key in ('date','amount','credit','debit','payer','reference','currency'):
        value=mapping[key]
        if key in required and value=='':
            raise ValueError('Map the date and required amount columns.')
        if value!='':
            if not value.isdigit() or int(value)>=len(saved.headers):
                raise ValueError('Choose a column from this file.')
            columns.append(int(value))
    if len(columns)!=len(set(columns)):
        raise ValueError('Use a separate column for each mapped field. Leave unused amount columns blank.')
    normalized,errors=[],[]
    for number,cells in enumerate(saved.rows,start=2):
        try:
            normalized.append(normalize(row,cells,mapping,number))
        except ValueError as error:
            errors.append(f'Row {number}: {error}')
    if errors:
        raise ValueError(' '.join(errors[:20])+ (f' ({len(errors)} rows need correction.)' if len(errors)>20 else ''))
    mapping_hash=digest(mapping)
    previous=db.session.scalar(db.select(StatementImport).where(StatementImport.account_uid==row.account_uid,
        StatementImport.file_hash==saved.file_hash,StatementImport.mapping_hash==mapping_hash,StatementImport.status=='imported'))
    if previous:
        saved.rows,saved.headers,saved.status=[],[],'discarded'
        return previous
    saved.mapping,saved.mapping_hash=mapping,mapping_hash
    saved.rows,saved.headers,saved.status=normalized,[],'preview'
    return saved


def key(row):
    return (row['date'],row['currency'],row['amount_minor'],row['direction'])


def preview(saved):
    row=account(saved.account_uid)
    dates=[item['date'] for item in saved.rows]
    if not dates:
        return [],digest([])
    start=datetime.combine(datetime.fromisoformat(min(dates)).date(),time.min,LOCAL_TIME)
    end=datetime.combine(datetime.fromisoformat(max(dates)).date()+timedelta(days=1),time.min,LOCAL_TIME)
    fields=(Transaction.account_uid,Transaction.category_uid,Transaction.feed_item_uid,Transaction.transaction_time,
        Transaction.amount_minor,Transaction.currency,Transaction.direction,Transaction.counterparty_name,Transaction.reference)
    query=db.select(Transaction).options(load_only(*fields)).where(Transaction.account_uid==row.account_uid,
        Transaction.transaction_time>=start,Transaction.transaction_time<end).order_by(Transaction.category_uid,Transaction.feed_item_uid)
    known={}
    # Stream existing records; retain only matching keys and a stable digest.
    checksum=hashlib.sha256()
    wanted={key(item) for item in saved.rows}
    for tx in db.session.scalars(query.execution_options(yield_per=500)):
        values=[getattr(tx,field.key) for field in fields]
        checksum.update(digest(values).encode())
        match=(str(tx.transaction_time.astimezone(LOCAL_TIME).date()),tx.currency,tx.amount_minor,tx.direction)
        if match in wanted:
            known[match]=known.get(match,0)+1
    seen=set()
    result=[]
    for item in saved.rows:
        match=key(item)
        result.append(dict(item,duplicates=known.get(match,0),repeated=match in seen))
        seen.add(match)
    return result,checksum.hexdigest()


def create_transaction(row, item, identity, source):
    when=datetime.combine(datetime.fromisoformat(item['date']).date(),time.min,LOCAL_TIME).astimezone(timezone.utc)
    tx=Transaction(account_uid=row.account_uid,category_uid=row.default_category_uid,feed_item_uid=identity,
        transaction_time=when,source_updated_at=when,amount_minor=item['amount_minor'],currency=row.currency,
        direction=item['direction'],source=source,status='SETTLED',counterparty_name=item['payer'] or None,
        reference=item['reference'] or None,raw_payload={})
    db.session.add(tx)
    db.session.flush()
    db.session.add(TransactionClassification(account_uid=row.account_uid,category_uid=row.default_category_uid,
        feed_item_uid=identity,type='other'))
    return tx


def confirm(saved, form):
    lock_writes()
    saved=batch(saved.id)
    if saved.status=='imported':
        return saved
    row=account(saved.account_uid,str(saved.account_revision))
    if saved.status!='preview':
        raise Conflict('Map the file before importing.')
    rows,fingerprint=preview(saved)
    if form.get('fingerprint')!=fingerprint:
        raise Conflict('Account transactions changed. Reload the preview and check duplicate decisions again.')
    if form.get('confirm')!='1':
        raise ValueError('Confirm that these are actual account transactions, excluding opening balances.')
    previous=db.session.scalar(db.select(StatementImport.id).where(StatementImport.account_uid==row.account_uid,
        StatementImport.file_hash==saved.file_hash,StatementImport.mapping_hash==saved.mapping_hash,StatementImport.status=='imported'))
    if previous:
        raise Conflict('This file was already imported. Open the account’s import history.')
    decisions=[]
    for item in rows:
        decision=form.get(f"row_{item['row_number']}")
        if decision not in ('import','skip'):
            raise ValueError(f"Choose import or skip for row {item['row_number']}; check every possible duplicate.")
        decisions.append((item,decision))
    imported=0
    for item,decision in decisions:
        identity=str(uuid5(UUID(saved.id),str(item['row_number']))) if decision=='import' else None
        if identity:
            create_transaction(row,item,identity,'STATEMENT_IMPORT')
            imported+=1
        db.session.add(StatementImportRow(batch_id=saved.id,row_number=item['row_number'],account_uid=row.account_uid,
            category_uid=row.default_category_uid if identity else None,feed_item_uid=identity,decision=decision))
    saved.status,saved.imported_at='imported',datetime.now(timezone.utc)
    saved.imported_count,saved.skipped_count=imported,len(rows)-imported
    if imported:
        invalidate_history(row.account_uid)
    return saved
