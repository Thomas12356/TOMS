"""Private evidence validation, retention, export and sample isolation."""
import io
import json
import re
import os
import unittest
import hashlib
import subprocess
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile
from PIL import Image
from pypdf import PdfWriter
from pypdf.actions import JavaScript
from sqlalchemy.exc import OperationalError
from werkzeug.datastructures import MultiDict
from models import EvidenceAsset, EvidenceDocument, ExpenseDeduction, MileageEntry
from services import evidence
from services.database.connection import db
from services.tax.export import package
from support import ApiTestCase
import deduction_support
import test_test_data as sample_support


def image_bytes():
    out = io.BytesIO()
    Image.new('RGB', (80, 100), 'white').save(out, format='PNG')
    return out.getvalue()


def pdf_bytes(*, encrypted=False, script=False, embedded=False):
    out = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt('synthetic-password')
    if script:
        writer.add_open_action(JavaScript('app.alert("synthetic")'))
    if embedded:
        writer.add_attachment('synthetic.txt', b'Synthetic embedded data')
    writer.write(out)
    return out.getvalue()


class EvidenceAuthTests(ApiTestCase):
    def test_owner_required_even_with_api_key(self):
        with patch('routes.evidence.storage.document') as lookup, patch('routes.evidence.storage.target') as target:
            for headers in ({}, self.headers):
                for path in ('/dashboard/evidence', '/dashboard/evidence/attach/transaction/key',
                    '/dashboard/evidence/'+str(uuid4())+'/download', '/dashboard/evidence/'+str(uuid4())+'/preview'):
                    self.assertEqual(self.client.get(path, headers=headers).status_code, 302)
            lookup.assert_not_called()
            target.assert_not_called()


