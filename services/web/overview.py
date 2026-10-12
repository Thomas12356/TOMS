"""An owner summary from one saved business-data snapshot."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import lazyload

from models import Account, AccountReserve, Invoice, IncomeShift, SyncRun
from services.database.connection import db
from services.invoices.reconciliation import invoice_work_changes
from services.invoices.workflow import invoice_query, settlement, payment_current
from services.tax.checklist import guided_checklist, payment_review_steps, review_payment
from services.tax.reserves import current_plan
from services.tax.records import planning_payments
from services.tax.rules import reviewed_rules
from services.web.tasks import task_inbox, LIMIT


def saved_overview(session, *, task_filter='all', as_of=None):
    now = as_of or datetime.now(timezone.utc)
    rules = reviewed_rules()
    first = datetime.fromisoformat(rules['starts_on']).replace(tzinfo=ZoneInfo('Europe/London'))
    end = (datetime.fromisoformat(rules['ends_on']) + timedelta(days=1)).replace(tzinfo=ZoneInfo('Europe/London'))
    steps = payment_review_steps()

    def observed_payments():
        for payment in planning_payments(rules, session, stream=True):
            review_payment(payment, steps, first, end, now)
            yield payment

    estimate = current_plan(refresh_rules=False, session=session, payments=observed_payments(), as_of=now)
    checks = guided_checklist(estimate, as_of=now, session=session, payment_steps=steps)
    earmarks = defaultdict(int)
    for currency, tax, ni in session.execute(db.select(Account.currency, AccountReserve.tax_minor,
            AccountReserve.ni_minor).join(AccountReserve, AccountReserve.account_uid == Account.account_uid)):
        earmarks[currency] += tax + ni
    target = estimate['received_reserve_minor'] if checks['received_ready'] else None
    invoices = defaultdict(lambda: dict(count=0, outstanding=0, review=0))
    invoice_rows = []
    invoice_count = 0
    order = lambda row: (not row['summary']['stale'], not row['changed_work'],
                         not row['summary']['overdue'], row['invoice'].issue_date, row['invoice'].id)
    query = invoice_query().options(lazyload(Invoice.deliveries)).where(Invoice.state != 'cancelled').order_by(
        Invoice.issue_date, Invoice.id).execution_options(yield_per=200)
    for batch in session.scalars(query).partitions():
        shift_ids = {item.shift_id for invoice in batch for item in invoice.items if item.active and item.shift_id}
        shifts = list(session.scalars(db.select(IncomeShift).where(IncomeShift.id.in_(shift_ids)))) if shift_ids else []
        changed_work = defaultdict(int)
        for row in invoice_work_changes(batch, shifts):
            changed_work[row['invoice'].id] += 1
        for invoice in batch:
            payment_states = {payment.id: payment_current(payment, invoice) for payment in invoice.payments if payment.active}
            summary = settlement(invoice, payment_states=payment_states)
            changed = changed_work[invoice.id]
            if summary['stale'] or changed or summary['overdue']:
                invoice_count += 1
                invoice_rows.append(dict(invoice=invoice, summary=summary, changed_work=changed))
                invoice_rows.sort(key=order)
                del invoice_rows[LIMIT:]
            if invoice.state == 'issued' and (summary['outstanding'] or summary['stale']):
                row = invoices[invoice.currency]
                row['count'] += 1
                row['outstanding'] += summary['outstanding']
                row['review'] += sum(not valid for valid in payment_states.values())
    # Requested/partial imports are not proof of a successful full import.
    last_success = session.scalar(db.select(db.func.max(SyncRun.finished_at)).where(
        SyncRun.status == 'completed', SyncRun.requested_options == {}))
    latest = session.scalar(db.select(SyncRun).order_by(SyncRun.started_at.desc(), SyncRun.run_uid).limit(1))
    latest_full = session.scalar(db.select(SyncRun).where(SyncRun.requested_options == {}).order_by(
        SyncRun.started_at.desc(), SyncRun.run_uid).limit(1))
    inbox = task_inbox(session, checks, invoice_rows, last_success=last_success,
                       latest=latest, latest_full=latest_full, now=now, selected=task_filter, invoice_count=invoice_count)
    return dict(checks=checks, target=target, earmarks=dict(sorted(earmarks.items())),
                gap=None if target is None else max(0, target - earmarks.get('GBP', 0)),
                invoices=dict(sorted(invoices.items())), last_success=last_success,
                import_status=latest.status if latest else None, inbox=inbox)
