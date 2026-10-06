"""UK business mileage and employee mileage relief, calculated once per group/year."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from services.tax.mileage_rules import reviewed_rules

LOCATIONS = {'england': 'England', 'wales': 'Wales', 'northern_ireland': 'Northern Ireland', 'scotland': 'Scotland'}
VEHICLES = {'car_van': 'Car / van', 'motorcycle': 'Motorcycle', 'bicycle': 'Bicycle (employees only)'}


def mileage_allowances(entries):
    """Share the 10,000-mile car/van band across vehicles in the same trade/job.

    Related employments share a group. Reimbursements offset the annual approved
    amount, not individual journeys, so overpayments cannot inflate relief.
    """
    rules = reviewed_rules()
    groups, amounts = defaultdict(list), defaultdict(int)
    for entry in entries:
        if entry.location not in rules['locations'] or not rules['starts_on'] <= entry.journey_date.isoformat() <= rules['ends_on']:
            raise ValueError('No reviewed mileage rules for this location and year.')
        employee = entry.income_stream.kind == 'employed'
        if entry.vehicle_type == 'bicycle' and not employee:
            raise ValueError('Self-employed simplified mileage does not cover bicycles.')
        # A stream is one business/job unless the owner explicitly groups related streams.
        group = entry.mileage_group.strip().casefold() or entry.income_stream_id
        groups[(employee, group, entry.vehicle_type)].append(entry)
    for (employee, _, vehicle), journeys in groups.items():
        journeys.sort(key=lambda entry: (entry.journey_date, entry.id))
        used, approved = Decimal(0), defaultdict(Decimal)
        for entry in journeys:
            miles = Decimal(entry.miles)
            if vehicle == 'car_van':
                first = min(miles, max(Decimal(0), Decimal(rules['threshold_miles']) - used))
                allowance = first * rules['car_van_first_pence'] + (miles - first) * rules['car_van_after_pence']
                used += miles
            else:
                allowance = miles * rules['motorcycle_pence' if vehicle == 'motorcycle' else 'employee_bicycle_pence']
            approved[entry.income_stream_id] += allowance
        reimbursement = sum(entry.reimbursed_minor for entry in journeys)
        total = sum(approved.values())
        eligible = max(Decimal(0), total - reimbursement)
        # Allocate remaining relief proportionally; round once and preserve pennies.
        target = int(eligible.quantize(Decimal(1), rounding=ROUND_HALF_UP))
        shares = {key: int(value * target / total) if total else 0 for key, value in approved.items()}
        for key in sorted(shares)[:target - sum(shares.values())]:
            shares[key] += 1
        for key, amount in shares.items():
            amounts[key] += amount
    return dict(amounts)
