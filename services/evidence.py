"""Private deduplicated attachments, independent of deduction eligibility and amounts."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
# The fixed parser command receives document bytes through stdin, never a shell.
import subprocess  # nosec B404
import sys
from uuid import uuid4

from sqlalchemy.orm import joinedload
from werkzeug.exceptions import Conflict, NotFound
from werkzeug.utils import secure_filename
from models import EvidenceAsset, EvidenceDocument, MileageEntry, Transaction
from services.database.connection import db
from services.invoices.workflow import lock_writes
from services.tax.deduction_forms import payment_key
from services.validation import optional_text, uid

MAX_FILE=10*1024*1024
MAX_STORAGE=256*1024*1024


def target(kind,key, *, lock=False):
    if kind=='transaction':
        try:
            parts=payment_key(key)
        except ValueError:
            raise NotFound('Outgoing payment not found.') from None
        row=db.session.get(Transaction,parts,populate_existing=True,with_for_update=lock)
        if row is None or row.direction!='OUT':
            raise NotFound('Outgoing payment not found.')
        fields=dict(account_uid=parts[0],category_uid=parts[1],feed_item_uid=parts[2])
        label=row.counterparty_name or 'Expense payment'
    elif kind=='mileage':
        try:
            identity=uid(key)
        except ValueError:
            raise NotFound('Journey not found.') from None
        row=db.session.get(MileageEntry,identity,populate_existing=True,with_for_update=lock)
        if row is None:
            raise NotFound('Journey not found.')
        fields=dict(mileage_id=row.id)
        label=str(row.journey_date)+' · '+row.purpose
    else:
        raise NotFound('Evidence target not found.')
    return row,fields,label


def matching(kind,fields):
    return db.and_(EvidenceDocument.kind==kind,*(getattr(EvidenceDocument,key)==value for key,value in fields.items()))


def documents(kind,fields):
    return db.session.scalars(db.select(EvidenceDocument).where(matching(kind,fields)).options(
        joinedload(EvidenceDocument.asset)).order_by(EvidenceDocument.created_at.desc())).all()


def upload(kind,key,file,notes):
    lock_writes()
    _,fields,_=target(kind,key,lock=True)
    notes=optional_text(notes,field='evidence notes',maximum=1000) or ''
    data=file.stream.read(MAX_FILE+1)
    if not 1<=len(data)<=MAX_FILE:
        raise ValueError('Choose one file up to 10 MiB.')
    digest=hashlib.sha256(data).hexdigest()
    existing=db.session.scalar(db.select(EvidenceDocument).where(matching(kind,fields),
        EvidenceDocument.asset_sha256==digest,EvidenceDocument.active.is_(True)))
    if existing:
        return existing,False
    if db.session.scalar(db.select(db.func.count()).select_from(EvidenceDocument).where(matching(kind,fields)))>=100:
        raise ValueError('This record has 100 attachments, including archived history.')
    asset=db.session.get(EvidenceAsset,digest)
    if asset is None:
        try:
            # Fixed trusted executable/module; no user-controlled command arguments.
            result=subprocess.run([sys.executable,'-m','services.evidence_media'],input=data,  # nosec B603
                cwd=Path(__file__).resolve().parents[1], shell=False,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,check=True)
            info=json.loads(result.stdout)
        except (subprocess.SubprocessError,ValueError):
            raise ValueError('This file could not be safely read. Try a smaller static image or PDF.') from None
        if info.get('error'):
            raise ValueError(info['error'])
        used=db.session.scalar(db.select(db.func.coalesce(db.func.sum(EvidenceAsset.byte_count+db.func.coalesce(db.func.octet_length(EvidenceAsset.preview),0)),0)))
        preview=base64.b64decode(info['preview']) if info['preview'] else None
        if used+len(data)+len(preview or b'')>MAX_STORAGE:
            raise ValueError('Private evidence storage has reached its 256 MiB capacity. Existing evidence is safe. Archiving retains originals and does not free storage.')
        asset=EvidenceAsset(sha256=digest,byte_count=len(data),data=data,preview=preview,
            mime=info['mime'],suffix=info['suffix'],pages=info['pages'])
        db.session.add(asset)
        db.session.flush()
    stem=Path(secure_filename(file.filename or 'Receipt')).stem[:160] or 'Receipt'
    doc=EvidenceDocument(id=str(uuid4()),asset_sha256=digest,kind=kind,**fields,
        name=stem+'.'+asset.suffix,notes=notes,revision=1,active=True)
    db.session.add(doc)
    return doc,True


def document(identity):
    doc=db.session.get(EvidenceDocument,uid(str(identity)),populate_existing=True)
    if doc is None:
        raise NotFound('Attachment not found.')
    return doc


def archive(identity, form):
    lock_writes()
    doc=document(identity)
    if str(doc.revision)!=form.get('version'):
        raise Conflict('This attachment changed. Reload before updating it.')
    if form.get('confirm')!='1':
        raise ValueError('Confirm archiving; the original stays in private storage and backups.')
    doc.active=not doc.active
    doc.archived_at=None if doc.active else datetime.now(timezone.utc)
    doc.revision+=1
    return doc
