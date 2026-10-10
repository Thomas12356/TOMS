"""Maximum-sized client documents: wrapping, complete multi-page PDFs and private controls."""
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import app
from flask import render_template
from playwright.sync_api import sync_playwright
from pypdf import PdfReader
from routes.dashboard import format_amount

items = [SimpleNamespace(id=f'{index:03d}', description=f'Work item {index:03d} — Completed customer work and agreed payment',
                         amount_minor=10000, shift_snapshot={}) for index in range(100)]
items[0].description += ' ' + 'LongUnbrokenDescription'*40
invoice = SimpleNamespace(id='11111111-1111-4111-8111-111111111111', number='INV-999999', state='issued',
    business_name='Example business '+ 'N'*180, business_address='Address line\n'*100,
    business_contact='Contact '+ 'C'*450, client_name='Example client '+ 'X'*180,
    client_address='Client address\n'*100, reference='Reference '+ 'R'*180,
    payment_instructions='Payment destination '+ 'P'*1900, issue_date=date(2026,10,10), due_date=date(2026,11,10),
    currency='GBP', total_minor=1000000, items=items)
with app.test_request_context('/dashboard/invoices/' + invoice.id + '/print'):
    html = render_template('invoice_print.html', invoice=invoice, format_amount=format_amount, preview=False, sample=False)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    for width in (320,390,1280):
        page = browser.new_page(viewport={'width':width,'height':900})
        def respond(route):
            path = urlparse(route.request.url).path
            if path.startswith('/static/'):
                route.fulfill(path=str(ROOT/path.lstrip('/')))
            else:
                route.fulfill(body=html, content_type='text/html')
        page.route('http://toms.test/**', respond)
        page.goto('http://toms.test/invoice')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{width}px screen overflow'
        page.emulate_media(media='print')
        pdf = page.pdf(path=f'/tmp/toms-large-invoice-{width}.pdf', prefer_css_page_size=True)
        reader = PdfReader(BytesIO(pdf))
        texts = [sheet.extract_text() for sheet in reader.pages]
        combined = '\n'.join(texts)
        assert len(reader.pages) > 1
        for index in range(100):
            assert combined.count(f'Work item {index:03d}') == 1, f'Work item missing or repeated: {index}'
        total_count = combined.count('Total · GBP')
        assert total_count == 1, f'Grand total appears {total_count} times across {len(reader.pages)} pages'
        assert 'GBP 10,000.00' in combined
        assert 'Return to invoice' not in combined
        assert 'Print / save as PDF' not in combined
        assert 'Payment destination' in combined
        page.close()
        print(f'PASS {width}px: all 100 lines, long fields, {len(reader.pages)} PDF pages, single total and no controls')
    browser.close()
