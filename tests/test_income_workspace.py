"""Owner income dialogs, optional deductions, payment notices and bank earmarks."""
from datetime import datetime, timezone
import re
from unittest.mock import patch
from uuid import uuid4

from werkzeug.datastructures import MultiDict

from models import AccountReserve, IncomeStream, Transaction, TransactionIncome
from services.notifications.income import notices
from services.tax.reserves import current_plan, received_split
from services.tax.records import tax_records
from services.tax.rules import reviewed_rules
from routes.dashboard import format_amount
from deduction_support import DeductionOwnerCase
from support import ApiTestCase


class WorkspaceAuthTests(ApiTestCase):
    def test_owner_only_even_with_api_key(self):
        from app import app
        with patch.dict(app.config, SECRET_KEY='a' * 64):
            for path in ('/dashboard/income-notifications', '/dashboard/income-notifications/read', '/dashboard/account-reserves', '/dashboard/income-streams/' + str(uuid4())):
                method = self.client.get if path.endswith('income-notifications') or '/income-streams/' in path else self.client.post
                for headers in ({}, self.headers):
                    self.assertEqual(method(path, headers=headers).status_code, 302)


class IncomeWorkspaceTests(DeductionOwnerCase):
    def test_workspace_settings_return_context_survives_stale_edits(self):
        from support import stream_token
        path = '/dashboard/income-streams?configure=' + self.stream + '&workspace=1'
        data = dict(csrf_token=self.csrf(), version=stream_token(self.client, self.stream),
            action='update', stream_id=self.stream, name='Workspace renamed', kind='self_employed',
            expected_gross='30000', expected_gross_period='yearly', expected_gross_currency='GBP',
            income_mode='forecast', forecast_tax_year='2026-27', unpaid_holiday='0', unpaid_holiday_unit='weeks')
        saved = self.client.post(path, data=data)
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(saved.location, '/dashboard/income-streams/' + self.stream)
        stale = self.client.post(path, data=data)
        self.assertEqual(stale.status_code, 409)
        self.assertIn('workspace=1', stale.get_data(as_text=True))
        self.assertEqual(self.client.get('/dashboard/income-streams?workspace=1').status_code, 400)
        self.assertEqual(self.client.get(path.replace('workspace=1', 'workspace=https://example.com')).status_code, 400)

    def test_workspace_previews_are_bounded_and_never_include_another_source(self):
        from contextlib import contextmanager
        @contextmanager
        def snapshot():
            yield self.session
        other = IncomeStream(id=str(uuid4()), name='Different employer', kind='employed')
        self.session.add(other)
        for index in range(7):
            self.session.add(Transaction(account_uid=self.account, category_uid=self.category,
                feed_item_uid=str(uuid4()), amount_minor=5500, currency='GBP', direction='IN', status='SETTLED',
                transaction_time=datetime(2026, 5, index + 1, tzinfo=timezone.utc),
                source_updated_at=datetime(2026, 5, index + 1, tzinfo=timezone.utc), source='FASTER_PAYMENTS_IN',
                counterparty_name='Unrelated receipt' if index == 6 else f'Workspace receipt {index}', raw_payload={},
                income=TransactionIncome(income_stream_id=other.id if index == 6 else self.stream,
                    income_type='other', tax_treatment='no_tax_deducted', gross_minor=5500,
                    recorded_currency='GBP', adjustment_minor=0, needs_review=False)))
        self.session.commit()
        with patch('services.tax.export.export_session', snapshot):
            html = self.client.get('/dashboard/income-streams/' + self.stream).get_data(as_text=True)
        self.assertNotIn('Unrelated receipt', html)
        self.assertNotIn('Workspace receipt 0', html)
        for index in range(1, 6):
            self.assertIn(f'Workspace receipt {index}', html)
        self.bank.assert_not_called()

    def test_staged_creation_keeps_forecast_unknown_and_continues_to_workspace(self):
        response = self.client.post('/dashboard/income-streams?setup=1', data=dict(
            csrf_token=self.csrf(), action='start', name='New roofing work', kind='self_employed'))
        self.assertEqual(response.status_code, 303)
        source_id = response.location.rsplit('/', 1)[1]
        source = self.session.get(IncomeStream, source_id)
        self.assertIsNone(source.expected_gross_minor)
        self.assertIsNone(source.expected_gross_period)
        self.assertIsNone(source.expected_gross_currency)
        self.assertEqual(source.name, 'New roofing work')
        self.assertIn('New roofing work: enter expected gross income.', current_plan(refresh_rules=False)['blockers'])
        from contextlib import contextmanager
        @contextmanager
        def snapshot():
            yield self.session
        with patch('services.tax.export.export_session', snapshot):
            workspace = self.client.get(response.location)
        self.assertEqual(workspace.status_code, 200)
        self.assertIn('Expected income is unknown', workspace.get_data(as_text=True))
        self.assertNotIn('>Open work records</a>', workspace.get_data(as_text=True))
        self.bank.assert_not_called()

    def test_staged_validation_retains_name_without_opening_advanced_dialog(self):
        response = self.client.post('/dashboard/income-streams?setup=1', data=dict(
            csrf_token=self.csrf(), action='start', name='Roofing <script>', kind='invalid'))
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn('Roofing &lt;script&gt;', html)
        self.assertIn('1. Name your income source', html)
        self.assertNotIn('data-reopen="true"', html)
        rejected = self.client.post('/dashboard/income-streams', data=dict(
            csrf_token=self.csrf(), action='start', name='Test', kind='employed', expected_gross='0'))
        self.assertEqual(rejected.status_code, 400)

    def test_source_workspace_is_owner_only_bounded_and_separates_evidence(self):
        from contextlib import contextmanager
        @contextmanager
        def snapshot():
            yield self.session
        self.receipt.counterparty_name = '<script>receipt</script>'
        self.session.commit()
        with patch('services.tax.export.export_session', snapshot), patch('services.tax.rules.start_rule_check') as rules:
            response = self.client.get('/dashboard/income-streams/' + self.stream)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('&lt;script&gt;receipt&lt;/script&gt;', html)
            self.assertIn('not added together as extra income', html)
            self.assertIn('Latest five assigned receipts', html)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(self.client.get('/dashboard/income-streams/' + str(uuid4())).status_code, 404)
            self.assertEqual(self.client.get('/dashboard/income-streams/' + self.stream + '?stream=other').status_code, 400)
            rules.assert_not_called()
        self.bank.assert_not_called()

    def setUp(self):
        super().setUp()
        self.enterContext(patch('services.tax.reserves.cached_status', return_value={'status': 'verified'}))
        self.enterContext(patch('services.tax.ni_rules.cached_status', return_value={'status': 'verified'}))
        self.receipt = self.session.get(Transaction, (self.account, self.category, self.incoming))
        self.receipt.source = 'FASTER_PAYMENTS_IN'
        self.receipt.transaction_time = datetime(2026, 4, 12, 12, tzinfo=timezone.utc)
        self.receipt.amount_minor = 100000
        self.receipt.confirmed_at = datetime.now(timezone.utc)
        self.receipt.income = TransactionIncome(income_stream_id=self.stream, income_type='other',
            gross_minor=100000, tax_deducted_minor=0, tax_treatment='no_tax_deducted',
            recorded_currency='GBP', adjustment_minor=0, needs_review=False)
        self.session.commit()

    def csrf(self):
        return re.search(r'name="csrf_token" value="([^"]+)"', self.html())[1]

    def notice_rows(self):
        return notices(current_plan(), format_amount)

    def acknowledge(self, row=None, **changes):
        row = row or self.notice_rows()[0]
        data = dict(csrf_token=self.csrf(), payment=row['payment'], version=row['version'])
        data.update(changes)
        return self.client.post('/dashboard/income-notifications/read', data=data)

    def earmark(self, **changes):
        data = dict(csrf_token=self.csrf(), account=self.account, version='0', tax='100', ni='20')
        data.update(changes)
        return self.client.post('/dashboard/account-reserves', data=data)

    def test_stream_actions_and_popup_filtering(self):
        html = self.client.get('/dashboard/income-streams').get_data(as_text=True)
        self.assertIn('>Add work</a>', html)
        self.assertIn('>Configure stream</a>', html)
        self.assertIn('>Show all income</a>', html)
        self.assertIn('>View deductions and expenses</a>', html)
        self.assertNotIn('<summary>Edit stream settings', html)
        self.assertIn('id="income-payments-dialog"', html)
        self.assertIn('id="account-reserves"', html)
        second = IncomeStream(id=str(uuid4()), name='Other employer', kind='employed',
            expected_gross_minor=1000000, expected_gross_period='yearly', expected_gross_currency='GBP')
        self.session.add(second)
        self.session.commit()
        html = self.client.get('/dashboard/income-streams?view=income&stream='+second.id).get_data(as_text=True)
        self.assertIn('No recorded income here yet.', html)
        self.assertIn('>Add overtime</a>', html)
        self.assertEqual(self.client.get('/dashboard/income-streams?view=invalid').status_code, 400)
        self.assertEqual(self.client.get('/dashboard/income-streams?configure='+str(uuid4())).status_code, 404)

    def test_tracking_expense_never_reduces_tax_or_blocks_when_changed(self):
        self.assertEqual(self.expense(tax_status='tracking', eligible='').status_code, 303)
        self.assertFalse(self.payment().expense.is_tax_deductible)
        self.assertEqual(tax_records(reviewed_rules())['deductions'], {})
        self.payment().amount_minor += 1
        self.session.commit()
        records = tax_records(reviewed_rules())
        self.assertFalse(records['issues'])
        html = self.client.get('/dashboard/income-streams?view=deductions&stream='+self.stream).get_data(as_text=True)
        self.assertIn('Tracking only', html)
        self.assertIn('Business tools', html)
        self.assertIn('Needs review · bank payment changed.', html)

    def test_explicit_tax_choice_and_eligibility_can_change(self):
        self.assertEqual(self.expense(tax_status='tracking', eligible='').status_code, 303)
        self.assertEqual(self.expense(tax_status='deduction', eligible='').status_code, 400)
        self.assertFalse(self.payment().expense.is_tax_deductible)
        self.assertEqual(self.expense(tax_status='deduction', eligible='1').status_code, 303)
        self.assertEqual(tax_records(reviewed_rules())['deductions'][self.stream], 1000)
        self.assertEqual(self.expense(tax_status='tracking', eligible='').status_code, 303)
        self.assertEqual(tax_records(reviewed_rules())['deductions'], {})
        self.assertEqual(self.expense(tax_status='made-up').status_code, 400)

    def test_tracking_vehicle_cost_does_not_exclude_mileage(self):
        self.assertEqual(self.expense(tax_status='tracking', eligible='', category='vehicle_running', vehicle_key='AB12 CDE').status_code, 303)
        self.assertEqual(self.mileage().status_code, 303)
        self.assertEqual(self.expense(tax_status='deduction', category='vehicle_running', vehicle_key='AB12 CDE').status_code, 400)

    def test_non_gbp_tracking_is_allowed_but_cannot_be_claimed(self):
        self.payment().currency = 'EUR'
        self.session.commit()
        self.assertEqual(self.expense(tax_status='tracking', eligible='').status_code, 303)
        self.assertEqual(self.expense(tax_status='deduction').status_code, 400)
        self.assertEqual(tax_records(reviewed_rules())['deductions'], {})
        # Even an inconsistent record inserted outside the forms cannot treat
        # foreign minor units as GBP when calculating tax.
        self.payment().expense.is_tax_deductible = True
        self.session.commit()
        records = tax_records(reviewed_rules())
        self.assertEqual(records['deductions'], {})
        self.assertTrue(records['issues'])

    def test_notices_use_annual_bands_and_actual_withholding(self):
        row = self.notice_rows()[0]
        self.assertEqual((row['tax_minor'], row['ni_minor']), (11620, 3486))
        self.receipt.amount_minor = 80000
        self.receipt.income.tax_treatment = 'cis'
        self.receipt.income.tax_deducted_minor = 20000
        self.session.commit()
        row = self.notice_rows()[0]
        self.assertEqual((row['tax_minor'], row['ni_minor']), (0, 0))
        self.assertEqual(received_split(current_plan()), (0, 0))

    def test_employment_forecast_sets_business_bracket(self):
        self.session.add(IncomeStream(id=str(uuid4()), name='Payroll job', kind='employed',
            expected_gross_minor=4000000, expected_gross_period='yearly', expected_gross_currency='GBP'))
        self.session.commit()
        row = self.notice_rows()[0]
        self.assertGreater(row['tax_minor'], 30000)
        self.assertEqual(row['ni_minor'], 3486)

    def test_unknown_unconfirmed_or_changed_income_never_claims_zero_tax(self):
        for field, value in (('confirmed_at', None),):
            setattr(self.receipt, field, value)
        self.session.commit()
        row = self.notice_rows()[0]
        self.assertIsNone(row['tax_minor'])
        self.assertIn('needs review', row['message'])
        self.receipt.confirmed_at = datetime.now(timezone.utc)
        self.receipt.income.needs_review = True
        self.session.commit()
        self.assertIsNone(self.notice_rows()[0]['ni_minor'])

    def test_notice_acknowledgement_reopens_for_confirmation_or_correction(self):
        self.receipt.confirmed_at = None
        self.session.commit()
        old = self.notice_rows()[0]
        self.assertEqual(self.acknowledge(old).status_code, 204)
        self.assertEqual(self.notice_rows(), [])
        self.receipt.confirmed_at = datetime.now(timezone.utc)
        self.session.commit()
        fresh = self.notice_rows()[0]
        self.assertNotEqual(old['version'], fresh['version'])
        self.assertEqual(self.acknowledge(old).status_code, 409)
        self.assertEqual(self.acknowledge(fresh).status_code, 204)
        self.receipt.reference = 'Corrected payment reference'
        self.session.commit()
        self.assertEqual(len(self.notice_rows()), 1)

    def test_pending_transfers_do_not_notify_and_unassigned_income_needs_review(self):
        self.receipt.status = 'PENDING'
        self.session.commit()
        self.assertEqual(self.notice_rows(), [])
        self.receipt.status = 'SETTLED'
        self.receipt.source = 'INTERNAL_TRANSFER'
        self.session.commit()
        self.assertEqual(self.notice_rows(), [])
        self.receipt.source = 'FASTER_PAYMENTS_IN'
        self.receipt.income.income_stream_id = None
        self.session.commit()
        row = self.notice_rows()[0]
        self.assertIsNone(row['tax_minor'])
        self.assertIn('Assign this incoming payment', row['message'])
        self.receipt.income = None
        self.session.commit()
        self.assertEqual(self.acknowledge().status_code, 204)
        self.assertEqual(self.notice_rows(), [])

    def test_notification_endpoint_private_no_raw_data_and_requires_csrf(self):
        response = self.client.get('/dashboard/income-notifications')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['count'], 1)
        self.assertNotIn('raw_payload', response.get_data(as_text=True))
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(self.acknowledge(csrf_token='bad').status_code, 400)
        self.assertEqual(self.acknowledge(payment=':'.join((self.account,self.category,self.outgoing))).status_code, 400)

    def test_earmarks_persist_and_stale_forms_cannot_overwrite(self):
        self.assertEqual(self.earmark().status_code, 303)
        row = self.session.get(AccountReserve, self.account)
        self.assertEqual((row.tax_minor, row.ni_minor, row.revision), (10000, 2000, 1))
        self.assertEqual(self.earmark(tax='0').status_code, 409)
        self.assertEqual(self.earmark(version='1', tax='50').status_code, 303)
        self.assertEqual((row.tax_minor, row.ni_minor, row.revision), (5000, 2000, 2))

    def test_earmark_validation_and_csrf(self):
        for values in (dict(tax='-1'), dict(ni='no'), dict(tax='92233720368547758.07',ni='1'),
                       dict(tax=''), dict(account=str(uuid4())), dict(csrf_token='bad')):
            self.assertEqual(self.earmark(**values).status_code, 400)
        data = MultiDict([('csrf_token', self.csrf()), ('tax', '1'), ('tax', '2')])
        self.assertEqual(self.client.post('/dashboard/account-reserves', data=data).status_code, 400)
        self.assertIsNone(self.session.get(AccountReserve, self.account))

    def test_live_balance_breakdown_underfunding_and_unavailable(self):
        self.assertEqual(self.earmark().status_code, 303)
        with patch('routes.dashboard.starling_request', return_value={'effectiveBalance': {'minorUnits': 9000, 'currency': 'GBP'}}):
            response = self.client.get('/dashboard/balances?account='+self.account+'&reserves=1')
        card = response.get_json()['balances'][0]
        self.assertEqual(card['amount'], 'GBP 90.00')
        self.assertEqual(card['tax_reserved'], 'GBP 100.00')
        self.assertEqual(card['ni_reserved'], 'GBP 20.00')
        self.assertEqual(card['unassigned'], '−GBP 30.00')
        self.assertTrue(card['underfunded'])
        from services.banking.client import StarlingError
        with patch('routes.dashboard.starling_request', side_effect=StarlingError('Unavailable')):
            card = self.client.get('/dashboard/balances?account='+self.account+'&reserves=1').get_json()['balances'][0]
        self.assertIsNone(card['unassigned'])
        self.assertEqual(card['tax_reserved'], 'GBP 100.00')
        self.assertEqual(self.client.get('/dashboard/balances?reserves=1', headers=self.headers).status_code, 403)

    def test_cumulative_notice_changes_match_received_target_across_streams(self):
        other = IncomeStream(id=str(uuid4()), name='Second business', kind='cis',
            expected_gross_minor=2000000, expected_gross_period='yearly', expected_gross_currency='GBP')
        self.session.add(other)
        self.session.flush()
        payment = Transaction(account_uid=self.account, category_uid=self.category, feed_item_uid=str(uuid4()),
            amount_minor=80000, currency='GBP', direction='IN', status='SETTLED', source='FASTER_PAYMENTS_IN',
            transaction_time=datetime(2026,4,13,12,tzinfo=timezone.utc), source_updated_at=self.now,
            confirmed_at=datetime.now(timezone.utc), raw_payload={})
        payment.income = TransactionIncome(income_stream_id=other.id, income_type='other', gross_minor=100000,
            tax_treatment='cis', tax_deducted_minor=20000, adjustment_minor=0, recorded_currency='GBP', needs_review=False)
        self.session.add(payment)
        self.session.commit()
        rows = self.notice_rows()
        target = received_split(current_plan())
        self.assertEqual((sum(row['tax_minor'] for row in rows), sum(row['ni_minor'] for row in rows)), target)
        # Acknowledged payments still contribute to the cumulative calculation.
        self.assertEqual(self.acknowledge(rows[-1]).status_code, 204)
        self.assertEqual(self.notice_rows()[0]['tax_minor'], rows[0]['tax_minor'])

    def test_old_future_and_foreign_receipts_cannot_get_current_year_advice(self):
        self.receipt.transaction_time = datetime(2025,4,10,tzinfo=timezone.utc)
        self.session.commit()
        self.assertIsNone(self.notice_rows()[0]['tax_minor'])
        self.receipt.transaction_time = datetime(2099,4,10,tzinfo=timezone.utc)
        self.session.commit()
        self.assertEqual(self.notice_rows(), [])
        self.receipt.transaction_time = datetime(2026,4,12,tzinfo=timezone.utc)
        self.receipt.currency = 'EUR'
        self.receipt.income.recorded_currency = 'EUR'
        self.session.commit()
        self.assertIsNone(self.notice_rows()[0]['ni_minor'])

    def test_new_expense_form_defaults_to_tracking_and_filters_other_stream_costs(self):
        self.assertIn('value="tracking" selected', self.html())
        self.assertEqual(self.expense(tax_status='tracking').status_code, 303)
        other = IncomeStream(id=str(uuid4()), name='Other business', kind='self_employed')
        self.session.add(other)
        self.session.commit()
        html = self.client.get('/dashboard/income-streams?view=deductions&stream='+other.id).get_data(as_text=True)
        self.assertNotIn('Business tools', html)

    def test_confirmed_non_taxable_notice_does_not_demand_stream_assignment(self):
        self.receipt.income.tax_treatment = 'non_taxable'
        self.receipt.income.income_stream_id = None
        self.receipt.income.gross_minor = None
        self.session.commit()
        row = self.notice_rows()[0]
        self.assertEqual((row['tax_minor'], row['ni_minor']), (0, 0))
        self.assertNotIn('Assign', row['message'])
        self.assertIn('non-taxable', row['message'])
        self.assertNotIn('needs review', row['message'])
        self.receipt.confirmed_at = None
        self.session.commit()
        row = self.notice_rows()[0]
        self.assertNotIn('Assign', row['message'])
        self.assertIn('confirm', row['message'])
        self.assertIsNone(row['tax_minor'])
