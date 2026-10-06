"""One stream setting connects related mileage; journeys need no group field."""
from uuid import uuid4

from werkzeug.exceptions import BadRequest

from models import IncomeStream
from services.database.connection import db
from services.validation import uid


def set_mileage_relationship(stream, value):
    """Caller holds the shared deduction write lock; changing one stream moves it."""
    if not value:
        # A separate identifier prevents other streams following a moving root.
        stream.mileage_pool_id = str(uuid4())
        return
    try:
        identity = uid(value)
    except ValueError:
        raise BadRequest('Choose an existing business or employer.') from None
    if identity == stream.id:
        raise BadRequest('Choose another stream, or a separate business or employer.')
    other = db.session.scalar(db.select(IncomeStream).where(IncomeStream.id == identity)
        .with_for_update().execution_options(populate_existing=True))
    if other is None:
        raise BadRequest('The selected income stream no longer exists.')
    if (other.kind == 'employed') != (stream.kind == 'employed'):
        raise BadRequest('Employment mileage cannot be combined with self-employed or CIS mileage.')
    stream.mileage_pool_id = other.mileage_pool_id


def mileage_partners(streams):
    """Summaries and default selections without additional database queries."""
    pools = {}
    for stream in streams:
        key = getattr(stream, 'mileage_pool_id', None) or stream.id
        pools.setdefault(key, []).append(stream)
    return {stream.id: [other for other in pools[getattr(stream, 'mileage_pool_id', None) or stream.id]
                        if other.id != stream.id] for stream in streams}
