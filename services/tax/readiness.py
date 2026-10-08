"""Readable, actionable checks; no extra queries or changes to bank records."""
from urllib.parse import quote


def payment_link(payment, endpoint):
    suffix = 'income' if endpoint == 'dashboard.edit_income' else 'classification'
    identity = '/'.join(quote(str(value), safe='') for value in (payment.account_uid, payment.category_uid, payment.feed_item_uid))
    return f'/dashboard/transactions/{identity}/{suffix}'


def add_attention(groups, key, title, url, *, label=None):
    group = groups.setdefault(key, dict(title=title, count=0, url=url, items=[]))
    group['count'] += 1
    # Keep a large import from turning the estimate into an enormous ledger.
    if label and len(group['items']) < 20:
        group['items'].append(dict(label=label, url=url))


def check_income(payment, groups):
    """Return whether this receipt is complete enough for received-income planning."""
    income = payment.income
    label = payment.counterparty_name or 'Unnamed payment'
    complete = (income is not None and not income.needs_review
                and income.recorded_currency == payment.currency == 'GBP'
                and income.income_stream_id is not None and income.gross_minor is not None
                and income.tax_treatment != 'unknown'
                and (income.tax_deducted_minor is not None
                     or income.tax_treatment in ('no_tax_deducted', 'non_taxable')))
    if income and income.tax_treatment == 'non_taxable' and not income.needs_review and income.recorded_currency == payment.currency:
        complete = True
    if not complete:
        add_attention(groups, 'income', 'Income details need completing',
                      payment_link(payment, 'dashboard.edit_income'), label=label)
    return complete
