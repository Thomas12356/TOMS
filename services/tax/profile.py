"""Owner-confirmed annual tax jurisdiction, independent of travel locations."""
from datetime import datetime, timezone

from werkzeug.exceptions import BadRequest, Conflict

from models import TaxProfile
from services.database.connection import db
from services.database.session import test_data_active
from services.tax.rules import reviewed_rules

JURISDICTIONS = {'unknown': 'Not confirmed', 'england': 'England', 'wales': 'Wales',
                 'northern_ireland': 'Northern Ireland', 'scotland': 'Scotland',
                 'other': 'Other / outside the supported scope'}
SUPPORTED = frozenset({'england', 'wales', 'northern_ireland'})


def current_profile(session):
    year = reviewed_rules()['tax_year']
    row = session.get(TaxProfile, year)
    jurisdiction = row.jurisdiction if row else 'unknown'
    supported = jurisdiction in SUPPORTED and row.confirmed_at is not None
    message = '' if supported else ('Confirm your tax jurisdiction for 2026–27 before using the estimate.'
        if jurisdiction == 'unknown' else
        'Your saved tax jurisdiction is outside the supported England, Wales and Northern Ireland estimate.')
    return dict(tax_year=year, jurisdiction=jurisdiction, label=JURISDICTIONS[jurisdiction],
                revision=row.revision if row else 0, supported=supported, message=message,
                confirmed_at=row.confirmed_at.isoformat() if row and row.confirmed_at else None)


def lock_profile():
    """Share first-insert protection with sample seeding as well as owner saves."""
    db.session.execute(db.select(db.func.pg_advisory_xact_lock(6075157141257144420 + int(test_data_active()))))


def save_profile(form):
    if set(form) - {'csrf_token', 'version', 'jurisdiction', 'confirmed'} or any(len(form.getlist(key)) != 1 for key in form):
        raise BadRequest('Supply each profile field once.')
    jurisdiction = form.get('jurisdiction', '')
    if jurisdiction not in JURISDICTIONS:
        raise BadRequest('Choose a listed tax jurisdiction, or Not confirmed.')
    if form.get('confirmed') not in (None, '1') or (jurisdiction != 'unknown' and form.get('confirmed') != '1'):
        raise BadRequest('Confirm that this is your tax jurisdiction for 2026–27.')
    # Serialise the first insert too; sample and real profiles have separate locks.
    lock_profile()
    year = reviewed_rules()['tax_year']
    row = db.session.get(TaxProfile, year, with_for_update=True, populate_existing=True)
    revision = row.revision if row else 0
    if form.get('version') != str(revision):
        raise Conflict('This tax profile changed. Reload the current profile before saving again.')
    if revision >= 2**63 - 1:
        raise Conflict('This tax profile has reached its revision limit.')
    if row is None:
        row = TaxProfile(tax_year=year)
        db.session.add(row)
    row.jurisdiction, row.revision = jurisdiction, revision + 1
    row.confirmed_at = datetime.now(timezone.utc) if jurisdiction != 'unknown' else None
