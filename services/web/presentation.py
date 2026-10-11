"""Money and local dates for browser pages; stored timestamps remain UTC."""
from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_TIME = ZoneInfo('Europe/London')


def format_amount(amount_minor, currency):
    if amount_minor is None:
        return 'Unavailable'
    if currency in ('GBP', 'EUR', 'USD'):
        sign = '−' if amount_minor < 0 else ''
        amount_minor = abs(amount_minor)
        return f'{sign}{currency} {amount_minor // 100:,}.{amount_minor % 100:02d}'
    return f'{currency} {amount_minor:,} minor units'


def local_date(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('A timezone-aware timestamp is required.')
    return value.astimezone(LOCAL_TIME)


def date_display(value, *, now=None):
    local = local_date(value)
    today = local_date(now or datetime.now(LOCAL_TIME)).date()
    return dict(date=local.strftime('%d %b %Y'),
                day='Today' if local.date() == today else local.strftime('%A'))