class EvidenceTests(deduction_support.DeductionOwnerCase):
    def setUp(self):
        super().setUp()
        self.path = '/dashboard/evidence/attach/transaction/'+self.key

    def token(self, path=None):
        response = self.client.get(path or self.path)
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]

    def attach(self, data=None, name='receipt.png', path=None, **extra):
        response = self.client.post(path or self.path, data={'csrf_token': self.token(path),
            'file': (io.BytesIO(image_bytes() if data is None else data), name), 'notes': '=private test notes', **extra})
        # Werkzeug's synthetic large multipart body is a private temporary file;
        # the test client owns it, unlike a server-owned WSGI request stream.
        response.request.environ['wsgi.input'].close()
        return response

    def doc(self):
        return self.session.scalar(db.select(EvidenceDocument).where(EvidenceDocument.account_uid == self.account))

    def test_original_preview_dedup_and_no_financial_mutation(self):
        self.assertEqual(self.expense(tax_status='tracking').status_code, 303)
        before = (self.payment().amount_minor, self.payment().confirmed_at)
        data = image_bytes()
        self.assertEqual(self.attach(data, '../../fake.pdf').status_code, 303)
        doc = self.doc()
        self.assertEqual(doc.name, 'fake.png')
        self.assertEqual(self.client.get(f'/dashboard/evidence/{doc.id}/download').data, data)
        preview = self.client.get(f'/dashboard/evidence/{doc.id}/preview')
        self.assertEqual(preview.mimetype, 'image/jpeg')
        self.assertIn('no-store', preview.headers['Cache-Control'])
        self.assertIn('attachment;', self.client.get(f'/dashboard/evidence/{doc.id}/download').headers['Content-Disposition'])
        self.assertEqual(self.attach(data).status_code, 303)
        self.assertEqual(len(self.session.scalars(db.select(EvidenceDocument).where(EvidenceDocument.account_uid == self.account)).all()), 1)
        self.assertEqual((self.payment().amount_minor, self.payment().confirmed_at), before)
        expense = self.session.get(ExpenseDeduction, (self.account, self.category, self.outgoing))
        self.assertFalse(expense.is_tax_deductible)
        self.assertEqual(expense.amount_minor, 1000)

    def test_invalid_active_encrypted_and_overlarge_files_rejected(self):
        for data in (b'<html>private text</html>', b'<svg/>', b'%PDF-invalid', pdf_bytes(encrypted=True), pdf_bytes(script=True), pdf_bytes(embedded=True), b'a'*(evidence.MAX_FILE+1)):
            response = self.attach(data)
            self.assertIn(response.status_code, (400, 413))
            self.assertNotIn('private text', response.text)
        self.assertIsNone(self.doc())

    def test_valid_pdf_and_upload_above_default_request_limit(self):
        data = pdf_bytes() + b'\n%' + b' '*(1024*1024)
        self.assertEqual(self.attach(data, 'invoice.pdf').status_code, 303)
        doc = self.doc()
        self.assertEqual(doc.asset.pages, 1)
        self.assertEqual(self.client.get(f'/dashboard/evidence/{doc.id}/download').data, data)
        self.assertEqual(self.client.get(f'/dashboard/evidence/{doc.id}/preview').status_code, 404)

    def test_preview_corrects_orientation_and_strips_metadata_only_from_preview(self):
        original = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = 'Synthetic private image metadata'
        Image.new('RGB', (80, 100), 'white').save(original, format='JPEG', exif=exif)
        data = original.getvalue()
        self.assertEqual(self.attach(data, 'photo.jpg').status_code, 303)
        doc = self.doc()
        preview = self.client.get(f'/dashboard/evidence/{doc.id}/preview')
        with Image.open(io.BytesIO(preview.data)) as image:
            self.assertEqual(image.size, (100, 80))
            self.assertFalse(image.getexif())
        self.assertEqual(self.client.get(f'/dashboard/evidence/{doc.id}/download').data, data)

    def test_csrf_strict_fields_unknown_target_and_storage_cap(self):
        self.assertEqual(self.client.post(self.path, data={'file': (io.BytesIO(image_bytes()), 'receipt.png')}).status_code, 400)
        self.assertEqual(self.attach(unexpected='1').status_code, 400)
        data = MultiDict([('csrf_token', self.token()), ('notes', 'one'), ('notes', 'two'), ('file', (io.BytesIO(image_bytes()), 'receipt.png'))])
        self.assertEqual(self.client.post(self.path, data=data).status_code, 400)
        with patch.object(evidence, 'MAX_STORAGE', 1):
            self.assertEqual(self.attach().status_code, 400)
        self.assertIsNone(self.doc())
        self.assertEqual(self.client.get(self.path.replace(self.outgoing, self.incoming)).status_code, 404)
        self.assertEqual(self.client.get(self.path.replace(self.outgoing, str(uuid4()))).status_code, 404)
        self.assertEqual(self.client.get('/dashboard/evidence/attach/transaction/malformed').status_code, 404)
        self.assertEqual(self.client.get('/dashboard/evidence/attach/mileage/malformed').status_code, 404)

    def test_archive_restore_version_and_expense_unlink_retains_original(self):
        self.expense(tax_status='tracking')
        self.attach()
        doc = self.doc()
        path = f'/dashboard/evidence/{doc.id}/archive'
        self.assertEqual(self.client.post(path, data={'csrf_token': self.token(), 'version': '1', 'confirm': '1'}).status_code, 303)
        self.assertEqual(self.client.post(path, data={'csrf_token': self.token(), 'version': '1', 'confirm': '1'}).status_code, 409)
        self.assertFalse(doc.active)
        output, manifest = package(self.session, include_documents=True)
        with output:
            self.assertNotIn(doc.id, [row['id'] for row in manifest['records']['documents']])
        self.assertEqual(self.client.post(path, data={'csrf_token': self.token(), 'version': '2', 'confirm': '1'}).status_code, 303)
        self.session.delete(self.session.get(ExpenseDeduction, (self.account, self.category, self.outgoing)))
        self.session.commit()
        self.assertEqual(self.client.get(f'/dashboard/evidence/{doc.id}/download').data, image_bytes())

    def test_export_opt_in_originals_index_formula_protection_and_retention(self):
        self.attach()
        self.assertEqual(self.mileage().status_code, 303)
        journey = self.session.scalar(db.select(MileageEntry).where(MileageEntry.income_stream_id == self.stream))
        journey_path = '/dashboard/evidence/attach/mileage/'+journey.id
        self.assertEqual(self.attach(path=journey_path).status_code, 303)
        rows = self.session.scalars(db.select(EvidenceDocument).where(EvidenceDocument.asset_sha256 == self.doc().asset_sha256)).all()
        self.assertEqual(len(rows), 2)
        out, manifest = package(self.session)
        with out, ZipFile(out) as archive:
            self.assertFalse(any(name.startswith('documents/') for name in archive.namelist()))
            self.assertIn("'=private test notes", archive.read('documents.csv').decode('utf-8-sig'))
            self.assertNotIn('data', manifest['records']['documents'][0])
        out, manifest = package(self.session, include_documents=True)
        with out, ZipFile(out) as archive:
            files = [name for name in archive.namelist() if name.startswith('documents/')]
            self.assertEqual(len(files), 1)
            self.assertEqual(archive.read(files[0]), image_bytes())
            self.assertEqual(json.loads(archive.read('report.json'))['supporting_file_count'], 1)
            index = archive.read('supporting_documents.html').decode()
            self.assertIn('receipt.png', index)
            self.assertIn(files[0], index)
        self.session.delete(journey)
        self.session.commit()
        self.session.expire_all()
        self.assertIsNone(self.session.get(EvidenceDocument, rows[1].id).mileage_id if rows[1].kind == 'mileage' else self.session.get(EvidenceDocument, rows[0].id).mileage_id)
        response = self.client.get('/dashboard/evidence')
        self.assertIn('Journey removed', response.text)
        out, manifest = package(self.session, include_documents=True)
        with out:
            selected = [r for r in manifest['records']['documents'] if r['id'] in [d.id for d in rows]]
            self.assertEqual(len(selected), 1)

    def test_export_query_is_strict(self):
        for query in ('documents=0', 'documents=1&documents=1', 'other=1'):
            self.assertEqual(self.client.get('/dashboard/tax-export/download?'+query).status_code, 400)

    def test_worker_timeout_and_database_failure_leave_no_partial_upload(self):
        with patch('services.evidence.subprocess.run', side_effect=subprocess.TimeoutExpired('fixed-parser', 15)):
            self.assertEqual(self.attach().status_code, 400)
        original = evidence.upload
        token = self.token()
        def fail_after_validation(*args, **kwargs):
            original(*args, **kwargs)
            self.session.flush()
            raise OperationalError('private SQL', {}, Exception('private detail'))
        with patch('routes.evidence.storage.upload', side_effect=fail_after_validation):
            response = self.client.post(self.path, data={'csrf_token': token, 'file': (io.BytesIO(image_bytes()), 'receipt.png')})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private detail', response.text)
        self.assertIsNone(self.doc())
        self.assertIsNone(self.session.get(EvidenceAsset, hashlib.sha256(image_bytes()).hexdigest()))


