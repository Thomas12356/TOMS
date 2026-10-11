"""An owner summary from one saved business-data snapshot."""
from collections import defaultdict

from models import Account, AccountReserve, Invoice, SyncRun
from services.database.connection import db
from services.invoices.workflow import invoice_query, settlement
from services.tax.checklist import guided_checklist
from services.tax.reserves import current_plan


def saved_overview(session):
    estimate = current_plan(refresh_rules=False, session=session)
    checks = guided_checklist(estimate, session=session)
    earmarks = defaultdict(int)
    for currency, tax, ni in session.execute(db.select(Account.currency, AccountReserve.tax_minor,
            AccountReserve.ni_minor).join(AccountReserve, AccountReserve.account_uid == Account.account_uid)):
        earmarks[currency] += tax + ni
    target = estimate['received_reserve_minor'] if checks['received_ready'] else None
    invoices = defaultdict(lambda: dict(count=0, outstanding=0, review=0))
    for invoice in session.scalars(invoice_query().where(Invoice.state == 'issued')):
        summary = settlement(invoice)
        if summary['outstanding'] or summary['stale']:
            row = invoices[invoice.currency]
            row['count'] += 1
            row['outstanding'] += summary['outstanding']
            row['review'] += summary['stale']
    # Requested/partial imports are not proof of a successful full import.
    last_success = session.scalar(db.select(db.func.max(SyncRun.finished_at)).where(
        SyncRun.status == 'completed', SyncRun.requested_options == {}))
    latest = session.scalar(db.select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))
    return dict(checks=checks, target=target, earmarks=dict(sorted(earmarks.items())),
                gap=None if target is None else max(0, target - earmarks.get('GBP', 0)),
                invoices=dict(sorted(invoices.items())), last_success=last_success,
                import_status=latest.status if latest else None)
