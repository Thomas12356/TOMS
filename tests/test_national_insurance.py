"""NI boundaries, shared profits, actual payroll and received-income targets."""
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from werkzeug.exceptions import BadRequest

from services.tax.estimate import estimate_streams
from services.tax.national_insurance import class4, reviewed_rules as ni_rules
from services.tax.ni_rules import validate_publication, refresh_rules, cached_status
from services.tax.rules import reviewed_rules
from services.transactions.income import income_body, validate_reconciliation
from test_tax_estimate import stream


def publication():
    return dict(base_path='/self-employed-national-insurance-rates', details=dict(body='''
        <p>For tax year 2026 to 2027</p>
        <p>6% on profits over £12,570 up to £50,270</p><p>2% on profits over £50,270</p>
        <p>If your profits are £7,105 or more a year</p>
        <p>you do not have to pay Class 2 contributions</p><p>£3.65 a week</p>'''))


class NationalInsuranceTests(unittest.TestCase):
    def test_class4_thresholds_and_penny_rounding(self):
        for gross, expected in ((0, 0), (1257000, 0), (1257008, 0), (1257009, 1),
                                (3000000, 104580), (5027000, 226200), (6000000, 245660)):
            self.assertEqual(class4(gross, '2026-27'), expected)
        for invalid in (-1, True, 1.5, '100'):
            with self.assertRaises(ValueError):
                class4(invalid, '2026-27')
        with self.assertRaises(ValueError):
            class4(100, '2027-28')

    def test_ni_combines_all_business_profits_once_excluding_employment(self):
        job, first, second = stream(9000000, 'employed'), stream(2000000), stream(1000000, 'cis')
        result = estimate_streams([job, first, second], reviewed_rules(), deductions={second.id: 100000})
        self.assertEqual(result['ni_minor'], 98580)  # 6% of £29,000 - £12,570
        self.assertTrue(result['mixed_ni'])
        self.assertEqual(estimate_streams([job], reviewed_rules())['ni_minor'], 0)

    def test_received_target_allocates_charge_before_actual_cis_credit(self):
        work = stream(3000000, 'cis')
        result = estimate_streams([work], reviewed_rules(), received={work.id: 1500000}, credits={work.id: 100000})
        self.assertEqual(result['total_liability_minor'], 453180)
        self.assertEqual(result['reserve_minor'], 353180)
        self.assertEqual(result['received_reserve_minor'], 126590)
        fully_received = estimate_streams([work], reviewed_rules(), received={work.id: 3000000}, credits={work.id: 100000})
        self.assertEqual(fully_received['received_reserve_minor'], fully_received['reserve_minor'])

    def test_received_unknown_or_above_forecast_never_looks_ready(self):
        work = stream(3000000)
        for kwargs in ({'received_issues': ['Confirm income']}, {'received': {work.id: 3000001}},
                       {'received': {'unknown-stream': 100}}):
            result = estimate_streams([work], reviewed_rules(), **kwargs)
            self.assertIsNone(result['received_reserve_minor'])
            self.assertTrue(result['received_issues'])
        self.assertEqual(estimate_streams([work], reviewed_rules())['received_reserve_minor'], 0)

    def test_logged_class1_is_not_an_income_tax_or_class4_credit(self):
        job, work = stream(4000000, 'employed'), stream(2000000)
        base = estimate_streams([job, work], reviewed_rules())
        logged = estimate_streams([job, work], reviewed_rules(), ni_paid={job.id: 219440})
        for field in ('reserve_minor', 'withheld_minor', 'ni_minor', 'uncovered_minor'):
            self.assertEqual(logged[field], base[field])
        self.assertEqual(logged['ni_paid_minor'], 219440)

    def test_payroll_reconciles_with_ni_without_legacy_adjustment_inference(self):
        values = income_body(dict(income_type='employment', tax_treatment='paye',
            gross_minor=333333, tax_deducted_minor=45717, ni_deducted_minor=7616))
        validate_reconciliation(values, 280000)
        with self.assertRaises(BadRequest):
            validate_reconciliation(values, 287616)
        legacy = income_body(dict(income_type='employment', tax_treatment='paye',
            gross_minor=333333, tax_deducted_minor=45717, adjustment_minor=-7616,
            adjustment_notes='Existing payroll adjustment'))
        self.assertIsNone(legacy['ni_deducted_minor'])
        validate_reconciliation(legacy, 280000)

    def test_invalid_or_excessive_ni_is_rejected(self):
        base = dict(income_type='employment', tax_treatment='paye', gross_minor=10000, tax_deducted_minor=2000)
        for value in (-1, True, 1.5, '100', 2**63, 8001):
            with self.assertRaises(BadRequest):
                income_body(dict(base, ni_deducted_minor=value))
        with self.assertRaises(BadRequest):
            income_body(dict(base, income_type='other', ni_deducted_minor=100))

    def test_official_source_changes_block_validation(self):
        rules = ni_rules()
        self.assertEqual(len(validate_publication(publication(), rules)), 64)
        for old, new in (('2026 to 2027', '2027 to 2028'), ('6%', '8%'), ('6%', '16%'), ('2%', '12%'), ('£50,270', '£51,000'),
                         ('£3.65', '£3.75'), ('£7,105', '£7,200')):
            payload = publication()
            payload['details']['body'] = payload['details']['body'].replace(old, new)
            with self.assertRaises(ValueError):
                validate_publication(payload, rules)
        with self.assertRaises(ValueError):
            validate_publication(dict(publication(), withdrawn_notice={'explanation': 'withdrawn'}), rules)

    def test_rule_cache_checks_once_and_retains_reviewed_rules_on_change(self):
        with TemporaryDirectory() as directory, patch('services.tax.ni_rules.fetch_publication', return_value=publication()) as fetch:
            before = ni_rules()
            self.assertEqual(refresh_rules(directory)['status'], 'verified')
            self.assertEqual(refresh_rules(directory)['status'], 'verified')
            self.assertEqual(fetch.call_count, 1)
            fetch.return_value = {}
            self.assertEqual(refresh_rules(directory, force=True)['status'], 'needs_review')
            self.assertEqual(cached_status(directory)['status'], 'needs_review')
            self.assertEqual(ni_rules(), before)


    def test_credits_for_an_unknown_year_stream_cannot_reduce_ready_target(self):
        work = stream(3000000)
        result = estimate_streams([work], reviewed_rules(), credits={'other-year': 100000})
        self.assertTrue(result['blockers'])
        self.assertIsNone(result['reserve_minor'])
        self.assertIsNone(result['received_reserve_minor'])
