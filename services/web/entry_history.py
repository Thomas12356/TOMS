"""Validated display filters and internal return context for work/deductions."""
import re
from datetime import date, datetime, time, timedelta

from flask import url_for
from werkzeug.exceptions import BadRequest

from models import IncomeStream
from services.database.connection import db
from services.transactions.shifts import LOCAL_TIME
from services.validation import uid

HISTORY_FIELDS = {'history_page', 'history_source', 'history_from', 'history_to'}


def history_options(options):
    values = {key: value for key, value in options.items() if key in HISTORY_FIELDS}
    page = values.get('history_page', '1')
    if not re.fullmatch(r'[0-9]{1,10}', page) or not 1 <= int(page) <= 2**31 - 1:
        raise BadRequest('Choose a positive history page up to 2147483647.')
    if values.get('history_source'):
        try:
            values['history_source'] = uid(values['history_source'])
        except ValueError:
            raise BadRequest('Choose a valid history income source.') from None
        if db.session.get(IncomeStream, values['history_source']) is None:
            raise BadRequest('History income source not found.')
    for key in ('history_from', 'history_to'):
        if key not in values:
            continue
        try:
            value = date.fromisoformat(values[key])
            if value.isoformat() != values[key] or value.year == 9999:
                raise ValueError()
        except ValueError:
            raise BadRequest('Enter history dates as YYYY-MM-DD.') from None
    if values.get('history_from') and values.get('history_to') and values['history_from'] > values['history_to']:
        raise BadRequest('History end date must follow its start date.')
    return values


def filter_history(query, model, date_column, options, *, timestamp=False):
    if options.get('history_source'):
        query = query.where(model.income_stream_id == options['history_source'])
    for key, lower in (('history_from', True), ('history_to', False)):
        if not options.get(key):
            continue
        value = date.fromisoformat(options[key])
        if timestamp:
            if not lower:
                value += timedelta(days=1)
            value = datetime.combine(value, time.min, LOCAL_TIME)
        condition = date_column >= value if lower else (date_column < value if timestamp else date_column <= value)
        query = query.where(condition)
    return query


def source_return(options, *, expected=None):
    """Return only to a known internal source; never accept arbitrary redirect URLs."""
    if options.get('return_to') != 'source':
        if options.get('source'):
            raise BadRequest('Choose a source return page.')
        return None
    try:
        identity = uid(options.get('source', ''))
    except ValueError:
        raise BadRequest('Choose a valid source return page.') from None
    if expected is not None and identity != expected:
        raise BadRequest('Return to the source for this work.')
    if db.session.get(IncomeStream, identity) is None:
        raise BadRequest('Return income source not found.')
    return url_for('dashboard.income_workspace', source_id=identity)
