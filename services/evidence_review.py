"""Document reminders reflect explicit needs, never a blanket receipt rule."""
import hmac
from sqlalchemy import tuple_
from werkzeug.exceptions import Conflict, NotFound
from models import EvidenceDocument, ExpenseDeduction, MileageEntry
from services import evidence
from services.database.connection import db
from services.invoices.workflow import lock_writes
from services.tax.deduction_forms import expense_version, mileage_version
from services.validation import optional_text


def expense_count():
    return db.select(db.func.count()).select_from(EvidenceDocument).where(
        EvidenceDocument.active.is_(True), EvidenceDocument.kind == 'transaction',
        EvidenceDocument.account_uid == ExpenseDeduction.account_uid,
        EvidenceDocument.category_uid == ExpenseDeduction.category_uid,
        EvidenceDocument.feed_item_uid == ExpenseDeduction.feed_item_uid).correlate(ExpenseDeduction).scalar_subquery()


def journey_count():
    return db.select(db.func.count()).select_from(EvidenceDocument).where(
        EvidenceDocument.active.is_(True), EvidenceDocument.kind == 'mileage',
        EvidenceDocument.mileage_id == MileageEntry.id).correlate(MileageEntry).scalar_subquery()


def missing_condition(model):
    condition = model.evidence_needed_reason.is_not(None)
    if model is ExpenseDeduction:
        return db.and_(condition, model.is_tax_deductible.is_(True), expense_count() == 0)
    if model is MileageEntry:
        return db.and_(condition, journey_count() == 0)
    raise ValueError('Unsupported evidence record.')


def counts(expenses, journeys, selected=None):
    keys = {(row.account_uid, row.category_uid, row.feed_item_uid) for row in expenses}
    if selected:
        keys.add((selected.account_uid, selected.category_uid, selected.feed_item_uid))
    identities = {row.id for row in journeys}
    result = {**{key: 0 for key in keys}, **{key: 0 for key in identities}}
    if keys:
        query = db.select(EvidenceDocument.account_uid, EvidenceDocument.category_uid,
            EvidenceDocument.feed_item_uid, db.func.count()).where(EvidenceDocument.active.is_(True),
            EvidenceDocument.kind == 'transaction', tuple_(EvidenceDocument.account_uid,
            EvidenceDocument.category_uid, EvidenceDocument.feed_item_uid).in_(keys)).group_by(
            EvidenceDocument.account_uid, EvidenceDocument.category_uid, EvidenceDocument.feed_item_uid)
        for account, category, item, count in db.session.execute(query):
            result[(account, category, item)] = count
    if identities:
        query = db.select(EvidenceDocument.mileage_id, db.func.count()).where(
            EvidenceDocument.active.is_(True), EvidenceDocument.kind == 'mileage',
            EvidenceDocument.mileage_id.in_(identities)).group_by(EvidenceDocument.mileage_id)
        result.update(dict(db.session.execute(query).all()))
    return result


def missing(row, count):
    return bool(row.evidence_needed_reason and not count and (
        not isinstance(row, ExpenseDeduction) or row.is_tax_deductible))


def request_record(kind, key, *, lock=False):
    parent, fields, _ = evidence.target(kind, key, lock=lock)
    if kind == 'transaction':
        row = db.session.get(ExpenseDeduction, (fields['account_uid'], fields['category_uid'], fields['feed_item_uid']),
            populate_existing=True, with_for_update=lock)
        version = expense_version(parent, row) if row else ''
    else:
        row, version = parent, mileage_version(parent)
    return row, version


def save_request(kind, key, form):
    lock_writes()
    row, version = request_record(kind, key, lock=True)
    if row is None:
        raise NotFound('Link this payment as an expense before requesting additional evidence.')
    if not hmac.compare_digest(version.encode(), form.get('version', '').encode()):
        raise Conflict('This record changed. Reload before updating its evidence request.')
    reason = optional_text(form.get('reason'), field='evidence request reason', maximum=1000)
    if reason and isinstance(row, ExpenseDeduction) and not row.is_tax_deductible:
        raise ValueError('Tracking-only expenses do not need an additional tax evidence request. Documents remain optional.')
    row.evidence_needed_reason = reason or None
    return row
