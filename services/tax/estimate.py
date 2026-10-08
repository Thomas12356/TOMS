"""Income tax and standard Class 4 annual planning. Gross sets the bands; withholding is a credit.

Amounts are integer pence. Decimal retains half-penny allowance taper values;
round half-up once for the resulting annual tax. No bank data or network calls.
"""
from decimal import Decimal, ROUND_HALF_UP

from services.transactions.income_streams import annual_gross
from services.tax.national_insurance import class4, reviewed_rules as ni_rules


def rounded_minor(value):
    """Round displayed tax money to the nearest penny, with halves up."""
    return int(Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def income_tax(gross_minor, rules):
    if type(gross_minor) is not int or gross_minor < 0:
        raise ValueError('Gross income must be nonnegative integer pence.')
    allowance = max(Decimal(0), Decimal(rules['personal_allowance_minor']) -
                    Decimal(max(0, gross_minor - rules['allowance_taper_starts_minor'])) / 2)
    taxable = max(Decimal(0), Decimal(gross_minor) - allowance)
    lower, tax, bands = Decimal(0), Decimal(0), []
    for band in rules['taxable_bands']:
        upper = taxable if band['upper_minor'] is None else Decimal(band['upper_minor'])
        portion = max(Decimal(0), min(taxable, upper) - lower)
        amount = portion * Decimal(band['rate_percent']) / 100
        bands.append(dict(rate=band['rate_percent'], taxable_minor=portion, tax_minor=amount))
        tax += amount
        lower = upper
    return dict(gross_minor=gross_minor, allowance_minor=allowance, taxable_minor=taxable,
                tax_minor=rounded_minor(tax), bands=bands)


def estimate_streams(streams, rules, credits=None, deductions=None, issues=None, *, received=None,
                     ni_paid=None, attention=None, received_issues=None):
    """Gross forecasts choose bands; only current, logged records supply credits."""
    credits, deductions = credits or {}, deductions or {}
    received, ni_paid = received or {}, ni_paid or {}
    received_issues = list(received_issues or [])
    rows, blockers = [], list(issues or [])
    result = dict(rows=rows, blockers=blockers, calculation=None, withheld_minor=None,
                  reserve_minor=None, uncovered_minor=None, excess_withheld_minor=None,
                  reserve_percent=None, self_managed_gross_minor=None, additional_tax_minor=None,
                  gross_minor=None, deductions_minor=sum(deductions.values()), ni_minor=None,
                  total_liability_minor=None, ni_paid_minor=sum(ni_paid.values()),
                  received_gross_minor=None, received_reserve_minor=None,
                  received_issues=received_issues, attention=attention or [], mixed_ni=False, ni_rules=ni_rules())
    for stream in streams:
        gross = annual_gross(stream)
        expense = deductions.get(stream.id, 0)
        rows.append(dict(stream=stream, gross_minor=gross, withheld_minor=credits.get(stream.id, 0),
                         deductions_minor=expense, profit_minor=max(0, (gross or 0) - expense)))
        if gross is None:
            blockers.append(f'{stream.name}: enter expected gross income.')
        elif stream.expected_gross_currency != rules['currency']:
            blockers.append(f'{stream.name}: a reviewed GBP conversion is required.')
        elif expense > gross:
            blockers.append(f'{stream.name}: deductions exceed the forecast. Loss relief is not supported; update the forecast.')
    known = {row['stream'].id for row in rows}
    if set(credits) - known or set(deductions) - known:
        blockers.append('Some tax credits or deductions belong to streams outside this tax year; check their assignments.')
    if not rows:
        blockers.append('Create an income stream to start your estimate.')
        return result
    if any(row['gross_minor'] is None or row['stream'].expected_gross_currency != rules['currency'] for row in rows):
        return result
    gross = sum(row['gross_minor'] for row in rows)
    profit = sum(row['profit_minor'] for row in rows)
    calculation = income_tax(profit, rules)
    employed_profit = sum(row['profit_minor'] for row in rows if row['stream'].kind == 'employed')
    employment_tax = income_tax(employed_profit, rules)['tax_minor']
    business_profit = sum(row['profit_minor'] for row in rows if row['stream'].kind != 'employed')
    ni = class4(business_profit, rules['tax_year'])
    liability = calculation['tax_minor'] + ni
    withheld = sum(credits.values())
    result.update(gross_minor=gross, calculation=calculation, withheld_minor=withheld,
                  ni_minor=ni, total_liability_minor=liability, mixed_ni=employed_profit > 0 and business_profit > 0,
                  uncovered_minor=max(0, liability - withheld),
                  excess_withheld_minor=max(0, withheld - liability),
                  additional_tax_minor=calculation['tax_minor'] - employment_tax)
    if blockers:
        return result
    business = [row for row in rows if row['stream'].kind != 'employed']
    business_credits = sum(row['withheld_minor'] for row in business)
    business_charge = result['additional_tax_minor'] + ni
    reserve = max(0, business_charge - business_credits)
    available = max(0, sum(row['gross_minor'] for row in business) - business_credits)
    percent = (Decimal(reserve) * 100 / available).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if available else None
    forecast_business_gross = sum(row['gross_minor'] for row in business)
    received_business_gross = sum(received.get(row['stream'].id, 0) for row in business)
    for row in rows:
        if received.get(row['stream'].id, 0) > row['gross_minor']:
            received_issues.append(f"{row['stream'].name}: received income exceeds the forecast; update the forecast.")
    if set(received) - known:
        received_issues.append('Some records belong to a stream outside this tax year; check their stream assignment.')
    result['received_gross_minor'] = received_business_gross
    if not received_issues:
        # Allocate the forecast charge BEFORE credits, then subtract actual credits once.
        # This avoids crediting the same CIS deduction in both the rate and the receipts.
        earned_share = (Decimal(business_charge) * received_business_gross / forecast_business_gross
                        if forecast_business_gross else Decimal(0))
        result['received_reserve_minor'] = max(0, rounded_minor(earned_share) - business_credits)
    result.update(reserve_minor=reserve, self_managed_gross_minor=available, reserve_percent=percent)
    return result