@unittest.skipUnless(os.getenv('RUN_POSTGRES_TESTS') == '1', 'Requires PostgreSQL.')
class EvidenceIsolationTests(unittest.TestCase):
    def test_receipts_and_bytes_stay_in_selected_schema(self):
        fixture = sample_support.TestDataTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.toggle(True)
        key = ':'.join(sample_support.sample_id(n) for n in (1, 11, 35))
        path = '/dashboard/evidence/attach/transaction/'+key
        token = fixture.csrf(path=path)
        self.assertEqual(fixture.client.post(path, data={'csrf_token': token, 'file': (io.BytesIO(image_bytes()), 'sample.png')}).status_code, 303)
        self.assertEqual(fixture.live_value('SELECT count(*) FROM SCHEMA.evidence_assets'), 0)
        self.assertEqual(fixture.live_value('SELECT count(*) FROM SCHEMA.evidence_documents'), 0)
        html = fixture.client.get(path).text
        identity = re.search(r'/dashboard/evidence/([^/]+)/download', html)[1]
        fixture.toggle(False)
        self.assertEqual(fixture.client.get(f'/dashboard/evidence/{identity}/download').status_code, 404)
        fixture.toggle(True)
        self.assertEqual(fixture.client.get(f'/dashboard/evidence/{identity}/download').data, image_bytes())
        fixture.bank.assert_not_called()
