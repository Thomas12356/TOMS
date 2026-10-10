"""Use one planning calculation for the tax page, balances and income notices."""
from decimal import Decimal

from flask import current_app

from models import IncomeStream
from services.database.connection import db
from services.tax import ni_rules, rules as tax_rule_service
from services.tax.estimate import estimate_streams, rounded_minor
from services.tax import records as tax_record_service
from services.tax.rules import cached_status, reviewed_rules
from services.transactions.shifts import attach_shift_totals


def current_plan():
    tax_rule_service.start_rule_check(current_app.instance_path)
    rules = reviewed_rules()
    streams = db.session.scalars(db.select(IncomeStream).where(
        IncomeStream.forecast_tax_year == rules['tax_year']).order_by(IncomeStream.name, IncomeStream.id)).all()
    attach_shift_totals(streams)
    records = tax_record_service.tax_records(rules)
    records.setdefault('issues', [])
    if cached_status(current_app.instance_path).get('status') == 'needs_review':
        records['issues'].append('Income-tax rules changed and need review.')
    if ni_rules.cached_status(current_app.instance_path).get('status') == 'needs_review':
        records['issues'].append('National Insurance rules changed and need review.')
    return estimate_streams(streams, rules, **records)


def split_reserve(tax_minor, ni_minor, credits_minor):
    """Actual CIS credits cover the combined Self Assessment charge once."""
    return (max(0, tax_minor - credits_minor),
            max(0, ni_minor - max(0, credits_minor - tax_minor)))


def earned_reserve(estimate, gross_received, credits_minor):
    """Apportion the forecast business charge, then apply actual credits once."""
    business = [row for row in estimate['rows'] if row['stream'].kind != 'employed']
    gross = sum(row['gross_minor'] for row in business)
    share = Decimal(gross_received) / gross if gross else Decimal(0)
    # Round the combined charge once, matching the existing received-income plan.
    total = rounded_minor((estimate['additional_tax_minor'] + estimate['ni_minor']) * share)
    tax = rounded_minor(estimate['additional_tax_minor'] * share)
    return split_reserve(tax, total - tax, credits_minor)


def received_split(estimate):
    if estimate['received_reserve_minor'] is None or estimate['blockers'] or estimate['received_issues']:
        return None
    business = [row for row in estimate['rows'] if row['stream'].kind != 'employed']
    return earned_reserve(estimate, estimate['received_gross_minor'], sum(row['withheld_minor'] for row in business))
