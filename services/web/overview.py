"""An owner summary from one saved business-data snapshot."""
from collections import defaultdict
from datetime import datetime, timezone

from models import Account, AccountReserve, Invoice, IncomeShift, SyncRun
from services.database.connection import db
from services.invoices.reconciliation import invoice_work_changes
from services.invoices.workflow import invoice_query, settlement, payment_current
from services.tax.checklist import guided_checklist
from services.tax.reserves import current_plan
from services.web.tasks import task_inbox


def saved_overview(session, *, task_filter='all', as_of=None):
    now = as_of or datetime.now(timezone.utc)
    estimate = current_plan(refresh_rules=False, session=session)
    checks = guided_checklist(estimate, as_of=now, session=session)
    earmarks = defaultdict(int)
    for currency, tax, ni in session.execute(db.select(Account.currency, AccountReserve.tax_minor,
            AccountReserve.ni_minor).join(AccountReserve, AccountReserve.account_uid == Account.account_uid)):
        earmarks[currency] += tax + ni
    target = estimate['received_reserve_minor'] if checks['received_ready'] else None
    invoices = defaultdict(lambda: dict(count=0, outstanding=0, review=0))
    saved_invoices = list(session.scalars(invoice_query().where(Invoice.state != 'cancelled').order_by(
        Invoice.issue_date, Invoice.id)))
    shift_ids = {item.shift_id for invoice in saved_invoices for item in invoice.items if item.active and item.shift_id}
    shifts = list(session.scalars(db.select(IncomeShift).where(IncomeShift.id.in_(shift_ids)))) if shift_ids else []
    changed_work = defaultdict(int)
    for row in invoice_work_changes(saved_invoices, shifts):
        changed_work[row['invoice'].id] += 1
    invoice_rows = []
    for invoice in saved_invoices:
        summary = settlement(invoice)
        invoice_rows.append(dict(invoice=invoice, summary=summary, changed_work=changed_work[invoice.id]))
        if invoice.state == 'issued' and (summary['outstanding'] or summary['stale']):
            row = invoices[invoice.currency]
            row['count'] += 1
            row['outstanding'] += summary['outstanding']
            row['review'] += sum(payment.active and not payment_current(payment, invoice) for payment in invoice.payments)
    # Requested/partial imports are not proof of a successful full import.
    last_success = session.scalar(db.select(db.func.max(SyncRun.finished_at)).where(
        SyncRun.status == 'completed', SyncRun.requested_options == {}))
    latest = session.scalar(db.select(SyncRun).order_by(SyncRun.started_at.desc(), SyncRun.run_uid).limit(1))
    latest_full = session.scalar(db.select(SyncRun).where(SyncRun.requested_options == {}).order_by(
        SyncRun.started_at.desc(), SyncRun.run_uid).limit(1))
    invoice_rows.sort(key=lambda row: (not row['summary']['stale'], not row['changed_work'],
                                     not row['summary']['overdue'], row['invoice'].issue_date, row['invoice'].id))
    inbox = task_inbox(session, checks, invoice_rows, last_success=last_success,
                       latest=latest, latest_full=latest_full, now=now, selected=task_filter)
    return dict(checks=checks, target=target, earmarks=dict(sorted(earmarks.items())),
                gap=None if target is None else max(0, target - earmarks.get('GBP', 0)),
                invoices=dict(sorted(invoices.items())), last_success=last_success,
                import_status=latest.status if latest else None, inbox=inbox)
