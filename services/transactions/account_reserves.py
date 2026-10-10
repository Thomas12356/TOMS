"""Attach owner earmarks to live balances without conflating them with tax targets."""
from collections import defaultdict

from models import AccountReserve
from services.database.connection import db


def balance_breakdown(payload, accounts, format_amount):
    reserves = {row.account_uid: row for row in db.session.scalars(db.select(AccountReserve))}
    totals = defaultdict(lambda: [0, 0, 0, False])
    for card, account in zip(payload['balances'], accounts):
        row = reserves.get(account.account_uid)
        tax, ni = (row.tax_minor, row.ni_minor) if row else (0, 0)
        balance = card.pop('minor_units', None)
        card.update(account=account.account_uid, currency=account.currency,
                    tax_reserved=format_amount(tax, account.currency), ni_reserved=format_amount(ni, account.currency),
                    unassigned=format_amount(balance - tax - ni, account.currency) if balance is not None else None,
                    underfunded=balance is not None and balance < tax + ni)
        total = totals[account.currency]
        total[0] += tax
        total[1] += ni
        total[2] += balance or 0
        total[3] |= balance is None
    if 'totals' in payload:
        for card, currency in zip(payload['totals'], sorted(totals)):
            tax, ni, balance, unavailable = totals[currency]
            card.update(currency=currency, tax_reserved=format_amount(tax, currency), ni_reserved=format_amount(ni, currency),
                        unassigned=None if unavailable else format_amount(balance - tax - ni, currency),
                        underfunded=not unavailable and balance < tax + ni)
    return payload
