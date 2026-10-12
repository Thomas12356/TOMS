"""Reminders only represent explicit additional-proof needs, not empty files."""
import io
import re
from uuid import uuid4
from models import EvidenceDocument, ExpenseDeduction, MileageEntry
from services.database.connection import db
from services.tax.export import package
from services.tax.reserves import current_plan
import deduction_support
import test_evidence


class EvidenceReviewTests(deduction_support.DeductionOwnerCase):
    def setUp(self):
        super().setUp()
        self.path = '/dashboard/evidence/attach/transaction/'+self.key

    def form(self, path=None):
        html = self.client.get(path or self.path).text
        box = html.split('class="evidence-request"')[1].split('</form>')[0]
        return {'csrf_token': re.search(r'name="csrf_token" value="([^"]+)"', box)[1],
                'version': re.search(r'name="version" value="([^"]+)"', box)[1]}

    def request_proof(self, reason='Itemised invoice needed to show the business purchase', path=None, **values):
        data = dict(self.form(path), reason=reason, **values)
        return self.client.post((path or self.path)+'/requirement', data=data)

    def attach(self):
        token = self.form()['csrf_token']
        return self.client.post(self.path, data={'csrf_token': token,
            'file': (io.BytesIO(test_evidence.image_bytes()), 'receipt.png')})

    def filtered(self, **options):
        return self.client.get('/dashboard/deductions', query_string={'history_evidence': 'missing', **options})

    def test_no_blanket_requirement_for_tax_expenses_mileage_or_tracking(self):
        self.assertEqual(self.expense().status_code, 303)
        self.assertEqual(self.mileage().status_code, 303)
        html = self.client.get('/dashboard/deductions').text
        self.assertIn('0 active supporting documents', html)
        self.assertNotIn('data-evidence-status', html)
        self.assertIn('0 expenses / 0 journeys', self.filtered().text)
        self.assertEqual(self.expense(tax_status='tracking').status_code, 303)
        self.assertEqual(self.request_proof().status_code, 400)
        self.assertIn('0 expenses / 0 journeys', self.filtered().text)

    def test_explicit_request_file_archive_restore_and_clear(self):
        self.expense()
        self.assertEqual(self.request_proof().status_code, 303)
        html = self.filtered().text
        self.assertIn('Missing evidence · Itemised invoice needed', html)
        self.assertIn('1 expenses / 0 journeys', html)
        self.assertEqual(self.attach().status_code, 303)
        self.assertIn('0 expenses / 0 journeys', self.filtered().text)
        html = self.client.get('/dashboard/deductions').text
        self.assertIn('1 active supporting document', html)
        self.assertNotIn('data-evidence-status', html)
        doc = self.session.scalar(db.select(EvidenceDocument).where(EvidenceDocument.account_uid == self.account))
        for version, expected in ((1, '1 expenses / 0 journeys'), (2, '0 expenses / 0 journeys')):
            response = self.client.post(f'/dashboard/evidence/{doc.id}/archive', data={
                'csrf_token': self.form()['csrf_token'], 'version': str(version), 'confirm': '1'})
            self.assertEqual(response.status_code, 303)
            self.assertIn(expected, self.filtered().text)
        self.assertEqual(self.request_proof('').status_code, 303)
        expense = self.session.get(ExpenseDeduction, (self.account, self.category, self.outgoing))
        self.assertIsNone(expense.evidence_needed_reason)

    def test_request_stale_csrf_strict_fields_and_payment_scope(self):
        self.expense()
        old = self.form()
        self.assertEqual(self.request_proof().status_code, 303)
        self.assertEqual(self.client.post(self.path+'/requirement', data=dict(old, reason='Changed reason')).status_code, 409)
        self.assertEqual(self.client.post(self.path+'/requirement', data={'reason': 'Missing invoice'}).status_code, 400)
        self.assertEqual(self.request_proof(reason='x'*1001).status_code, 400)
        self.assertEqual(self.request_proof(unexpected='1').status_code, 400)
        unknown = self.path.replace(self.outgoing, str(uuid4()))
        self.assertEqual(self.client.post(unknown+'/requirement', data=dict(self.form(), reason='Need invoice')).status_code, 404)
        for value in ('unattached', 'all', '0'):
            self.assertEqual(self.filtered(history_evidence=value).status_code, 400)
        self.assertEqual(self.client.get('/dashboard/deductions?history_evidence=missing&history_evidence=missing').status_code, 400)
        api = self.client.get('/dashboard/deductions?history_evidence=missing', headers=self.headers)
        self.assertEqual(api.status_code, 302)

    def test_mileage_explicit_need_preserves_relief_and_export_metadata(self):
        self.expense()
        self.mileage()
        journey = self.session.scalar(db.select(MileageEntry).where(MileageEntry.income_stream_id == self.stream))
        path = '/dashboard/evidence/attach/mileage/'+journey.id
        before = current_plan(refresh_rules=False, session=self.session)
        self.assertEqual(self.request_proof('Customer appointment record requested', path=path).status_code, 303)
        self.assertIn('0 expenses / 1 journeys', self.filtered().text)
        after = current_plan(refresh_rules=False, session=self.session)
        for key in ('gross_minor', 'deductions_minor', 'total_liability_minor', 'reserve_minor'):
            self.assertEqual(after[key], before[key])
        output, manifest = package(self.session)
        with output:
            row = next(row for row in manifest['records']['mileage'] if row['id'] == journey.id)
            self.assertEqual(row['evidence_needed_reason'], 'Customer appointment record requested')
        self.assertEqual(self.request_proof('', path=path).status_code, 303)
        self.assertIn('0 expenses / 0 journeys', self.filtered().text)

    def test_filter_keeps_sources_dates_pagination_and_annual_totals(self):
        self.expense()
        self.request_proof()
        html = self.filtered(history_source=self.stream, history_from='2026-04-10', history_to='2026-04-10').text
        self.assertIn('1 expenses / 0 journeys', html)
        self.assertIn('0 expenses / 0 journeys', self.filtered(history_from='2026-04-11').text)
        page_two = self.filtered(history_page='2').text
        self.assertIn('No expenses on this history page.', page_two)
        self.assertIn('history_evidence=missing', page_two)
        expense = self.session.get(ExpenseDeduction, (self.account, self.category, self.outgoing))
        expense.is_tax_deductible = False
        self.session.commit()
        self.assertIn('0 expenses / 0 journeys', self.filtered().text)
