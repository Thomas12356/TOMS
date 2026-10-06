"""Income-only annual planning. Gross sets the bands; withholding is a credit.

Amounts are integer pence. Decimal retains half-penny allowance taper values;
round half-up once for the resulting annual tax. No bank data or network calls.
"""
from decimal import Decimal, ROUND_HALF_UP

from services.transactions.income_streams import annual_gross


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


def allocate_payroll_tax(rows, total_tax, total_gross):
    """Share one employment-only allowance across jobs, preserving every penny."""
    if not total_gross:
        return [0] * len(rows)
    amounts = [total_tax * row['gross_minor'] // total_gross for row in rows]
    remainder_order = sorted(range(len(rows)), key=lambda index: -(total_tax * rows[index]['gross_minor'] % total_gross))
    for index in remainder_order[:total_tax - sum(amounts)]:
        amounts[index] += 1
    return amounts


def estimate_streams(streams, rules):
    rows, blockers = [], []
    for stream in streams:
        gross = annual_gross(stream)
        row = dict(stream=stream, gross_minor=gross, withheld_minor=None)
        rows.append(row)
        if gross is None:
            blockers.append(f'{stream.name}: enter expected gross income.')
        elif stream.expected_gross_currency != rules['currency']:
            blockers.append(f'{stream.name}: {stream.expected_gross_currency} needs a reviewed GBP conversion; currencies are not added together.')
        elif stream.withholding_mode == 'unknown' and gross:
            blockers.append(f'{stream.name}: choose how tax is taken automatically.')
        elif stream.withholding_mode == 'manual' and stream.expected_tax_deducted_minor > gross:
            blockers.append(f'{stream.name}: expected tax deductions exceed the forecast; update the deduction amount.')
    result = dict(rows=rows, blockers=blockers, calculation=None, withheld_minor=None,
                  reserve_minor=None, excess_withheld_minor=None, reserve_percent=None,
                  self_managed_gross_minor=None, additional_tax_minor=None)
    if not rows:
        blockers.append('Create an income stream to start your estimate.')
        return result
    # A partial total would hide income and could choose the wrong tax brackets.
    if any(row['gross_minor'] is None or row['stream'].expected_gross_currency != rules['currency'] for row in rows):
        return result
    gross = sum(row['gross_minor'] for row in rows)
    result['calculation'] = income_tax(gross, rules)
    employed = [row for row in rows if row['stream'].kind == 'employed']
    employed_gross = sum(row['gross_minor'] for row in employed)
    employment_tax = income_tax(employed_gross, rules)['tax_minor']
    for row, allocated in zip(employed, allocate_payroll_tax(employed, employment_tax, employed_gross)):
        if row['stream'].withholding_mode == 'paye_estimate':
            row['withheld_minor'] = allocated
    for row in rows:
        mode = row['stream'].withholding_mode
        if mode == 'none' or not row['gross_minor']:
            row['withheld_minor'] = 0
        elif mode == 'manual':
            row['withheld_minor'] = row['stream'].expected_tax_deducted_minor
    result['additional_tax_minor'] = result['calculation']['tax_minor'] - employment_tax
    if blockers:
        return result
    withheld = sum(row['withheld_minor'] for row in rows)
    balance = result['calculation']['tax_minor'] - withheld
    # This is a whole-year planning target, not a balance after money already saved.
    reserve = max(0, balance)
    available = sum(row['gross_minor'] - row['withheld_minor'] for row in rows if row['stream'].kind != 'employed')
    percent = (Decimal(reserve) * 100 / available).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if available else None
    result.update(withheld_minor=withheld, reserve_minor=reserve,
                  excess_withheld_minor=max(0, -balance), self_managed_gross_minor=available,
                  reserve_percent=percent)
    return result
